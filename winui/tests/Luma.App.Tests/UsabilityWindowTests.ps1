param(
 [Parameter(Mandatory=$true)][string]$Executable,
 [Parameter(Mandatory=$true)][string]$OutputDirectory,
 [ValidateSet('Release','Model','Viewer')][string]$Scenario='Model',
 [ValidateSet(100,150)][int]$TextScale=100,
 [int]$Width=1000,[int]$Height=650
)
$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$output=[IO.Path]::GetFullPath($OutputDirectory)
$prefix=(Join-Path $workspace 'build')+[IO.Path]::DirectorySeparatorChar
if(-not $output.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $output)){throw 'Fresh output inside project build required'}
[IO.Directory]::CreateDirectory($output)|Out-Null
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class UsabilityNative{[DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int w,int z,uint f);[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int command);[DllImport("user32.dll")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);[DllImport("user32.dll")]public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);}'
$cases=@{
 Release=@('LUMA_RELEASE_UI_TEST','ui-release-report.json','passed_native_release_ui')
 Model=@('LUMA_MODEL_USABILITY_UI_TEST','model-usability-ui-report.json','passed_model_usability_ui')
 Viewer=@('LUMA_VIEWER_USABILITY_UI_TEST','viewer-usability-ui-report.json','passed_viewer_usability_ui')
}
$case=$cases[$Scenario];$data=Join-Path $output 'native-profile'
$start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable))
$start.UseShellExecute=$false;$start.WindowStyle='Hidden'
$start.Arguments='--data-dir "'+$data+'" --diagnostics --capture --text-scale '+$TextScale
foreach($key in @($start.Environment.Keys)){if($key -like 'LUMA_*_UI_TEST'){$start.Environment.Remove($key)|Out-Null}}
$start.Environment[$case[0]]='1'
$process=[Diagnostics.Process]::Start($start)
try{
 $reportPath=Join-Path $data $case[1];$deadline=[DateTimeOffset]::UtcNow.AddSeconds(100);$placed=$false;$handle=[IntPtr]::Zero
 while(-not(Test-Path -LiteralPath $reportPath)){
  if($process.HasExited){throw 'Own UI probe exited'}
  if([DateTimeOffset]::UtcNow -gt $deadline){throw 'Own UI probe timed out'}
  if(-not $placed){
   $process.Refresh();$handle=$process.MainWindowHandle
   if($handle -eq [IntPtr]::Zero){
    $statePath=Join-Path $data 'window-state.json';$reader=$null
    try{
     if(Test-Path -LiteralPath $statePath){$reader=[IO.StreamReader]::new([IO.File]::Open($statePath,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));$state=$reader.ReadToEnd()|ConvertFrom-Json;if($state.ready){$handle=[IntPtr][long]$state.hwnd}}
    }catch [IO.IOException]{}finally{if($reader){$reader.Dispose()}}
   }
   if($handle -ne [IntPtr]::Zero){[uint32]$owner=0;[UsabilityNative]::GetWindowThreadProcessId($handle,[ref]$owner)|Out-Null;if($owner -ne $process.Id){throw 'Window owner mismatch'};[UsabilityNative]::ShowWindow($handle,5)|Out-Null;$placed=[UsabilityNative]::SetWindowPos($handle,[IntPtr]::Zero,0,0,$Width,$Height,0x14)}
  }
  Start-Sleep -Milliseconds 100
 }
 $report=Get-Content -LiteralPath $reportPath -Raw|ConvertFrom-Json
 $report|ConvertTo-Json -Depth 7
 if(-not $placed -or $report.result -ne $case[2]){throw 'Own native usability verification failed'}
}finally{
 if(-not $process.HasExited){if(-not $process.CloseMainWindow() -and $handle -ne [IntPtr]::Zero){[uint32]$owner=0;[UsabilityNative]::GetWindowThreadProcessId($handle,[ref]$owner)|Out-Null;if($owner -eq $process.Id){[UsabilityNative]::PostMessage($handle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null}};if(-not $process.WaitForExit(20000)){throw 'Own process did not close safely'}}
}
