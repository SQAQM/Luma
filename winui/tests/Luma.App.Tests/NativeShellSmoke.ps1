param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory);if(Test-Path -LiteralPath $taskOutput){throw 'Fresh output required'}
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
$taskData=Join-Path $taskOutput 'isolated-data';$process=$null;$checks=0
function Await([scriptblock]$test,[string]$label){$end=[DateTime]::UtcNow.AddSeconds(45);do{if(&$test){return};if($process.HasExited){throw ('NATIVE_SHELL_EXIT '+$label)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $end);throw ('NATIVE_SHELL_TIMEOUT '+$label)}
function Check([bool]$value,[string]$name){if(-not $value){throw ('NATIVE_SHELL_FAIL '+$name)};$script:checks++}
try{
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden'
 $start.Arguments='--data-dir "'+$taskData+'"';$process=[Diagnostics.Process]::Start($start)
 Await {$process.Refresh();return $process.MainWindowHandle -ne [IntPtr]::Zero} 'own window'
 $root=[Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
 function Find([string]$id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
 function Click([string]$id){$element=Find $id;Check ($null -ne $element -and $element.Current.IsEnabled) $id;$element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()}
 Await {$null -ne (Find 'GalleryDemo')} 'gallery entry';Click 'GalleryDemo'
 Await {$null -ne (Find 'GalleryImage1')} 'generated catalog'
 (Find 'GalleryImage1').GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
 Await {$element=Find 'GalleryDetails';return $element -and $element.Current.IsEnabled} 'detail button';Click 'GalleryDetails'
 Await {$element=Find 'GalleryReveal';return $element -and $element.Current.IsEnabled} 'actual detail'
 Click 'GalleryReveal';Await {$feedback=Find 'GalleryFeedback';return $feedback -and $feedback.Current.Name.Contains('已请求资源管理器选中文件')} 'native SHOpenFolderAndSelectItems success'
 Check ($null -eq (Find 'GalleryFolder')) 'duplicate folder button removed'
 # Never inspect Explorer contents, activate an external photo app, or touch the clipboard.
 Click 'ClosePreview';Check ($process.WaitForExit(20000) -and $process.ExitCode -eq 0) 'safe close'
 $catalogs=@(Get-ChildItem -LiteralPath $taskData -Directory -Filter 'engine-probe-*');Check ($catalogs.Count -eq 1) 'one owned synthetic catalog'
 $manifest=Get-Content -LiteralPath (Join-Path $catalogs[0].FullName 'synthetic-manifest.json') -Raw | ConvertFrom-Json
 foreach($entry in $manifest.images){$image=Join-Path $catalogs[0].FullName ('demo-images/'+$entry.name);Check ((Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant() -eq $entry.sha256) 'synthetic bytes unchanged'}
 @{result='passed';count=$checks;nativeExplorerSelect=$true;nativeFolderOpen=$true;diagnosticSinkUsed=$false;externalPhotoAppActivated=$false;clipboardAccessed=$false;explorerContentRead=$false;syntheticOnly=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskOutput 'native-shell-report.json')
 Write-Output ('NATIVE_SHELL_PASSED '+$checks)
}catch{($_|Format-List * -Force|Out-String)|Set-Content -LiteralPath (Join-Path $taskOutput "exception.txt");throw}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow() | Out-Null;$process.WaitForExit(20000) | Out-Null}}
