param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$TextScale=100,[switch]$ExpectEngineMissing,[switch]$ExpectIdleDisconnect)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class EngineWindowNative {
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
}
'@
$taskExe=[IO.Path]::GetFullPath($Executable);$taskOutput=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $taskOutput){throw 'Fresh output required'}
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
$taskData=Join-Path $taskOutput 'isolated-data';$taskState=Join-Path $taskData 'window-state.json'
$checks=New-Object 'System.Collections.Generic.List[object]';$process=$null
function Check([bool]$value,[string]$name){if(-not $value){throw ('ENGINE_UI_FAIL '+$name)};$checks.Add(@{name=$name;passed=$true})}
function State {
 if(Test-Path -LiteralPath $taskState){$reader=$null;try{$reader=[IO.StreamReader]::new([IO.File]::Open($taskState,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));return $reader.ReadToEnd() | ConvertFrom-Json}catch [IO.IOException] {}finally{if($reader){$reader.Dispose()}}};return $null
}
function Await([scriptblock]$predicate,[string]$name){$end=[DateTime]::UtcNow.AddSeconds(20);do{if(&$predicate){return};if($process.HasExited){throw ('ENGINE_UI_EXIT '+$process.ExitCode+' '+$name)};Start-Sleep -Milliseconds 40}while([DateTime]::UtcNow -lt $end);throw ('ENGINE_UI_TIMEOUT '+$name)}
try{
 $start=[Diagnostics.ProcessStartInfo]::new($taskExe);$start.UseShellExecute=$false;$start.WorkingDirectory=Split-Path -Parent $taskExe;$start.WindowStyle='Hidden'
 $start.Arguments='--data-dir "'+$taskData+'" --diagnostics --capture --text-scale '+$TextScale
 $start.EnvironmentVariables['PATH']=$env:SystemRoot+'\System32;'+$env:SystemRoot;$start.EnvironmentVariables['DOTNET_ROOT']=Join-Path $taskOutput 'absent-dotnet';$start.EnvironmentVariables['PYTHONPATH']=Join-Path $taskOutput 'absent-python'
 $process=[Diagnostics.Process]::Start($start)
 Await {$s=State;return $s.ready -and $s.captureRevision -eq $s.revision} 'ready'
 $hwnd=[IntPtr][long](State).hwnd;$root=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
 function Find([string]$id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
 function Click([string]$id){$element=Find $id;Check ($null -ne $element) ('exists '+$id);Check ($element.Current.IsEnabled -and -not $element.Current.IsOffscreen) ('reachable '+$id);$b=$element.Current.BoundingRectangle;$w=$root.Current.BoundingRectangle;Check ($b.Left -ge $w.Left -and $b.Top -ge $w.Top -and $b.Right -le $w.Right -and $b.Bottom -le $w.Bottom) ('bounds '+$id);$before=(State).revision;$element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();if($id -ne 'ClosePreview'){Await {$v=State;return $v.revision -gt $before -and $v.captureRevision -eq $v.revision} ('layout settled '+$id)}}
 Check (-not (State).engineStarted -and -not (State).catalogOpened) 'no automatic engine or catalog'
 Click 'NavJobs';Await {(State).page -eq 'Jobs' -and (State).captureRevision -eq (State).revision} 'jobs'
 Check ($null -ne (Find 'EngineConnect')) 'explicit engine connection exists'
 Click 'EngineConnect'
 if($ExpectIdleDisconnect){Click 'NavSettings';Click 'ThemeLight';Click 'NavJobs';Check (-not (State).engine.connected -and (State).engine.connecting) 'initial query completes before UI reports connected'}
 if($ExpectEngineMissing){
  Await {(State).engine.state -eq '连接失败' -and (State).captureRevision -eq (State).revision} 'missing engine feedback'
  Check ((Find 'EngineFeedback').Current.Name.Contains('缺少引擎')) 'actionable missing engine error'
  Check ((Find 'EngineConnect').Current.IsEnabled -and -not (State).engine.alive) 'retry enabled without live orphan'
  Click 'ClosePreview';Check ($process.WaitForExit(10000) -and $process.ExitCode -eq 0) 'missing engine closes safely'
  @{result='passed';count=$checks.Count;checks=$checks.ToArray()} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'engine-window-report.json') -Encoding UTF8
  Write-Output ('ENGINE_MISSING_PASSED '+$checks.Count);return
 }
 Await {(State).engine.connected -and (State).captureRevision -eq (State).revision} 'connected'
 $child=[Diagnostics.Process]::GetProcessById([int](State).engine.processId)
 Check ((State).engine.emptyQueryVerified -and -not (State).engine.userCatalogOpened) 'actual isolated empty catalog'
 if($ExpectIdleDisconnect){
  Await {(State).engine.state -eq '连接已断开' -and -not (State).engine.alive -and (State).captureRevision -eq (State).revision} 'idle disconnect UI update'
  Check ((Find 'EngineFeedback').Current.Name.Contains('中断')) 'visible idle disconnect feedback'
  Check ((Find 'EngineConnect').Current.IsEnabled) 'reconnect enabled automatically after idle disconnect'
  Click 'ClosePreview';Check ($process.WaitForExit(10000) -and $process.ExitCode -eq 0) 'faulted engine closes safely'
  @{result='passed';count=$checks.Count;checks=$checks.ToArray();state=(State)} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'engine-window-report.json') -Encoding UTF8
  Write-Output ('ENGINE_IDLE_PASSED '+$checks.Count);return
 }
 Check (($child.Modules | Where-Object {$_.ModuleName -match '^python3'}).Count -gt 0) 'frozen Python child loaded'
 $moduleProvenance=@($child.Modules | Where-Object {$_.ModuleName -match '^python3'} | ForEach-Object {@{name=$_.ModuleName;path=$_.FileName}})
 $enginePrefix=(Split-Path -Parent $child.MainModule.FileName)+'\'
 Check (-not ($moduleProvenance | Where-Object {-not $_.path.StartsWith($enginePrefix,[StringComparison]::OrdinalIgnoreCase)})) 'Python runtime from bundled engine only'
 $uiPrefix=(Split-Path -Parent $taskExe)+'\'
 Check (-not ($process.Modules | Where-Object {$_.ModuleName -in @('coreclr.dll','Microsoft.UI.Xaml.dll') -and -not $_.FileName.StartsWith($uiPrefix,[StringComparison]::OrdinalIgnoreCase)})) 'WinUI and CLR from own bundle'
 Check (-not ($child.Modules | Where-Object {$_.ModuleName -match 'Qt[56]|PySide|WebView|chrome'})) 'child has no UI renderer'
 Click 'EngineProbe';Await {(State).engine.requestActive} 'request active'
 Click 'NavModels';Await {(State).page -eq 'Models' -and (State).captureRevision -eq (State).revision} 'navigate during blocked RPC'
 Click 'NavSettings';Click 'ThemeDark';Click 'NavJobs';Await {(State).actualTheme -eq 'Dark' -and (State).captureRevision -eq (State).revision} 'theme during blocked RPC'
 Check ((State).engine.requestActive) 'request still blocked while UI responds'
 Click 'NavJobs';Await {$null -ne (Find 'EngineCancel')} 'cancel visible';Click 'EngineCancel'
 Await {-not (State).engine.requestActive -and (State).engine.connected -and (State).captureRevision -eq (State).revision} 'request cancelled'
 Check ((Find 'EngineFeedback').Current.Name.Contains('取消')) 'visible cancellation feedback'
 Click 'EngineDisconnect';Await {-not (State).engine.alive -and (State).captureRevision -eq (State).revision} 'safe disconnect'
 Check ($child.HasExited) 'child exited on disconnect'
 Click 'EngineConnect';Await {(State).engine.connected -and (State).captureRevision -eq (State).revision} 'reconnect'
 $lastChild=[Diagnostics.Process]::GetProcessById([int](State).engine.processId)
 Click 'EngineProbe';Await {(State).engine.requestActive} 'closing active request'
 [EngineWindowNative]::PostMessage($hwnd,0x0010,[IntPtr]::Zero,[IntPtr]::Zero) | Out-Null
 Check ($process.WaitForExit(20000) -and $process.ExitCode -eq 0) 'native close waits safely'
 Check ($lastChild.WaitForExit(10000)) 'no orphan child after native close'
 @{result='passed';count=$checks.Count;checks=$checks.ToArray();moduleProvenance=$moduleProvenance;state=(State)} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'engine-window-report.json') -Encoding UTF8
 Write-Output ('ENGINE_WINDOW_PASSED '+$checks.Count)
}finally{if($process -and -not $process.HasExited){$checks.ToArray() | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskOutput 'partial-checks.json');$process.CloseMainWindow() | Out-Null;$process.WaitForExit(20000) | Out-Null}}
