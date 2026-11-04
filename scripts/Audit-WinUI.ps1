param([Parameter(Mandatory=$true)][string]$BundleDirectory,[string]$CrtVersion='14.44.35211.0')
$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$bundle=[IO.Path]::GetFullPath($BundleDirectory).TrimEnd('\')
$prefix=$bundle+'\'
$files=@(Get-ChildItem -LiteralPath $bundle -Recurse -File)
foreach ($file in $files) {
    $relative=$file.FullName.Substring($prefix.Length).Replace('\','/')
    if($relative.StartsWith('engine/')){continue} # Separate full archive/input audit below.
    # Unused SDK MapControl asset, not an application UI. Exact locked-SDK bytes only.
    if ($file.FullName.Substring($prefix.Length).Replace('\','/') -eq 'Microsoft.UI.Xaml/Assets/map.html' -and (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -eq '1C48985F80BF626FABE1B7E2BE7D7C53C271C5F75F5800EBA47587505D73F99D') {continue}
    if ($file.Extension -match '^\.(db|sqlite|sqlite3|safetensors|ckpt|py|pyc|html|htm|js)$' -or $file.Name -match '^Qt[56]|^python3|^PySide|^msedgewebview2\.exe$') { throw ('Unexpected payload: '+$file.Name) }
}
$snapshotPath=Join-Path $bundle 'build-source-snapshot.json'
Push-Location -LiteralPath $workspace
try {
    & (Join-Path $workspace '.venv-build/Scripts/python.exe') -m worker.build_audit --directory (Join-Path $bundle 'engine') --verify
    if($LASTEXITCODE -ne 0){throw 'Bundled engine audit failed'}
} finally {Pop-Location}
if (-not (Test-Path -LiteralPath $snapshotPath -PathType Leaf)) {throw 'Missing pre-build source snapshot; build provenance cannot be inferred after publishing.'}
$expected=Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json
function Confirm-SourceSnapshot {
    $actual=(& (Join-Path $workspace 'winui/Get-Stage1Inputs.ps1')) | ConvertFrom-Json
    if ($actual.PSObject.Properties.Name.Count -ne $expected.PSObject.Properties.Name.Count) {throw 'Source input list changed during/after build.'}
    foreach ($property in $expected.PSObject.Properties) {
        if ($actual.PSObject.Properties[$property.Name].Value -ne $property.Value) {throw ('Source drift; rebuild preview: '+$property.Name)}
    }
}
Confirm-SourceSnapshot
$sourceUi=Get-ChildItem -LiteralPath (Join-Path $workspace 'winui/src') -Recurse -File | Where-Object {$_.Extension -in @('.cs','.xaml') -and $_.FullName -notmatch '[\\/](bin|obj)[\\/]'}
if ($sourceUi | Select-String -Pattern 'WebView|HttpListener|(?-i:Process)\.Start|https?://' | Where-Object {$_.Line -notmatch 'xmlns(:x)?="http://schemas.microsoft.com/' -and -not ($_.Path -eq (Join-Path $workspace 'winui/src/Luma.Core/Engine/EngineProcess.cs') -and $_.Line.Trim() -eq 'var process = Process.Start(info) ?? throw new EngineRpcException("engine_failed");')}) {throw 'Review unexpected web/process UI entry point in source.'}
$payload=[ordered]@{}
foreach ($file in ($files | Sort-Object FullName)) {
    if ($file.Name -eq 'build-info.json') {continue}
    $payload[$file.FullName.Substring($prefix.Length).Replace('\','/')]=@{bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
}
foreach ($required in @('Luma.exe','App.xbf','MainWindow.xbf','Luma.pri','coreclr.dll','hostfxr.dll','Microsoft.UI.Xaml.dll','msvcp140.dll','vcruntime140.dll','engine/Luma.Engine.exe','engine/engine-build-info.json','Luma.Core.dll','ThirdPartyNotices/README.txt','ThirdPartyNotices/dotnet-10.0.14-LICENSE.txt','ThirdPartyNotices/dotnet-10.0.14-THIRD-PARTY-NOTICES.txt')) {
    if (-not ($payload.Keys | Where-Object {$_ -ieq $required})) {throw ('Missing self-contained runtime or notice: '+$required)}
}
$appSource=Join-Path $workspace 'winui/src/Luma.App'
if($payload.Keys | Where-Object {$_ -like 'Assets/Stage2/*' -or $_ -like 'engine/_internal/demo-art/*'}){throw 'Demonstration artwork must not be distributed.'}
foreach ($xaml in (Get-ChildItem -LiteralPath $appSource -Recurse -File -Filter '*.xaml' | Where-Object {$_.FullName -notmatch '[\\/](bin|obj)[\\/]'})) {
    $relative=$xaml.FullName.Substring($appSource.Length+1).Replace('\','/') -replace '\.xaml$','.xbf'
    if (-not ($payload.Keys | Where-Object {$_ -ieq $relative})) {throw ('Missing compiled XAML: '+$relative)}
}
[xml]$project=Get-Content -LiteralPath (Join-Path $appSource 'Luma.App.csproj') -Raw
$projectVersion=[string]$project.Project.PropertyGroup.Version
$fileVersion=[string]$project.Project.PropertyGroup.FileVersion
if ($projectVersion -match 'stage(?<stage>\d+)') {$stageNumber=[int]$Matches.stage}
else {$stageNumber=[int]$project.Project.PropertyGroup.LumaMigrationStage;if($stageNumber -ne 8){throw 'Explicit migration stage8 required for release candidates'}}
$exe=Get-Item -LiteralPath (Join-Path $bundle 'Luma.exe')
if ($exe.VersionInfo.FileVersion -ne $fileVersion) {throw 'Unexpected preview file version.'}
Confirm-SourceSnapshot
$info=[ordered]@{schemaVersion=2;stage=$stageNumber;version=$projectVersion;fileVersion=$exe.VersionInfo.FileVersion;ui='C#/XAML WinUI3';sdk='10.0.403';targetFramework='net10.0-windows10.0.26100.0';minimumOS='10.0.19041.0';rid='win-x64';dotnetSelfContained=$true;windowsAppSdkSelfContained=$true;crt=('app-local VC143 x64 '+$CrtVersion);engineIncluded=$true;catalogIncluded=$false;webFrontend=$false;unusedSdkMapAsset='Microsoft.UI.Xaml/Assets/map.html (exact hash whitelist; no MapControl/WebView instantiated)';cleanMachineTested=$false;sourceInputs=$expected;payload=$payload}
$info | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $bundle 'build-info.json') -Encoding UTF8
Write-Output ('WINUI_AUDIT stage'+$stageNumber+' '+$payload.Count+' payload files; '+$expected.PSObject.Properties.Name.Count+' source inputs')
