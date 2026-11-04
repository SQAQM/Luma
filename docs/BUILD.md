# 构建 Luma

Windows x64，Python 3.11.15、.NET SDK 10.0.403，以及 Visual Studio 的 VC143 x64 Redistributable。安装器使用 NSIS 3.13，下载地址和 SHA256 固定于 packaging/nsis-toolchain.json。

```powershell
New-Item -ItemType Directory -Force build/temp | Out-Null
$env:TEMP = "$pwd/build/temp"
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:DOTNET_CLI_HOME = "$pwd/build/dotnet-home"
$env:NUGET_PACKAGES = "$pwd/build/nuget-packages"
$env:NUGET_HTTP_CACHE_PATH = "$pwd/build/nuget-http"
$env:PYINSTALLER_CONFIG_DIR = "$pwd/build/pyinstaller-cache"
py -3.11 -m venv .venv-build
./.venv-build/Scripts/python.exe -m pip install -r requirements-engine-build.txt
./scripts/Build-Engine.ps1
./scripts/Build-WinUI.ps1
./scripts/Build-Delivery.ps1 -BundleDirectory release/Luma-native-1.0.0 -OutputDirectory release/Luma-1.0.0
```

Build-WinUI 的 CrtDirectory 与 CrtVersion 参数可指定本机 Microsoft.VC143.CRT 目录和文件版本；构建会核对签名、架构及版本。Build-Delivery 的 Compiler 参数可指定解压后的 makensis.exe。

```powershell
./.venv-build/Scripts/python.exe -m unittest backend.test_model_workflow scripts.test_delivery_packaging -v
Push-Location winui
dotnet run --project tests/Luma.Core.Tests/Luma.Core.Tests.csproj -c Release
Pop-Location
```

v1.x 标签、main 上的 VERSION 或发布工作流更新可触发原生发布，上传 EXE、ZIP、SHA256SUMS 和交付清单。已有版本标签时固定构建该标签的源码；也可在 Actions 手动运行。
