param(
    [string]$OutputDirectory='release/Luma-native-1.0.0',
    [string]$EngineDirectory='release/Engine-1.0.0/engine',
    [string]$CrtDirectory='C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT',
    [string]$CrtVersion='14.44.35211.0'
)
$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$target=[IO.Path]::GetFullPath((Join-Path $workspace $OutputDirectory))
$releaseRoot=(Join-Path $workspace 'release')+[IO.Path]::DirectorySeparatorChar
if (-not $target.StartsWith($releaseRoot,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $target) -notmatch '^(WinUI-preview|Luma-native)[-.]') { throw 'Output must be a dedicated native release directory.' }
if (Test-Path -LiteralPath $target) { throw 'Output already exists. Use a new preview directory; this script never removes or replaces files.' }
$engine=[IO.Path]::GetFullPath((Join-Path $workspace $EngineDirectory))
Push-Location -LiteralPath $workspace
try {
    & (Join-Path $workspace '.venv-build/Scripts/python.exe') -m worker.build_audit --directory $engine --verify
    if($LASTEXITCODE -ne 0){throw 'Engine source/payload audit failed'}
} finally {Pop-Location}
$crt=[IO.Path]::GetFullPath($CrtDirectory)
$crtNames=@('concrt140.dll','msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','msvcp140_atomic_wait.dll','msvcp140_codecvt_ids.dll','vccorlib140.dll','vcruntime140.dll','vcruntime140_1.dll','vcruntime140_threads.dll')
foreach ($name in $crtNames) {
    $file=Join-Path $crt $name
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw ('Missing redistributable CRT file: '+$name) }
    $signature=Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') { throw ('Invalid Microsoft CRT signature: '+$name) }
    $image=[IO.File]::ReadAllBytes($file)
    $pe=[BitConverter]::ToInt32($image,0x3c)
    if ([BitConverter]::ToUInt16($image,$pe+4) -ne 0x8664 -or (Get-Item -LiteralPath $file).VersionInfo.FileVersion -ne $crtVersion) { throw ('Expected pinned x64 CRT '+$crtVersion+': '+$name) }
}
$sourceSnapshot=& (Join-Path $workspace 'winui/Get-Stage1Inputs.ps1')
Push-Location -LiteralPath (Join-Path $workspace 'winui')
try {
    $sdk=(& dotnet --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $sdk -ne '10.0.403') { throw 'SDK10.0.403 is required by global.json.' }
    & dotnet restore 'src/Luma.App/Luma.App.csproj' --locked-mode -p:Platform=x64
    if ($LASTEXITCODE -ne 0) { throw 'Locked NuGet restore failed.' }
    & dotnet publish 'src/Luma.App/Luma.App.csproj' -c Release -p:Platform=x64 --no-restore -o $target
    if ($LASTEXITCODE -ne 0) { throw 'Publish failed; output is incomplete and must not be distributed.' }
} finally { Pop-Location }
foreach ($name in $crtNames) { Copy-Item -LiteralPath (Join-Path $crt $name) -Destination (Join-Path $target $name) -ErrorAction Stop }
Copy-Item -LiteralPath $engine -Destination (Join-Path $target 'engine') -Recurse -ErrorAction Stop
$licenseRoot=Join-Path $target 'ThirdPartyNotices'
[IO.Directory]::CreateDirectory($licenseRoot) | Out-Null
$lock=Get-Content -LiteralPath (Join-Path $workspace 'winui/src/Luma.App/packages.lock.json') -Raw | ConvertFrom-Json
foreach ($framework in $lock.dependencies.PSObject.Properties) {
    foreach ($dependency in $framework.Value.PSObject.Properties) {
        if($dependency.Value.type -eq 'Project'){continue}
        $name=$dependency.Name;$version=$dependency.Value.resolved
        $cacheRoot=if ($env:NUGET_PACKAGES) {$env:NUGET_PACKAGES} else {Join-Path $env:USERPROFILE '.nuget/packages'}
        $package=Join-Path $cacheRoot ($name.ToLowerInvariant()+'/'+$version.ToLowerInvariant())
        foreach ($file in (Get-ChildItem -LiteralPath $package -File | Where-Object {$_.Name -match 'license|notice|thirdparty|eula'})) {
            Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $licenseRoot ($name+'-'+$version+'-'+$file.Name))
        }
    }
}
@'
Luma WinUI native workspace preview. .NET and Windows App SDK are self-contained.
VC143 x64 CRT: Microsoft signed release redistributables, file version 14.44.35211.0.
UCRT and Windows system components remain OS prerequisites.
Microsoft redistribution documentation and terms:
https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170
https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/
https://visualstudio.microsoft.com/license-terms/vs2022-ga-diagnosticbuildtools/
Windows App SDK has a transitive WebView2 SDK dependency. This app creates no
WebView or browser; runtime module checks are separate from NuGet dependencies.
Luma includes a Qt-free local engine. Third-party license files are listed below.
'@ | Set-Content -LiteralPath (Join-Path $licenseRoot 'README.txt') -Encoding UTF8
$runtimeCache=Join-Path $cacheRoot 'microsoft.netcore.app.runtime.win-x64/10.0.14'
foreach ($notice in @('LICENSE.txt','THIRD-PARTY-NOTICES.txt')) {
    $source=Join-Path $runtimeCache $notice
    if (-not (Test-Path -LiteralPath $source)) {throw ('Missing .NET runtime notice: '+$notice)}
    Copy-Item -LiteralPath $source -Destination (Join-Path $licenseRoot ('dotnet-10.0.14-'+$notice))
}
Copy-Item -LiteralPath (Join-Path $workspace 'packaging/NSIS-COPYING.txt') -Destination (Join-Path $licenseRoot 'NSIS-COPYING.txt')
Copy-Item -LiteralPath (Join-Path $workspace 'winui/src/Luma.App/packages.lock.json') -Destination (Join-Path $target 'packages.lock.json')
$sourceSnapshot | Set-Content -LiteralPath (Join-Path $target 'build-source-snapshot.json') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $workspace 'docs/WINUI_1.0.0_PACKAGE.txt') -Destination (Join-Path $target 'README.txt')
& (Join-Path $PSScriptRoot 'Audit-WinUI.ps1') -BundleDirectory $target -CrtVersion $CrtVersion
Write-Output ('WINUI_PUBLISHED '+$target)
