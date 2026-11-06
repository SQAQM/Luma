param(
 [Parameter(Mandatory=$true)][string]$BundleDirectory,
 [string]$OutputDirectory='release/Luma-1.2.1',
 [string]$Compiler='build/toolchain/nsis-3.13/nsis-3.13/makensis.exe'
)
$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $workspace
try{
 & (Join-Path $workspace '.venv-build/Scripts/python.exe') (Join-Path $PSScriptRoot 'package_native_delivery.py') --bundle $BundleDirectory --compiler $Compiler --output $OutputDirectory
 if($LASTEXITCODE -ne 0){throw 'Delivery packaging failed'}
}finally{Pop-Location}
