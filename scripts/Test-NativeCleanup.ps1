$ErrorActionPreference='Stop'
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$lab=Join-Path $workspace ('build/cleanup-lab-'+[Guid]::NewGuid().ToString('N'));[IO.Directory]::CreateDirectory($lab)|Out-Null
[IO.File]::WriteAllText((Join-Path $lab '.luma-cleanup-lab'),'synthetic-only')
function Bundle([string]$name,[bool]$extra=$false){$dir=Join-Path $lab ('release/'+$name);[IO.Directory]::CreateDirectory($dir)|Out-Null;[IO.File]::WriteAllText((Join-Path $dir 'Luma.WinUI.Preview.exe'),'inert synthetic executable');[IO.File]::WriteAllText((Join-Path $dir 'build-source-snapshot.json'),'{}');[IO.File]::WriteAllText((Join-Path $dir 'README.txt'),'own fixture');$payload=@{};foreach($file in Get-ChildItem -LiteralPath $dir -File){$payload[$file.Name]=@{sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant();bytes=$file.Length}};@{stage=8;ui='C#/XAML WinUI3';payload=$payload}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $dir 'build-info.json');if($extra){[IO.File]::WriteAllText((Join-Path $dir 'keep-original.jpg'),'inert unknown-user-file canary')};return $dir}
$valid=Bundle 'WinUI-preview-stage8.1';$unknown=Bundle 'WinUI-preview-stage8.2' $true;$changing=Bundle 'WinUI-preview-stage8.3';$rollback=Bundle 'WinUI-preview-stage8.16';$latest=Bundle 'WinUI-preview-stage8.99';$old=Join-Path $lab 'release/WinUI-preview-stage7.10';[IO.Directory]::CreateDirectory($old)|Out-Null
$preview=Join-Path $lab 'preview.json';$report=Join-Path $lab 'applied.json';$script=Join-Path $PSScriptRoot 'Clean-NativeArtifacts.ps1'
& pwsh -NoProfile -File $script -WorkspaceDirectory $lab -ReportPath $preview
if($LASTEXITCODE -ne 0){throw 'Preview failed'}
[IO.File]::WriteAllText((Join-Path $changing 'added-after-preview.txt'),'late synthetic canary')
& pwsh -NoProfile -File $script -WorkspaceDirectory $lab -ReportPath $report -Apply -PreviewPath $preview
if($LASTEXITCODE -ne 0){throw 'Apply failed'}
if(Test-Path -LiteralPath $valid){throw 'Verified intermediate not removed'}
foreach($path in @((Join-Path $unknown 'keep-original.jpg'),(Join-Path $changing 'added-after-preview.txt'),$rollback,$latest,$old)){if(-not (Test-Path -LiteralPath $path)){throw 'Protected canary/old/final target removed'}}
$r=Get-Content -LiteralPath $report -Raw|ConvertFrom-Json;if(@($r.results|Where-Object {$_.status -eq 'removed'}).Count -ne 1){throw 'Unexpected cleanup count'}
$linkedBundle=Bundle 'WinUI-preview-stage8.4';$preview2=Join-Path $lab 'preview2.json';$report2=Join-Path $lab 'collision-report.json'
& pwsh -NoProfile -File $script -WorkspaceDirectory $lab -ReportPath $preview2
if($LASTEXITCODE -ne 0){throw 'Second preview failed'}
$outside=Join-Path $lab 'outside-canary';[IO.Directory]::CreateDirectory($outside)|Out-Null
$junction=Join-Path $lab 'retained-manifests-collision-report';New-Item -ItemType Junction -Path $junction -Target $outside|Out-Null
try{
 & pwsh -NoProfile -File $script -WorkspaceDirectory $lab -ReportPath $report2 -Apply -PreviewPath $preview2 2>&1|Out-File -LiteralPath (Join-Path $lab 'junction-negative.log')
 if($LASTEXITCODE -eq 0){throw 'Retained-manifest junction was not rejected'}
 if(-not (Test-Path -LiteralPath $linkedBundle) -or (Get-ChildItem -LiteralPath $outside -Force|Select-Object -First 1)){throw 'Retained junction crossed boundary or cleanup started before validation'}
}finally{if((Get-Item -LiteralPath $junction -Force).LinkType -ne 'Junction'){throw 'Own junction identity changed'};Remove-Item -LiteralPath $junction -Force}
Write-Output ('CLEANUP_TEST_PASSED '+$lab)
