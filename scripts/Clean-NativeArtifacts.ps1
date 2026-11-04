param([string]$WorkspaceDirectory,[Parameter(Mandatory=$true)][string]$ReportPath,[switch]$Apply,[string]$PreviewPath,[string[]]$KeepBundles=@(),[switch]$IncludeHistorical)
$ErrorActionPreference='Stop'
$project=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$root=if($WorkspaceDirectory){[IO.Path]::GetFullPath($WorkspaceDirectory)}else{$project}
if($root -ne $project){if(-not $root.StartsWith((Join-Path $project 'build')+'\',[StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath (Join-Path $root '.luma-cleanup-lab'))){throw 'Only project or marked own cleanup lab allowed'}}
function Contained([string]$path){$full=[IO.Path]::GetFullPath($path);if(-not $full.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Cleanup target escapes workspace'};return $full}
function NoReparse([string]$path){$walk=$path;while($walk){if(Test-Path -LiteralPath $walk){if((Get-Item -LiteralPath $walk -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse path rejected'}};$walk=Split-Path -Parent $walk}}
function FileMap([string]$path,$expected){
 NoReparse $path;$found=@{};$map=@{}
 foreach($item in Get-ChildItem -LiteralPath $path -Force -Recurse){if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse descendant rejected'};if(-not $item.PSIsContainer){$relative=$item.FullName.Substring($path.Length+1).Replace('\','/');if(-not $expected.ContainsKey($relative)){throw 'Unknown file present; preserve without opening it'};$found[$relative]=$item}}
 if($found.Count -ne $expected.Count){throw 'Missing payload files; preserve directory'}
 foreach($relative in $found.Keys){$item=$found[$relative];$map[$relative]=@{sha256=(Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant();bytes=$item.Length}}
 return $map
}
function SameMap($a,$b){if($a.Count -ne $b.Count){return $false};foreach($key in $a.Keys){if(-not $b.ContainsKey($key) -or $a[$key].sha256 -ne $b[$key].sha256 -or $a[$key].bytes -ne $b[$key].bytes){return $false}};return $true}
$report=Contained ([IO.Path]::GetFullPath($ReportPath));NoReparse $report;if(Test-Path -LiteralPath $report){throw 'Fresh report required'}
$release=Join-Path $root 'release';$all=@(Get-ChildItem -LiteralPath $release -Directory -ErrorAction SilentlyContinue|Where-Object {if($IncludeHistorical){$_.Name -match '^WinUI-preview-(stage[1-8](\.[0-9]+)?|photos\.[0-9]+)$'}else{$_.Name -match '^WinUI-preview-stage8\.(\d+)$'}})
$latest=$all|Where-Object {$_.Name -match '^WinUI-preview-stage8\.\d+$'}|Sort-Object {[int]($_.Name.Split('.')[-1])} -Descending|Select-Object -First 1
$protected=@('WinUI-preview-stage8.16')+$KeepBundles;if($latest){$protected+=@($latest.Name)}
if($IncludeHistorical){$protected+=@('WinUI-preview-photos.10','WinUI-preview-photos.11','WinUI-preview-photos.12','WinUI-preview-stage7.10')}
$running=@();foreach($name in @('Luma.WinUI.Preview','Luma.Engine','Luma.Core.Tests')){foreach($p in Get-Process -Name $name -ErrorAction SilentlyContinue){try{$running+=@($p.MainModule.FileName)}catch{$running+=@('*unknown*')}}}
$items=New-Object System.Collections.Generic.List[object];$skips=New-Object System.Collections.Generic.List[object]
foreach($directory in $all){
 if($directory.Name -in $protected){$skips.Add(@{name=$directory.Name;reason='protected final/rollback/newest'});continue}
 $path=Contained $directory.FullName
 try{
  if($running -contains '*unknown*' -or ($running|Where-Object {$_.StartsWith($path+'\',[StringComparison]::OrdinalIgnoreCase)})){throw 'Running own executable'}
  NoReparse $path
  $infoPath=Join-Path $path 'build-info.json';$info=Get-Content -LiteralPath $infoPath -Raw|ConvertFrom-Json
  if(($info.stage -ne 8 -and -not ($IncludeHistorical -and $info.stage -ge 1 -and $info.stage -le 8)) -or $info.ui -ne 'C#/XAML WinUI3'){throw 'Not owned native payload'}
  $expected=@{};foreach($entry in $info.payload.PSObject.Properties){$resolved=Contained (Join-Path $path $entry.Name);if(-not $resolved.StartsWith($path+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Manifest traversal'};$expected[$entry.Name]=@{sha256=$entry.Value.sha256;bytes=[long]$entry.Value.bytes}}
  $expected['build-info.json']=@{sha256=(Get-FileHash -LiteralPath $infoPath -Algorithm SHA256).Hash.ToLowerInvariant();bytes=(Get-Item -LiteralPath $infoPath).Length}
  $actual=FileMap $path $expected;if(-not (SameMap $expected $actual)){throw 'Extra or modified files; preserve entire directory'}
  $items.Add(@{name=$directory.Name;kind='nativeBundle';path=$path;files=$actual;logicalBytes=($actual.Values|Measure-Object -Property bytes -Sum).Sum})
 }catch{$skips.Add(@{name=$directory.Name;reason=$_.Exception.Message})}
}
if(-not $Apply){
 [IO.Directory]::CreateDirectory((Split-Path -Parent $report))|Out-Null
 @{schema=1;workspace=$root;protected=$protected;items=$items.ToArray();skipped=$skips.ToArray();scope='only manifest-owned native intermediate bundles; historical stages opt-in; unproven caches, image libraries and archived ZIPs preserved'}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $report
 Write-Output ('CLEANUP_PREVIEW '+$items.Count);exit
}
if(-not $PreviewPath){throw 'Persisted preview required'}
$previewFull=Contained ([IO.Path]::GetFullPath($PreviewPath));NoReparse $previewFull;$preview=Get-Content -LiteralPath $previewFull -Raw|ConvertFrom-Json
if($preview.schema -ne 1 -or $preview.workspace -ne $root){throw 'Preview identity differs'}
$results=New-Object System.Collections.Generic.List[object]
$retain=Contained (Join-Path (Split-Path -Parent $report) ('retained-manifests-'+[IO.Path]::GetFileNameWithoutExtension($report)))
NoReparse $retain;if(Test-Path -LiteralPath $retain){throw 'Fresh retained-manifest directory required'}
[IO.Directory]::CreateDirectory($retain)|Out-Null
foreach($old in $preview.items){
 $current=$items|Where-Object {$_.kind -eq $old.kind -and $_.name -eq $old.name}|Select-Object -First 1
 if(-not $current){$results.Add(@{name=$old.name;status='skipped';reason='no longer an eligible owned target'});continue}
 $oldMap=@{};foreach($property in $old.files.PSObject.Properties){$oldMap[$property.Name]=$property.Value}
 if(-not (SameMap $oldMap $current.files)){$results.Add(@{name=$old.name;status='skipped';reason='changed after preview'});continue}
 $target=Contained $current.path;NoReparse $target
 # Only explicit verified files are removed. A late unknown file keeps its folder.
 if($current.kind -eq 'nativeBundle'){
  $metadata=Contained (Join-Path $retain $current.name);NoReparse $metadata;if(Test-Path -LiteralPath $metadata){throw 'Fresh bundle metadata directory required'};[IO.Directory]::CreateDirectory($metadata)|Out-Null
  foreach($name in @('build-info.json','build-source-snapshot.json','README.txt')){$destination=Contained (Join-Path $metadata $name);NoReparse $destination;[IO.File]::Copy((Join-Path $target $name),$destination,$false)}
 }
 try{
  foreach($relative in $current.files.Keys){$file=Contained (Join-Path $target $relative);NoReparse $file;if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $current.files[$relative].sha256){throw 'File changed before removal'};Remove-Item -LiteralPath $file -Force}
  foreach($directory in @(Get-ChildItem -LiteralPath $target -Directory -Recurse -Force|Sort-Object {$_.FullName.Length} -Descending)){NoReparse $directory.FullName;if(-not (Get-ChildItem -LiteralPath $directory.FullName -Force|Select-Object -First 1)){Remove-Item -LiteralPath $directory.FullName -Force}}
  if(-not (Get-ChildItem -LiteralPath $target -Force|Select-Object -First 1)){Remove-Item -LiteralPath $target -Force;$status='removed'}else{$status='preserved-late-extra'}
  $results.Add(@{name=$current.name;status=$status;logicalBytes=$current.logicalBytes;files=$current.files.Count})
 }
 catch{$results.Add(@{name=$current.name;status='incomplete';reason=$_.Exception.Message})}
}
@{schema=1;workspace=$root;previewSha256=(Get-FileHash -LiteralPath $previewFull -Algorithm SHA256).Hash.ToLowerInvariant();protected=$protected;results=$results.ToArray();logicalBytesRemoved=($results|Where-Object {$_.status -eq 'removed'}|Measure-Object -Property logicalBytes -Sum).Sum;note='logical file size, not a promise of physical free-space gain; retained source manifests preserved; user libraries and historical1-7 packages untouched'}|ConvertTo-Json -Depth 7|Set-Content -LiteralPath $report
Write-Output ('CLEANUP_APPLIED '+@($results|Where-Object {$_.status -eq 'removed'}).Count)
