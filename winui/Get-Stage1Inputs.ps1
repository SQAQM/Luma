$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$inputs=[ordered]@{}
Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File | Where-Object {$_.FullName -notmatch '[\\/](bin|obj)[\\/]'} | Sort-Object FullName | ForEach-Object {
    $relative=$_.FullName.Substring($workspace.Length+1).Replace('\','/')
    $inputs[$relative]=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
foreach ($relative in @('public/luma.ico','scripts/Build-WinUI.ps1','scripts/Audit-WinUI.ps1','scripts/verify_winui_compatibility.py','scripts/verify_local_storage.py','scripts/benchmark_catalog.py','docs/WINUI_1.0.0_PACKAGE.txt','scripts/package_native_delivery.py','scripts/test_delivery_packaging.py','scripts/Build-Delivery.ps1','scripts/Clean-NativeArtifacts.ps1','scripts/Test-NativeCleanup.ps1')) {
    $inputs[$relative]=(Get-FileHash -LiteralPath (Join-Path $workspace $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
}
Get-ChildItem -LiteralPath (Join-Path $workspace 'packaging') -File | Sort-Object Name | ForEach-Object {$relative=$_.FullName.Substring($workspace.Length+1).Replace('\','/');$inputs[$relative]=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
$engineInputs=(& (Join-Path $workspace '.venv-build/Scripts/python.exe') -c 'import json,sys;sys.path.insert(0,sys.argv[1]);from worker.build_audit import inputs;print(json.dumps(inputs()))' $workspace) | ConvertFrom-Json
if($LASTEXITCODE -ne 0){throw 'Engine source input enumeration failed'}
foreach($property in $engineInputs.PSObject.Properties){$inputs[$property.Name]=$property.Value}
$inputs | ConvertTo-Json -Depth 6 -Compress
