param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$TextScale=100,[int]$Width=1000,[int]$Height=650)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
. (Join-Path $PSScriptRoot 'MaterialAutomation.ps1')
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class MaterialNative{[DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int w,int z,uint f);}'
$output=[IO.Path]::GetFullPath($OutputDirectory);if(Test-Path -LiteralPath $output){throw 'Fresh output required'};[IO.Directory]::CreateDirectory($output)|Out-Null
$data=Join-Path $output 'isolated-data';$statePath=Join-Path $data 'window-state.json';$process=$null;$checks=0
function State{try{$reader=[IO.StreamReader]::new([IO.File]::Open($statePath,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));try{return $reader.ReadToEnd()|ConvertFrom-Json}finally{$reader.Dispose()}}catch [IO.IOException]{return $null}}
function Await([scriptblock]$test,[string]$name){$until=[DateTime]::UtcNow.AddSeconds(35);do{if(&$test){return};if($process.HasExited){throw ('EXIT '+$name)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $until);throw ('TIMEOUT '+$name)}
function Check([bool]$ok,[string]$name){if(-not $ok){throw ('MATERIAL_FAIL '+$name)};$script:checks++}
try{
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --diagnostics --capture --text-scale '+$TextScale
 $process=[Diagnostics.Process]::Start($start);Await {(State).ready} 'ready';$root=[Windows.Automation.AutomationElement]::FromHandle([IntPtr][long](State).hwnd)
 [MaterialNative]::SetWindowPos([IntPtr][long](State).hwnd,[IntPtr]::Zero,0,0,$Width,$Height,0x14)|Out-Null
 function Find([string]$id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
 function Click([string]$id){Await {$e=Find $id;$pattern=$null;if($e -and $e.Current.IsEnabled -and -not $e.Current.IsOffscreen -and $e.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){$script:invoke=$pattern;return $true};return $false} ('button '+$id);$script:invoke.Invoke();$script:checks++}
 function Capture([string]$name){Await {$s=State;return $s.ready -and $s.revision -eq $s.captureRevision} ('capture '+$name);$s=State;@{name=$name;capture=$s.captures[-1];material=$s.material}|ConvertTo-Json -Depth 5 -Compress|Add-Content -LiteralPath (Join-Path $output 'checkpoints.jsonl')}
 Click 'NavSettings';Await {(State).page -eq 'Settings'} 'settings'
 $s=State;Check ($s.material -is [pscustomobject] -and $null -ne $s.material.PSObject.Properties['requested']) 'explicit material policy state'
 Check (-not $s.engineStarted -and -not $s.catalogOpened) 'appearance does not open any catalog'
 Check ($s.material.requested -and $s.material.material -eq 'Acrylic') 'default acrylic preference'
 $sliderId='BackgroundTransparency';$rangePattern=[Windows.Automation.RangeValuePattern]::Pattern
 Await {$slider=Find $sliderId;return $null -ne $slider -and $slider.Current.IsEnabled} 'transparency slider'
 $range=(Find $sliderId).GetCurrentPattern($rangePattern)
 Check ($range.Current.Minimum -eq 0 -and $range.Current.Maximum -eq 100 -and -not $range.Current.IsReadOnly) 'writable transparency range from 0 to 100'
 Check ($range.Current.Value -eq 85 -and $s.material.backgroundTransparency -eq 85) 'default transparency'
 Capture 'default'
 Select-LumaBackgroundMaterial $root $process.Id 'Solid'
 Await {(State).material.material -eq 'Solid' -and (State).material.nativeLayers -eq 0} 'solid preference'
 Check ((State).material.reason -eq 'user_solid' -or (State).material.highContrast) 'solid reason'
 Check (-not (Find $sliderId).Current.IsEnabled) 'solid disables the transparency slider'
 Capture 'solid'
 Select-LumaBackgroundMaterial $root $process.Id 'Acrylic'
 Await {(State).material.material -eq 'Acrylic' -and (State).material.requested} 'acrylic preference'
 $s=State;$allowed=$s.material.supported -and $s.material.advancedEffects -and -not $s.material.highContrast
 $expectedLayers=if($allowed){1}else{0}
 Check ($s.material.acrylicRequested -eq $allowed) 'system constraints respected';Check ($s.material.nativeLayers -eq $expectedLayers) 'fixed native material layers';Capture 'acrylic'
 Click 'ThemeDark';Await {(State).actualTheme -eq 'Dark'} 'dark';Capture 'dark';Click 'ThemeLight';Await {(State).actualTheme -eq 'Light'} 'light';Capture 'light'
 (Find 'ReduceMotion').GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Toggle();Check ((State).material.requested) 'motion does not disable material'
 Select-LumaBackgroundMaterial $root $process.Id 'Frosted'
 Await {(State).material.material -eq 'Frosted'} 'frosted glass preference'
 Select-LumaBackgroundMaterial $root $process.Id 'Liquid'
 Await {(State).material.material -eq 'Liquid'} 'liquid glass preference'
 $transparency=37
 Await {
     try {
         $slider=Find $sliderId;$pattern=$null
         if($slider -and $slider.Current.IsEnabled -and $slider.TryGetCurrentPattern($rangePattern,[ref]$pattern)) {
             $pattern.SetValue($transparency);return $true
         }
     } catch [Windows.Automation.ElementNotAvailableException] {}
     return $false
 } 'change transparency in settings'
 Await {(State).material.backgroundTransparency -eq $transparency} 'transparency change reaches material state'
 Capture 'liquid-custom-transparency'
 Check ($process.CloseMainWindow() -and $process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe close'

 $preferences=Get-Content -LiteralPath (Join-Path $data 'winui-window-v1.json') -Raw|ConvertFrom-Json
 Check ($preferences.Material -eq 'Liquid') 'liquid glass preference persisted'
 Check ($preferences.BackgroundTransparency -eq $transparency) 'transparency preference persisted'
 Check (-not (Test-Path -LiteralPath (Join-Path $data 'settings.json'))) 'Qt preferences untouched'

 # Diagnostics require a fresh directory. The normal app can reopen our synthetic owned profile.
 $start.Arguments='--data-dir "'+$data+'" --text-scale '+$TextScale
 $process=[Diagnostics.Process]::Start($start)
 Await {$process.Refresh();return $process.MainWindowHandle -ne [IntPtr]::Zero} 'reopened synthetic profile'
 $root=[Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
 Check ($root.Current.ProcessId -eq $process.Id) 'reopened window belongs to the synthetic process'
 Click 'NavSettings';Await {$null -ne (Find $sliderId)} 'reopened appearance settings'
 Check ((Find $sliderId).GetCurrentPattern($rangePattern).Current.Value -eq $transparency) 'restored transparency visible in slider'
 $reopenedPicker=Find 'BackgroundMaterial'
 $selectionPattern=$reopenedPicker.GetCurrentPattern([Windows.Automation.SelectionPattern]::Pattern)
 $currentMaterial=$selectionPattern.Current.GetSelection()
 Check ($currentMaterial.Count -eq 1 -and $currentMaterial[0].Current.Name -eq '液态玻璃') 'material restored in the picker'
 Check ($process.CloseMainWindow() -and $process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe close after restart'
 @{result='passed';count=$checks;syntheticOnly=$true;privateDataAccess=$false;scope='own XAML only; does not prove compositor pixels';state=(State)}|ConvertTo-Json -Depth 15|Set-Content -LiteralPath (Join-Path $output 'material-window-report.json');Write-Output ('MATERIAL_WINDOW_PASSED '+$checks)
}catch{(State)|ConvertTo-Json -Depth 15|Set-Content -LiteralPath (Join-Path $output 'failure-state.json');($_|Format-List * -Force|Out-String)|Set-Content -LiteralPath (Join-Path $output 'exception.txt');throw}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null}}
