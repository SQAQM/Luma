param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$TextScale=100,[int]$Width=1000,[int]$Height=650,[switch]$ThumbnailOnly,[switch]$FavoriteOnly,[switch]$ViewerBackOnly)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class ManageNative{[DllImport("user32.dll")]public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);[DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int w,int z,uint f);}'
$output=[IO.Path]::GetFullPath($OutputDirectory);if(Test-Path -LiteralPath $output){throw 'Fresh output required'};[IO.Directory]::CreateDirectory($output)|Out-Null
$data=Join-Path $output 'isolated-data';$stateFile=Join-Path $data 'window-state.json';$checks=0;$process=$null
function State{for($attempt=0;$attempt -lt 10;$attempt++){try{$stream=[IO.FileStream]::new($stateFile,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete));$reader=[IO.StreamReader]::new($stream);try{$value=$reader.ReadToEnd()|ConvertFrom-Json}finally{$reader.Dispose()};$script:hasState=$true;return $value}catch{Start-Sleep -Milliseconds 10}};if($script:hasState){throw 'Own diagnostic state could not be read'};return $null}

function Await([scriptblock]$test,[string]$name){$end=[DateTime]::UtcNow.AddSeconds(40);do{$matched=$false;try{$matched=&$test}catch{$transient=$false;$cause=$_.Exception;for($depth=0;$depth -lt 8 -and $cause;$depth++){if($cause.GetType().FullName -eq 'System.Windows.Automation.ElementNotAvailableException'){$transient=$true;break};$cause=$cause.InnerException};if(-not $transient){throw}};if($matched){return};if($process.HasExited){throw ('EXIT '+$name)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $end);throw ('TIMEOUT '+$name)}

function Checkpoint([string]$name){Await {$s=State;return -not $s.workspace.management.loading -and -not $s.workspace.management.writing -and $s.revision -eq $s.captureRevision} ('capture '+$name);$s=State;@{name=$name;capture=$s.captures[-1];revision=$s.revision}|ConvertTo-Json -Compress|Add-Content -LiteralPath (Join-Path $output 'checkpoints.jsonl')}
function Check([bool]$value,[string]$name){if(-not $value){throw ('MANAGEMENT_FAIL '+$name)};$script:checks++}
function SelectFirstModel{
 Await {$list=Find 'ManageList';if(-not $list -or -not $list.Current.IsEnabled){return $false};$script:firstModel=$list.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::ListItem));return $null -ne $script:firstModel} 'first model card realized'
 $script:firstModel.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
 Await {$null -ne (Find 'ManageSelectedModel')} 'selected model inspector'
}
try{
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --diagnostics --capture --text-scale '+$TextScale
 $process=[Diagnostics.Process]::Start($start);Await {(State).ready -and (State).revision -eq (State).captureRevision} 'ready';$root=[Windows.Automation.AutomationElement]::FromHandle([IntPtr][long](State).hwnd);[ManageNative]::SetWindowPos([IntPtr][long](State).hwnd,[IntPtr]::Zero,0,0,$Width,$Height,0x14)|Out-Null
 function Find([string]$id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
 function Reach([string]$id){Await {$e=Find $id;if(-not $e -or -not $e.Current.IsEnabled){return $false};$rect=$e.Current.BoundingRectangle;$minimum=if($id.StartsWith("Nav") -and $id -ne "NavSettings"){48}else{0};$parent=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($e);$ancestors=@();for($level=0;$level -lt 16 -and $parent;$level++){if($parent.Current.ProcessId -ne $process.Id){break};$sp=$null;if($parent.TryGetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern,[ref]$sp)){$ancestors+=,@($parent,$sp)};if($parent.Current.NativeWindowHandle -eq $root.Current.NativeWindowHandle -and $parent.Current.NativeWindowHandle -ne 0){break};$parent=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($parent)}
  for($ancestorIndex=$ancestors.Count-1;$ancestorIndex -ge 0;$ancestorIndex--){$pair=$ancestors[$ancestorIndex];$scroll=$pair[0];$pattern=$pair[1];$viewport=$scroll.Current.BoundingRectangle;$ownBounds=$root.Current.BoundingRectangle;$viewport.Intersect($ownBounds);if($rect.Height -gt 0 -and $rect.Top -ge $viewport.Top-1 -and $rect.Bottom -le $viewport.Bottom+1 -and $rect.Height -ge [Math]::Max(1,$minimum-1)){continue};if($pattern.Current.VerticallyScrollable){$direction=if($rect.Height -gt 0 -and ($rect.Top -lt $viewport.Top -or ($rect.Height -lt $minimum-1 -and $rect.Top -le $viewport.Top+1))){[Windows.Automation.ScrollAmount]::SmallDecrement}else{[Windows.Automation.ScrollAmount]::SmallIncrement};if($e.Current.IsOffscreen -and $pattern.Current.VerticalScrollPercent -ge 99){$pattern.SetScrollPercent(-1,0)}else{$pattern.Scroll([Windows.Automation.ScrollAmount]::NoAmount,$direction)}};return $false};$bounds=$root.Current.BoundingRectangle;$script:reachedElement=$e;return -not $e.Current.IsOffscreen -and $rect.Height -ge [Math]::Max(1,$minimum-1) -and $rect.Top -ge $bounds.Top -and $rect.Bottom -le $bounds.Bottom} ('fully reachable '+$id)}

 function Click([string]$id){Reach $id;$script:reachedElement.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$script:checks++;if($id.StartsWith('Nav')){$page=$id.Substring(3);Await {$s=State;return $s.workspace.page -eq $page -and -not $s.workspace.management.loading -and $s.revision -eq $s.captureRevision} ('settled '+$page)}}
 function SetText([string]$id,[string]$value){Reach $id;$e=Find $id;$e.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($value)}
 Click 'NavModels'
 Await {$s=State;return $s.engine.connected -and $null -ne (Find 'ManagePickModels') -and -not $s.workspace.management.loading} 'models work without opening a gallery'
 Check (-not (State).engine.galleryOpened -and -not (State).engine.startupCatalogQueried) 'model startup never opens or queries gallery'
 Check ((State).workspace.gallery.itemCount -eq 0) 'no demonstration images generated'
 $models=Join-Path $output 'fixtures/models';[IO.Directory]::CreateDirectory($models)|Out-Null
 $weight=Join-Path $models 'standalone-fixture.safetensors';[IO.File]::WriteAllText($weight,'inert synthetic model, never deserialize')
 Click 'ManageShowModelPath';SetText 'ManageModelPath' $models;Click 'ManageAddModelRoot';Await {(State).workspace.management.rootCount -eq 1 -and -not (State).workspace.management.loading} 'model directory registered independently'
 Await {(State).workspace.management.modelCount -eq 1 -and -not (State).workspace.management.loading} 'adding directory automatically indexes independently'
 Check (-not (State).engine.galleryOpened) 'model scan has no gallery dependency'
 Checkpoint 'models-only'
 $firstProcess=(State).engine.processId
 Click 'NavGallery';Await {(State).engine.galleryOpened -and -not (State).workspace.gallery.loading -and $null -ne (Find 'GalleryImportPathToggle')} 'explicit image workspace'
 Check ((State).engine.processId -eq $firstProcess) 'image workspace reuses service, preserves model store'
 $pictures=Join-Path $output 'fixtures/pictures';[IO.Directory]::CreateDirectory($pictures)|Out-Null
 & (Join-Path $PSScriptRoot '../../../.venv-build/Scripts/python.exe') (Join-Path $PSScriptRoot 'create_independent_picture.py') $pictures
 if($LASTEXITCODE -ne 0){throw 'Synthetic fixture failed'}
 Click 'GalleryImportPathToggle';SetText 'GalleryImportPath' $pictures;Click 'GalleryImportStart';Await {(State).workspace.page -eq 'Jobs' -and (State).workspace.management.activeJobs -eq 0} 'explicit picture import finished'
 Click 'NavModels';Await {(State).workspace.management.modelCount -eq 1 -and -not (State).workspace.management.loading} 'gallery import preserves models'
 Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'first process safely exits'
 # Diagnostics intentionally require a fresh profile. Reopen normally using
 # own UIAutomation only; do not relax that privacy rule just for the test.
 $firstState=State;$pointer=[IO.File]::ReadAllText((Join-Path $data 'winui-library-v1.json'))
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --text-scale '+$TextScale
 $process=[Diagnostics.Process]::Start($start);Await {$process.Refresh();return $process.MainWindowHandle -ne [IntPtr]::Zero} 'own reopened process';$root=[Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
 function ClickNormal([string]$id){Reach $id;$script:reachedElement.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$script:checks++}
 ClickNormal 'NavModels';SelectFirstModel;Await {$e=Find 'ManageSelectedModel';return $e -and $e.Current.Name -eq 'standalone-fixture.safetensors'} 'model inventory persists without gallery startup'
 Check ([IO.File]::ReadAllText((Join-Path $data 'winui-library-v1.json')) -eq $pointer) 'same persistent application index'
 $galleryCount=Find 'GalleryCount';Check ($null -eq $galleryCount -or $galleryCount.Current.IsOffscreen) 'reopen never visits gallery workspace'
 ClickNormal 'ManageModelDetails';ClickNormal 'ManageCandidateImages';Await {$null -ne (Find 'ManageLinkedImage1')} 'cached image association without image workspace'
 ClickNormal 'ManageLinkedImage1';Await {$null -ne (Find 'ViewerBack') -and $null -ne (Find 'ViewerHeart')} 'model-associated image viewer works independently'
 Check ((Find 'ViewerHeart').Current.IsEnabled) 'indexed associated image remains editable'
 ClickNormal 'ViewerBack';Await {$e=Find 'ManageLinkedImage1';return $e -and -not $e.Current.IsOffscreen} 'back keeps model association'
 Check ([IO.File]::ReadAllText($weight) -eq 'inert synthetic model, never deserialize') 'model file unchanged'
 ClickNormal 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'reopened process safely exits'
 @{result='passed';count=$checks;syntheticOnly=$true;state=$firstState;normalReopen=$true;diagnosticPrivacyRulePreserved=$true;scope='models-first startup; normal process reopen via own UIA; explicit image import, shared-index association without gallery UI'}|ConvertTo-Json -Depth 14|Set-Content -LiteralPath (Join-Path $output 'model-independent-window-report.json');Write-Output ('MODEL_INDEPENDENT_WINDOW_PASSED '+$checks)
}catch{(State)|ConvertTo-Json -Depth 14|Set-Content -LiteralPath (Join-Path $output 'failure-state.json');($_|Format-List * -Force|Out-String)|Set-Content -LiteralPath (Join-Path $output 'exception.txt');throw}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null}}
