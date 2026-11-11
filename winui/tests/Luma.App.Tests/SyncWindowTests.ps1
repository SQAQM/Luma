param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$TextScale=100)
$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$output=[IO.Path]::GetFullPath($OutputDirectory)
$build=(Join-Path $workspace 'build')+[IO.Path]::DirectorySeparatorChar
if(-not $output.StartsWith($build,[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $output)){throw 'Fresh output inside project build required'}
& (Join-Path $workspace '.venv-build/Scripts/python.exe') (Join-Path $PSScriptRoot 'create_sync_fixture.py') ($output+'-fixtures')
if($LASTEXITCODE -ne 0){throw 'Own fixture generation failed'}
$start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable))
$start.UseShellExecute=$false;$start.WindowStyle='Hidden'
$start.Arguments='--data-dir "'+$output+'" --diagnostics --capture --text-scale '+$TextScale
$start.Environment['LUMA_SYNC_UI_TEST']='1'
$start.Environment.Remove('LUMA_ENGINE_UI_TEST')|Out-Null
$start.Environment['LUMA_SYNC_RETRY_UI_TEST']='1'
$process=[Diagnostics.Process]::Start($start)
try{
 $reportPath=Join-Path $output 'sync-ui-report.json';$deadline=[DateTimeOffset]::UtcNow.AddSeconds(100)
 while(-not(Test-Path -LiteralPath $reportPath)){
  if($process.HasExited){throw 'Own UI probe exited'}
  if([DateTimeOffset]::UtcNow -gt $deadline){throw 'Own UI probe timed out'}
  Start-Sleep -Milliseconds 200
 }
 $report=Get-Content -LiteralPath $reportPath -Raw|ConvertFrom-Json
 $report|ConvertTo-Json -Depth 5
 if($report.result -ne 'passed_sync_ui'){throw 'Own native sync verification failed'}
}finally{
 if(-not $process.HasExited){$process.CloseMainWindow()|Out-Null;if(-not $process.WaitForExit(20000)){throw 'Own process did not close safely'}}
}
