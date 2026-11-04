param([Parameter(Mandatory=$true)][string]$BundleDirectory,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$Width=1000,[int]$Height=650,[int]$TextScale=100)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class StorageNative{[DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int w,int z,uint f);}'
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Fresh test output required'}
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if(-not $output.StartsWith((Join-Path $workspace 'build')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Own build lab only'}
[IO.Directory]::CreateDirectory($output)|Out-Null
$app=Join-Path $output '便携程序';Copy-Item -LiteralPath ([IO.Path]::GetFullPath($BundleDirectory)) -Destination $app -Recurse
$exe=Join-Path $app 'Luma.WinUI.Preview.exe';$data=Join-Path $app 'Data';$process=$null;$checks=0
function Check([bool]$ok,[string]$name){if(-not $ok){throw ('STORAGE_UI_FAIL '+$name)};$script:checks++}
function Await([scriptblock]$test,[string]$name){$end=[DateTime]::UtcNow.AddSeconds(45);do{if(&$test){return};if($process.HasExited){throw ('EXIT '+$name)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $end);$root.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition) | ForEach-Object { if($_.Current.ProcessId -eq $process.Id -and $_.Current.AutomationId -like 'Storage*') {@{id=$_.Current.AutomationId;enabled=$_.Current.IsEnabled;offscreen=$_.Current.IsOffscreen;rect=$_.Current.BoundingRectangle.ToString()}} } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'timeout-controls.json');throw ('TIMEOUT '+$name)}
function Find([string]$id){return $script:root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
function Reach([string]$id){$candidate=Find $id;if($candidate -and $candidate.Current.IsEnabled){try{$candidate.SetFocus()}catch{}};Await {$e=Find $id;if(-not $e -or -not $e.Current.IsEnabled){return $false};$rect=$e.Current.BoundingRectangle;$parent=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($e);for($i=0;$i -lt 18 -and $parent;$i++){if($parent.Current.ProcessId -ne $process.Id){break};$sp=$null;if($parent.TryGetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern,[ref]$sp)){$vp=$parent.Current.BoundingRectangle;$vp.Intersect($root.Current.BoundingRectangle);if($rect.Height -ge 26 -and $rect.Top -ge $vp.Top-1 -and $rect.Bottom -le $vp.Bottom+1){}elseif($sp.Current.VerticallyScrollable){$direction=if($rect.Height -gt 0 -and $rect.Top -lt $vp.Top){[Windows.Automation.ScrollAmount]::SmallDecrement}else{[Windows.Automation.ScrollAmount]::SmallIncrement};if($e.Current.IsOffscreen -and $sp.Current.VerticalScrollPercent -ge 99){$sp.SetScrollPercent(-1,0)}else{$sp.Scroll([Windows.Automation.ScrollAmount]::NoAmount,$direction)};return $false}};$parent=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($parent)};$bounds=$root.Current.BoundingRectangle;return -not $e.Current.IsOffscreen -and $rect.Height -ge 26 -and $rect.Top -ge $bounds.Top -and $rect.Bottom -le $bounds.Bottom} ('reachable '+$id)}
function Click([string]$id){Reach $id;(Find $id).GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$script:checks++}
function Text([string]$id,[string]$value){Reach $id;(Find $id).GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($value)}
function Status([string]$pattern){Await {$e=Find 'StorageStatus';return $e -and $e.Current.Name -like $pattern} ('status '+$pattern);$e=Find 'StorageStatus';$r=$e.Current.BoundingRectangle;$w=$root.Current.BoundingRectangle;Check (-not $e.Current.IsOffscreen -and $r.Top -ge $w.Top -and $r.Bottom -le $w.Bottom) 'feedback always visible'}
function StartOwn([bool]$capture){$start=[Diagnostics.ProcessStartInfo]::new($exe);$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments=if($capture){'--capture --text-scale '+$TextScale}else{''};$script:process=[Diagnostics.Process]::Start($start);Await {$process.Refresh();return $process.MainWindowHandle -ne [IntPtr]::Zero} 'hwnd';$script:root=[Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle);[StorageNative]::SetWindowPos($process.MainWindowHandle,[IntPtr]::Zero,0,0,$Width,$Height,0x14)|Out-Null;Await {$null -ne (Find 'LibraryOpen')} 'welcome'}
function Capture([string]$name){Start-Sleep -Milliseconds 700;$file=Get-ChildItem -LiteralPath (Join-Path $data 'Profiles/default') -File -Filter 'capture-*-Storage-*.png'|Sort-Object LastWriteTimeUtc -Descending|Select-Object -First 1;Check ($null -ne $file) 'own XAML capture';Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $output ($name+'.png'))}
try{
 StartOwn $true
 Click 'LibraryOpen';Await {$null -ne (Find 'GalleryCount')} 'empty library'
 $source=Join-Path $output 'synthetic-images';[IO.Directory]::CreateDirectory($source)|Out-Null
 $image=Join-Path $source 'synthetic.png';[IO.File]::WriteAllBytes($image,[Convert]::FromBase64String('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRZkAAAAASUVORK5CYII='));$hash=(Get-FileHash -LiteralPath $image).Hash
 Click 'GalleryImportPathToggle';Text 'GalleryImportPath' $source;Click 'GalleryImportStart';Await {$null -ne (Find 'ManageList')} 'import task';Start-Sleep -Milliseconds 400;Click 'NavGallery';Click 'GalleryRefresh';Await {$e=Find 'GalleryCount';return $e -and $e.Current.Name -like '1 项*'} 'one own image'
 Click 'NavSettings';Reach 'ManagePreferenceCache';(Find 'ManagePreferenceCache').GetCurrentPattern([Windows.Automation.RangeValuePattern]::Pattern).SetValue(128);Click 'ManageSavePreferences';Await {$e=Find 'ManageSavePreferences';return $e -and $e.Current.IsEnabled} 'preference saved';Click 'NavGallery';Click 'NavSettings';Reach 'ManagePreferenceCache';Check ((Find 'ManagePreferenceCache').GetCurrentPattern([Windows.Automation.RangeValuePattern]::Pattern).Current.Value -eq 128) 'quota persisted through actual UI protocol'
 Click 'NavStorage';Capture 'storage-overview';Click 'StorageBackupPreview';Status '*先点击*安全断开*'
 Click 'StorageDisconnect';Status '*已安全断开*'
 Click 'StorageBackupPreview';Status '*备份预览*'
 Click 'StorageBackupCreate';Status '*备份已保存*'
 $backup=(Find 'StorageSnapshotPath').GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value
 Check ($backup.StartsWith((Join-Path $data 'Backups')+'\',[StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath (Join-Path $backup 'snapshot.json'))) 'backup confined to app Data'
 Capture 'storage-backup'
 Click 'StorageCachePreview';Status '*缓存预览*'
 Click 'StorageBackupPreview';Status '*备份预览*';Click 'StorageCacheApply';Start-Sleep -Milliseconds 150;Check ($null -eq (Find 'PrimaryButton')) 'switching preview invalidates old cache review';Status '*先预览缓存*';Click 'StorageCachePreview';Status '*缓存预览*'
 Click 'StorageCacheApply';Click 'PrimaryButton';Status '*已清理*'
 Check ((Get-FileHash -LiteralPath $image).Hash -eq $hash) 'original unchanged after cache cleanup'
 Text 'StorageSnapshotPath' $backup
 Click 'StorageRestore';Click 'CloseButton';Await {$e=Find 'StorageBackupPreview';return $e -and $e.Current.IsEnabled} 'cancel restore'
 Check (-not (Test-Path -LiteralPath (Join-Path $data 'active-profile.json'))) 'cancel does not activate'
 Click 'StorageRestore';Click 'PrimaryButton';Status '*新图库已核验*'
 Check (Test-Path -LiteralPath (Join-Path $data 'Profiles/default/winui-library-v1.json')) 'old profile preserved'
 $pointer=Get-Content -LiteralPath (Join-Path $data 'active-profile.json') -Raw|ConvertFrom-Json
 Check ($pointer.directory -match '^profile-[a-f0-9]{32}$') 'new profile activated atomically'
 Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe close after restore'
 StartOwn $false;Click 'LibraryOpen';Await {$e=Find 'GalleryCount';return $e -and $e.Current.Name -like '1 项*'} 'restored catalog opens with one image'
 Check ((Get-FileHash -LiteralPath $image).Hash -eq $hash) 'source unchanged after restored reopen'
 Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe reopened close'
 @{result='passed';checks=$checks;width=$Width;height=$Height;textScale=$TextScale;appLocalDefault=$true;restoreReopen=$true;originalBytesUnchanged=$true;captureScope='own XAML only, own generated image and isolated copied application'}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $output 'storage-window-report.json')
 Write-Output ('STORAGE_WINDOW_PASSED '+$checks)
}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null}}
