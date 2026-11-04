param([Parameter(Mandatory=$true)][string]$DeliveryDirectory,[Parameter(Mandatory=$true)][string]$OutputDirectory,[ValidateSet('Full','SecondLocation','LinkedPayload')][string]$Mode='Full')
$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$output=[IO.Path]::GetFullPath($OutputDirectory);$allowed=(Join-Path $workspace 'build')+[IO.Path]::DirectorySeparatorChar
if(-not $output.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $output)){throw 'Fresh project build child required'}
$walk=$output;while($walk){if(Test-Path -LiteralPath $walk){if((Get-Item -LiteralPath $walk -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'No reparse test parents'}};$walk=Split-Path -Parent $walk}
[IO.Directory]::CreateDirectory($output)|Out-Null
$delivery=[IO.Path]::GetFullPath($DeliveryDirectory);$manifest=Get-Content -LiteralPath (Join-Path $delivery 'delivery-manifest.json') -Raw|ConvertFrom-Json
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Luma.WinUI.D412B8DB'
$shortcuts=Join-Path ([Environment]::GetFolderPath('Programs')) 'Luma WinUI'
if((Test-Path -LiteralPath $key) -or (Test-Path -LiteralPath $shortcuts)){throw 'Existing user installation/shortcut must not be touched by smoke test'}
$setup=Join-Path $delivery $manifest.setup.name;$install=Join-Path $output '安装 中文 空格';$checks=0;$app=$null;$installed=$false
function Check([bool]$value,[string]$name){if(-not $value){throw ('INSTALLER_FAIL '+$name)};$script:checks++}
function Hash([string]$path){return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
function RunExe([string]$exe,[string]$arguments){$start=[Diagnostics.ProcessStartInfo]::new($exe);$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments=$arguments;$child=[Diagnostics.Process]::Start($start);$until=[DateTime]::UtcNow.AddSeconds(120);while(-not $child.HasExited){if([DateTime]::UtcNow -ge $until){throw 'Installer child timeout'};Start-Sleep -Milliseconds 100};return $child.ExitCode}
try{
 Check ((Hash $setup) -eq $manifest.setup.sha256) 'shipping installer hash'
 Check ((RunExe $setup ('/S /D='+$install)) -eq 0) 'shipping exe installs as current user'
 $installed=$true
 Check (Test-Path -LiteralPath $key) 'per-user uninstall registration'
 Check ((Get-ItemProperty -LiteralPath $key).InstallLocation -eq $install) 'registered isolated install path'
 foreach($entry in $manifest.fileHashes.PSObject.Properties){Check ((Hash (Join-Path $install $entry.Name)) -eq $entry.Value) 'installed file equals portable payload'}
 Check (Test-Path -LiteralPath (Join-Path $shortcuts 'Luma.lnk')) 'per-user application shortcut'
 if($Mode -ne 'LinkedPayload'){
  $second=Join-Path $output 'second-install';$secondExit=RunExe $setup ('/S /D='+$second)
  if($secondExit -eq 0){
   # Contain the negative regression to this test's two new installation paths.
   Check ((RunExe (Join-Path $second 'Uninstall.exe') ('/S _?='+$second)) -eq 0) 'remove unexpected second test install'
   New-Item -Path $key -Force|Out-Null;New-ItemProperty -LiteralPath $key -Name InstallLocation -Value $install -PropertyType String -Force|Out-Null
  }
  Check ($secondExit -ne 0) 'second location cannot strand registered installation'
  Check ((Get-ItemProperty -LiteralPath $key).InstallLocation -eq $install) 'original registration remains owned'
 }
 if($Mode -ne 'SecondLocation'){
  $linkedOriginal=Join-Path $output 'outside-link-canary.txt';[IO.File]::WriteAllText($linkedOriginal,'inert linked repair canary');$linkedHash=Hash $linkedOriginal
  $leaf=Join-Path $install 'README.txt';Remove-Item -LiteralPath $leaf;New-Item -ItemType HardLink -Path $leaf -Target $linkedOriginal|Out-Null
  Check ((RunExe $setup ('/S /D='+$install)) -ne 0) 'linked payload blocks repair'
  Check ((Hash $linkedOriginal) -eq $linkedHash) 'linked outside original unchanged'
  Remove-Item -LiteralPath $leaf
 }
 if($Mode -ne 'Full'){Write-Output ('INSTALLER_BOUNDARY_PASSED '+$Mode);exit 0}
 $canary=Join-Path $install 'keep-me-user-file.txt';[IO.File]::WriteAllText($canary,'owned unknown-file uninstall canary');$canaryHash=Hash $canary
 $original=Join-Path $output 'outside-original.png';Copy-Item -LiteralPath (Join-Path $install 'Assets/Stage2/alpine.png') -Destination $original;$originalHash=Hash $original
 $model=Join-Path $output 'outside-model.safetensors';[IO.File]::WriteAllText($model,'inert synthetic model canary');$modelHash=Hash $model
 # A loaded app must cause installation to stop, rather than force-killing it.
 $start=[Diagnostics.ProcessStartInfo]::new((Join-Path $install 'Luma.WinUI.Preview.exe'));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+(Join-Path $output 'running-profile')+'"';$app=[Diagnostics.Process]::Start($start)
 $until=[DateTime]::UtcNow.AddSeconds(30);do{$app.Refresh();if($app.MainWindowHandle -ne [IntPtr]::Zero){break};if($app.HasExited -or [DateTime]::UtcNow -ge $until){throw 'Installed native app startup failed'};Start-Sleep -Milliseconds 100}while($true)
 $before=Hash (Join-Path $install 'Luma.WinUI.Preview.exe')
 Check ((RunExe $setup ('/S /D='+$install)) -ne 0) 'running native app blocks repair'
 Check (-not $app.HasExited -and (Hash (Join-Path $install 'Luma.WinUI.Preview.exe')) -eq $before) 'no forced close or active exe overwrite'
 $app.CloseMainWindow()|Out-Null;Check ($app.WaitForExit(30000) -and $app.ExitCode -eq 0) 'graceful own app exit'
 & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'ManagementPersistenceTests.ps1') -Executable (Join-Path $install 'Luma.WinUI.Preview.exe') -OutputDirectory (Join-Path $output 'native-validation') 2>&1 | Out-File -LiteralPath (Join-Path $output 'native-validation.log')
 Check ($LASTEXITCODE -eq 0) 'installed app imports/reopens only own synthetic library'
 $profile=Join-Path $output 'native-validation/own-preview';$profileHashes=@{}
 foreach($file in Get-ChildItem -LiteralPath $profile -File -Recurse){$profileHashes[$file.FullName]=Hash $file.FullName}
 $uninstall=Join-Path $install 'Uninstall.exe'
 Check ((RunExe $uninstall ('/S _?='+$install)) -eq 0) 'shipping uninstaller succeeds'
 $installed=$false
 Check (-not (Test-Path -LiteralPath $key)) 'only own uninstall registration removed'
 Check (-not (Test-Path -LiteralPath (Join-Path $shortcuts 'Luma.lnk'))) 'own shortcut removed'
 Check (-not (Test-Path -LiteralPath (Join-Path $install 'Luma.WinUI.Preview.exe'))) 'known executable removed'
 Check ((Hash $canary) -eq $canaryHash) 'unknown file inside install directory retained'
 Check ((Hash $original) -eq $originalHash -and (Hash $model) -eq $modelHash) 'outside synthetic original/model retained'
 foreach($file in $profileHashes.Keys){Check ((Hash $file) -eq $profileHashes[$file]) 'test library and settings retained byte-exact'}
 # Reject nonempty unowned paths even in silent mode.
 $unowned=Join-Path $output 'unowned';[IO.Directory]::CreateDirectory($unowned)|Out-Null;$secret=Join-Path $unowned 'synthetic-photo.jpg';[IO.File]::WriteAllText($secret,'inert synthetic canary');$secretHash=Hash $secret
 Check ((RunExe $setup ('/S /D='+$unowned)) -ne 0) 'unowned existing directory rejected'
 Check ((Hash $secret) -eq $secretHash -and -not (Test-Path -LiteralPath (Join-Path $unowned 'luma-install.identity'))) 'unowned directory untouched'
 $target=Join-Path $output 'junction-target';[IO.Directory]::CreateDirectory($target)|Out-Null;[IO.File]::WriteAllText((Join-Path $target 'luma-install.identity'),"LUMA-WINUI-D412B8DB-INSTALL-v1`r`n")
 $link=Join-Path $output 'junction-install';New-Item -ItemType Junction -Path $link -Target $target|Out-Null
 try{Check ((RunExe $setup ('/S /D='+$link)) -ne 0) 'reparse install directory rejected';Check (-not (Test-Path -LiteralPath (Join-Path $target 'Luma.WinUI.Preview.exe'))) 'junction destination never receives payload'}finally{if((Get-Item -LiteralPath $link -Force).LinkType -ne 'Junction'){throw 'Own junction identity changed'};Remove-Item -LiteralPath $link -Force}
 @{result='passed';count=$checks;shippingInstallerSha256=$manifest.setup.sha256;installedFiles=@($manifest.fileHashes.PSObject.Properties).Count;userDataRetained=$true;unknownCanaryRetained=$true;registryCleaned=$true;realLibraryAccess=$false;networkUsed=$false;scope='same-user shipping EXE at isolated Unicode/space path; no clean-machine claim'}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath (Join-Path $output 'installer-smoke-report.json')
 Write-Output ('INSTALLER_SMOKE_PASSED '+$checks)
}finally{
 if($app -and -not $app.HasExited){$app.CloseMainWindow()|Out-Null;$app.WaitForExit(30000)|Out-Null}
 if($installed -and (Test-Path -LiteralPath (Join-Path $install 'Uninstall.exe'))){$exit=RunExe (Join-Path $install 'Uninstall.exe') ('/S _?='+$install);[IO.File]::WriteAllText((Join-Path $output 'cleanup-exit.txt'),$exit.ToString())}
}
