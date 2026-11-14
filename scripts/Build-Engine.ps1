param([string]$OutputDirectory='release/Engine-1.3.3')
$ErrorActionPreference='Stop'
$taskWorkspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskOutput=[IO.Path]::GetFullPath((Join-Path $taskWorkspace $OutputDirectory))
if(-not $taskOutput.StartsWith($taskWorkspace+'\release\',[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $taskOutput)){throw 'Engine output must be a fresh directory under release.'}
$taskPython=Join-Path $taskWorkspace '.venv-build/Scripts/python.exe'
$taskWork=Join-Path $taskWorkspace ('build/winui-stage8-release/engine-build-'+[Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($taskWork) | Out-Null
$taskSnapshot=Join-Path $taskWork 'source-inputs.json'
Push-Location -LiteralPath $taskWorkspace
try {
 & $taskPython -m worker.build_audit --snapshot $taskSnapshot
 if($LASTEXITCODE -ne 0){throw 'Engine source snapshot failed'}
 & $taskPython -m PyInstaller --noconfirm --distpath $taskOutput --workpath $taskWork 'scripts/luma-engine.spec'
 if($LASTEXITCODE -ne 0){throw 'Engine freeze failed'}
 & $taskPython -m worker.build_audit --snapshot $taskSnapshot --directory (Join-Path $taskOutput 'engine')
 if($LASTEXITCODE -ne 0){throw 'Engine native audit failed'}
 Write-Output ('ENGINE_PUBLISHED '+$taskOutput)
} finally {Pop-Location}
