param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$TextScale=100,[int]$Width=1000,[int]$Height=650)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
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
 Click 'NavSettings'
 $s=State;Check ($s.material -is [pscustomobject] -and $null -ne $s.material.PSObject.Properties['requested']) 'explicit material policy state'
 Check (-not $s.engineStarted -and -not $s.catalogOpened) 'appearance does not open any catalog';Check $s.material.requested 'default material preference';Capture 'default'
 Click 'MaterialToggle';Await {-not (State).material.requested -and (State).material.nativeLayers -eq 0} 'solid preference';Check ((State).material.reason -eq 'user_solid' -or (State).material.highContrast) 'solid reason';Capture 'solid'
 Click 'MaterialToggle';Await {(State).material.requested} 'material preference'
 $s=State;$allowed=$s.material.supported -and $s.material.advancedEffects -and -not $s.material.highContrast
 $expectedLayers=if($allowed){1}else{0}
 Check ($s.material.acrylicRequested -eq $allowed) 'system constraints respected';Check ($s.material.nativeLayers -eq $expectedLayers) 'fixed native material layers';Capture 'acrylic'
 Click 'ThemeDark';Await {(State).actualTheme -eq 'Dark'} 'dark';Capture 'dark';Click 'ThemeLight';Await {(State).actualTheme -eq 'Light'} 'light';Capture 'light'
 (Find 'ReduceMotion').GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Toggle();Check ((State).material.requested) 'motion does not disable material'
 # Explicitly leave the preference off, then inspect only our private settings file.
 Click 'MaterialToggle';Await {-not (State).material.requested} 'save solid preference';Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe close'
 $preferences=Get-Content -LiteralPath (Join-Path $data 'winui-window-v1.json') -Raw|ConvertFrom-Json;Check (-not $preferences.UseMaterials) 'material preference persisted';Check (-not (Test-Path -LiteralPath (Join-Path $data 'settings.json'))) 'Qt preferences untouched'
 @{result='passed';count=$checks;syntheticOnly=$true;privateDataAccess=$false;scope='own XAML only; does not prove compositor pixels';state=(State)}|ConvertTo-Json -Depth 15|Set-Content -LiteralPath (Join-Path $output 'material-window-report.json');Write-Output ('MATERIAL_WINDOW_PASSED '+$checks)
}catch{(State)|ConvertTo-Json -Depth 15|Set-Content -LiteralPath (Join-Path $output 'failure-state.json');($_|Format-List * -Force|Out-String)|Set-Content -LiteralPath (Join-Path $output 'exception.txt');throw}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null}}
