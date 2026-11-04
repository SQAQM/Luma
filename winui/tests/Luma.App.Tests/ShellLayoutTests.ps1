param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[ValidateSet(100,125,150)][int]$TextScale=100,[ValidateSet('Side','Top')][string]$Layout='Side',[int]$Width=1000,[int]$Height=650,[switch]$ExpectArtworkFailure)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ShellNative {
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int w,int height,uint flags);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,System.Text.StringBuilder name,int max);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int command);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,IntPtr w,IntPtr l,uint flags,uint timeout,out IntPtr result);
}
'@
$taskExe=[IO.Path]::GetFullPath($Executable)
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $taskOutput) {throw 'Fresh test output required.'}
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
$taskData=Join-Path $taskOutput 'isolated-data'
$taskState=Join-Path $taskData 'window-state.json'
$checks=New-Object 'System.Collections.Generic.List[object]'
function Check([bool]$Condition,[string]$Name) {if(-not $Condition){throw ('SHELL_FAIL '+$Name)};$checks.Add(@{name=$Name;passed=$true})}
function State {
 if(Test-Path -LiteralPath $taskState){
  $reader=$null
  try{$stream=[IO.File]::Open($taskState,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete));$reader=New-Object IO.StreamReader($stream,[Text.Encoding]::UTF8);return $reader.ReadToEnd() | ConvertFrom-Json}catch{}finally{if($reader){$reader.Dispose()}}
 };return $null
}
function Await([scriptblock]$Predicate,[string]$Name){$end=[DateTime]::UtcNow.AddSeconds(15);do{if(&$Predicate){return};if($process -and $process.HasExited){throw ('SHELL_PROCESS_EXIT '+$process.ExitCode+' at '+$Name)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $end);throw ('SHELL_TIMEOUT '+$Name)}
$process=$null
try {
 $start=New-Object Diagnostics.ProcessStartInfo
 $start.FileName=$taskExe;$start.Arguments='--data-dir "'+$taskData+'" --diagnostics --capture --text-scale '+$TextScale
 if($Layout -eq 'Top'){$start.Arguments+=' --layout Top'}
 $start.UseShellExecute=$false;$start.WorkingDirectory=Split-Path -Parent $taskExe
 $start.EnvironmentVariables['PATH']=$env:SystemRoot+'\System32;'+$env:SystemRoot
 $start.EnvironmentVariables['DOTNET_ROOT']=Join-Path $taskOutput 'absent-dotnet';$start.EnvironmentVariables['DOTNET_ROOT_X64']=$start.EnvironmentVariables['DOTNET_ROOT']
 $process=[Diagnostics.Process]::Start($start)
 Await {$s=State;return($s.ready -and $s.captureRevision -eq $s.revision)} 'window ready'
 $s=State;Check ($s.processId -eq $process.Id) 'own process'
 $hwnd=[IntPtr][long]$s.hwnd
 [ShellNative]::SetWindowPos($hwnd,[IntPtr]::Zero,0,0,$Width,$Height,0x16) | Out-Null
 Await {$v=State;return($v.windowWidth -eq $Width -and $v.windowHeight -eq $Height -and $v.captureRevision -eq $v.revision)} 'requested window dimensions'
 Check ((State).windowWidth -eq $Width) 'actual width'
 $modules=@($process.Modules | ForEach-Object {$_.ModuleName})
 Check ($modules -contains 'Microsoft.UI.Xaml.dll' -and $modules -contains 'coreclr.dll') 'native WinUI and .NET loaded'
 Check (-not($modules | Where-Object {$_ -match 'Qt[56]|python3|WebView2|chrome'})) 'no web Qt or Python renderer'
 $root=[Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$s.hwnd)
 $class=New-Object Text.StringBuilder 256;[ShellNative]::GetClassName($hwnd,$class,256) | Out-Null;Check ($class.ToString() -like '*WinUI*') 'real WinUI HWND'
 function Find([string]$Id){$c=New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::AutomationIdProperty,$Id);return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,$c)}
 function Click([string]$Id){if([ShellNative]::IsIconic($hwnd)){[ShellNative]::ShowWindow($hwnd,4) | Out-Null;Await {-not [ShellNative]::IsIconic($hwnd)} 'restore own minimized window'};$c=Find $Id;if($c){$c.SetFocus();Await {$e=Find $Id;return $e -and -not $e.Current.IsOffscreen} ('focus visible '+$Id)};Check ($null -ne $c) ('exists '+$Id);Check ($c.Current.IsEnabled -and -not $c.Current.IsOffscreen) ('focus reachable '+$Id);$b=$c.Current.BoundingRectangle;$w=$root.Current.BoundingRectangle;Check ($b.Width -gt 0 -and $b.Left -ge $w.Left -and $b.Top -ge $w.Top -and $b.Right -le $w.Right -and $b.Bottom -le $w.Bottom) ('inside own window '+$Id);$beforeClick=(State).revision;$samePage=$Id.StartsWith('Nav') -and (State).page -eq $Id.Substring(3);$c.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();if($Id -ne 'ClosePreview'){Await {$v=State;return(($samePage -or $v.revision -gt $beforeClick) -and $v.captureRevision -eq $v.revision)} ('settled '+$Id)}}
 if($ExpectArtworkFailure){Check ((State).workspace.artworkFailureCount -gt 0) 'missing artwork detected';Check (-not(Find 'Feedback').Current.IsOffscreen -and (Find 'Feedback').Current.Name.Contains([string][char]0x65e0+[char]0x6cd5)) 'visible artwork error feedback';Click 'ClosePreview';Check ($process.WaitForExit(10000) -and $process.ExitCode -eq 0) 'damaged preview closes cleanly';@{result='passed';count=$checks.Count;checks=$checks.ToArray();state=(State)} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $taskOutput 'shell-report.json') -Encoding UTF8;Write-Output ('ARTWORK_FAILURE_PASSED '+$checks.Count);return}
 foreach($theme in @('Light','Dark','System')){Click 'NavSettings'
  Click ('Theme'+$theme);Await {(State).theme -eq $theme} ('theme '+$theme)
  if($theme -ne 'System'){Check ((State).actualTheme -eq $theme) ('actual theme '+$theme)}
  foreach($page in @('Gallery','Models','Tags','Organize','Duplicates','Jobs')){
   Click ('Nav'+$page);Await {(State).page -eq $page} ('page '+$page);Check ((State).page -eq $page) ('page selected '+$page)
   Check ((Find 'PageTitle').Current.Name -eq (Find ('Nav'+$page)).Current.Name) ('real heading matches route '+$page)
   Check (-not [string]::IsNullOrEmpty((Find ('Nav'+$page)).Current.ItemStatus)) ('persistent nav selected state '+$page)
   if($page -eq 'Gallery'){
    $gridBounds=(Find 'DemoTiles').Current.BoundingRectangle
    foreach($id in 1..6){$item=Find ('DemoImage'+$id);$chip=Find ('TagFilter'+$id);if($item -and $chip -and $item.Current.BoundingRectangle.Top -le $gridBounds.Top+10 -and -not $chip.Current.IsOffscreen){Check ($chip.Current.BoundingRectangle.Height -ge 28) ('first-row tag is not vertically clipped '+$id)}}
   }
   foreach($mode in @('Empty','Loading','Error')){Click ('State'+$mode);Check ((State).mode -eq $mode) ('preview state '+$page+$mode);Check (-not(Find 'StateAction').Current.IsOffscreen) ('state recovery visible '+$page+$mode);Click 'StateAction';Check ((State).mode -eq 'Sample') ('recovery works '+$page+$mode)}
   if($page -in @('Organize','Duplicates','Jobs')){Check (-not(Find 'EngineAction').Current.IsEnabled) ('real file action disabled '+$page)}
  }
 }
 $held=[IO.File]::Open($taskState,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete));try{Click 'NavModels';Click 'NavGallery';Check (-not $process.HasExited) 'diagnostic reader cannot crash real window'}finally{$held.Dispose()}
 Click 'NavGallery'
 Check ((State).workspace.artworkFailureCount -eq 0 -and (State).workspace.artworkCacheCount -eq 3) 'three bounded artwork sources loaded without failure'
 $search=Find 'Search';$search.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue('A');Await {(State).workspace.visibleCount -eq 2 -and (State).captureRevision -eq (State).revision} 'synthetic search';Check ((State).workspace.visibleCount -eq 2) 'actual filtered sample count'
 $sortControl=Find 'Sort';$sortControl.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand();$listCondition=New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::ListItem);Await {$sortControl.FindAll([Windows.Automation.TreeScope]::Descendants,$listCondition).Count -ge 2} 'native sort choices';$sortItems=$sortControl.FindAll([Windows.Automation.TreeScope]::Descendants,$listCondition);$sortItems.Item(1).GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select();Await {(State).workspace.sort -eq 1 -and (State).captureRevision -eq (State).revision} 'descending sort';Check ((State).workspace.sort -eq 1) 'real native sort selection'
 $tile=Find 'DemoImage1';$tile.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select();Await {(State).workspace.selectedId -eq 1} 'selection'
 Click 'NavModels';Click 'NavGallery';Check ((State).workspace.queryLength -eq 1 -and (State).workspace.selectedId -eq 1 -and (State).workspace.sort -eq 1) 'query sort selection retained across navigation'
 (Find 'Search').GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue('no-match');Await {(State).workspace.visibleCount -eq 0 -and (State).captureRevision -eq (State).revision} 'empty search';Check ($null -eq (State).workspace.selectedId) 'selection leaves filtered scope safely';Click 'StateAction'
 Click 'NavTags';$tag=Find 'TagDraft';$tag.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue('Custom');Click 'NavModels';Click 'NavTags';Check ((Find 'TagDraft').GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value -eq 'Custom') 'tag draft retained';Click 'AddTag';Check ((State).workspace.tagCount -eq 4) 'tag added in memory'
 Click 'NavSettings';$toggle=Find 'ReduceMotion';$toggle.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Toggle();Await {(State).workspace.reducedMotion} 'reduced motion';Click 'NavGallery';Check ((State).workspace.animationMilliseconds -eq 0) 'navigation respects reduced motion'
 $focus=Find 'Search';$focus.SetFocus();Await {$focus.Current.HasKeyboardFocus} 'native keyboard focus';Check ($focus.Current.HasKeyboardFocus) 'real search focus'
 $before=(State).revision;$messageResult=[IntPtr]::Zero;$sent=[ShellNative]::SendMessageTimeout($hwnd,0x001A,[IntPtr]::Zero,[IntPtr]::Zero,2,1000,[ref]$messageResult);Check ($sent -ne [IntPtr]::Zero) 'synchronous own settings message delivered';Await {(State).revision -gt $before -and (State).captureRevision -eq (State).revision} 'own window settings message';Check ((State).nativeSettingsHook) 'native preference fallback active'
 $expectedStage=(Get-Content -LiteralPath (Join-Path (Split-Path -Parent $taskExe) 'build-info.json') -Raw|ConvertFrom-Json).stage
 Check ((State).stage -eq $expectedStage) 'diagnostic stage matches packaged stage'
 Check (-not (State).engineStarted -and -not (State).catalogOpened) 'no backend or catalog'
 [ShellNative]::ShowWindow($hwnd,3) | Out-Null;Await {[ShellNative]::IsZoomed($hwnd)} 'native maximize';Check ([ShellNative]::IsZoomed($hwnd)) 'system maximize'
 [ShellNative]::ShowWindow($hwnd,9) | Out-Null;Await {-not [ShellNative]::IsZoomed($hwnd)} 'native restore';Check (-not [ShellNative]::IsZoomed($hwnd)) 'system restore'
 [ShellNative]::ShowWindow($hwnd,6) | Out-Null;Await {[ShellNative]::IsIconic($hwnd)} 'native minimize';Check ([ShellNative]::IsIconic($hwnd)) 'system minimize'
 [ShellNative]::ShowWindow($hwnd,9) | Out-Null;Await {-not [ShellNative]::IsIconic($hwnd) -and (State).captureRevision -eq (State).revision} 'native final restore';Check (-not [ShellNative]::IsIconic($hwnd)) 'restore minimized window'
 Click 'ClosePreview';Check ($process.WaitForExit(15000)) 'normal close';Check ($process.ExitCode -eq 0) 'clean exit'
 @{result='passed';count=$checks.Count;checks=$checks.ToArray();state=(State)} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $taskOutput 'shell-report.json') -Encoding UTF8
 Write-Output ('SHELL_PASSED '+$checks.Count)
}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow() | Out-Null;$process.WaitForExit(10000) | Out-Null}}
