param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$output=[IO.Path]::GetFullPath($OutputDirectory)
$project=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if(-not $output.StartsWith((Join-Path $project 'build')+'\',[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $output)){throw 'Fresh project-local test output required'}
$start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden'
$start.Arguments='--data-dir "'+$output+'" --diagnostics --capture'
$process=[Diagnostics.Process]::Start($start)
try{
 $file=Join-Path $output 'window-state.json';$deadline=[DateTime]::UtcNow.AddSeconds(30);$state=$null
 do{
  if($process.HasExited){throw 'Application exited during startup'}
  if(Test-Path -LiteralPath $file){try{$state=Get-Content -LiteralPath $file -Raw|ConvertFrom-Json}catch{}}
  if($state.ready -and $state.revision -eq $state.captureRevision){break}
  Start-Sleep -Milliseconds 100
 }while([DateTime]::UtcNow -lt $deadline)
 if(-not $state.ready -or $state.revision -ne $state.captureRevision){throw 'Startup did not produce a ready XAML frame'}
 if($state.workspace.gallery.itemCount -gt 0 -or $state.workspace.gallery.cacheCount -gt 0 -or $state.workspace.management.modelCount -gt 0){throw 'Release startup still renders synthetic demonstration records'}
 if($state.engine.connected -or $state.engine.userCatalogOpened){throw 'Startup unexpectedly opened a catalog'}
 @{result='passed';scope='one startup with fresh project-local diagnostics, own XAML only; no UI Automation and no real library';capture=$state.captures[-1]}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $output 'startup-report.json')
 Write-Output 'RELEASE_STARTUP_PASSED'
}finally{if(-not $process.HasExited){$process.CloseMainWindow()|Out-Null;if(-not $process.WaitForExit(20000)){throw 'Own test process did not close safely'}}}
