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
try{
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --diagnostics --capture --text-scale '+$TextScale
 $process=[Diagnostics.Process]::Start($start);Await {(State).ready -and (State).revision -eq (State).captureRevision} 'ready';$root=[Windows.Automation.AutomationElement]::FromHandle([IntPtr][long](State).hwnd);[ManageNative]::SetWindowPos([IntPtr][long](State).hwnd,[IntPtr]::Zero,0,0,$Width,$Height,0x14)|Out-Null
 function Find([string]$id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
 function Reach([string]$id){Await {$e=Find $id;if(-not $e -or -not $e.Current.IsEnabled){return $false};$rect=$e.Current.BoundingRectangle;$minimum=if($id.StartsWith("Nav") -and $id -ne "NavSettings"){48}else{0};$parent=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($e);$ancestors=@();for($level=0;$level -lt 16 -and $parent;$level++){if($parent.Current.ProcessId -ne $process.Id){break};$sp=$null;if($parent.TryGetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern,[ref]$sp)){$ancestors+=,@($parent,$sp)};if($parent.Current.NativeWindowHandle -eq $root.Current.NativeWindowHandle -and $parent.Current.NativeWindowHandle -ne 0){break};$parent=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($parent)}
  for($ancestorIndex=$ancestors.Count-1;$ancestorIndex -ge 0;$ancestorIndex--){$pair=$ancestors[$ancestorIndex];$scroll=$pair[0];$pattern=$pair[1];$viewport=$scroll.Current.BoundingRectangle;$ownBounds=$root.Current.BoundingRectangle;$viewport.Intersect($ownBounds);if($rect.Height -gt 0 -and $rect.Top -ge $viewport.Top-1 -and $rect.Bottom -le $viewport.Bottom+1 -and $rect.Height -ge [Math]::Max(1,$minimum-1)){continue};if($pattern.Current.VerticallyScrollable){$direction=if($rect.Height -gt 0 -and ($rect.Top -lt $viewport.Top -or ($rect.Height -lt $minimum-1 -and $rect.Top -le $viewport.Top+1))){[Windows.Automation.ScrollAmount]::SmallDecrement}else{[Windows.Automation.ScrollAmount]::SmallIncrement};if($e.Current.IsOffscreen -and $pattern.Current.VerticalScrollPercent -ge 99){$pattern.SetScrollPercent(-1,0)}else{$pattern.Scroll([Windows.Automation.ScrollAmount]::NoAmount,$direction)}};return $false};$bounds=$root.Current.BoundingRectangle;$script:reachedElement=$e;return -not $e.Current.IsOffscreen -and $rect.Height -ge [Math]::Max(1,$minimum-1) -and $rect.Top -ge $bounds.Top -and $rect.Bottom -le $bounds.Bottom} ('fully reachable '+$id)}

 function Click([string]$id){Reach $id;$script:reachedElement.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$script:checks++;if($id.StartsWith('Nav')){$page=$id.Substring(3);Await {$s=State;return $s.workspace.page -eq $page -and -not $s.workspace.management.loading -and $s.revision -eq $s.captureRevision} ('settled '+$page)}}
 function SetText([string]$id,[string]$value){Reach $id;$e=Find $id;$e.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($value)}

 Click 'NavModels'
 Await {$s=State;return $s.engine.connected -and $null -ne (Find 'ManagePickModels') -and -not $s.workspace.management.loading} 'independent model workspace'
 Check (-not (State).engine.galleryOpened -and -not (State).engine.startupCatalogQueried) 'no gallery prerequisite'
 $python=Join-Path $PSScriptRoot '../../../.venv-build/Scripts/python.exe';$fixture=Join-Path $PSScriptRoot 'create_model_card_fixture.py'
 & $python $fixture prepare $output;if($LASTEXITCODE -ne 0){throw 'Synthetic fixture failed'}
 $models=Join-Path $output 'fixtures/models';$weight=Join-Path $models 'standalone-fixture.safetensors'
 Click 'ManageShowModelPath';SetText 'ManageModelPath' $models;Click 'ManageAddModelRoot'
 Await {$s=State;return $s.workspace.management.rootCount -eq 1 -and $s.workspace.management.modelTotal -eq 66 -and $s.workspace.management.modelCount -eq 60 -and -not $s.workspace.management.loading -and -not $s.workspace.management.writing} 'one add automatically indexes 66 synthetic models'
 Check ($null -eq (Find 'ManageScanModels')) 'no separate indexing button'
 Check (-not (State).workspace.management.modelInspector) 'cover grid uses full workspace until selection'
 Checkpoint 'added-cover-grid'
 & $python $fixture seed $output;if($LASTEXITCODE -ne 0){throw 'Synthetic cached metadata seed failed'}
 $evidence=Get-Content -LiteralPath (Join-Path $output 'fixture-evidence.json') -Raw|ConvertFrom-Json
 $modelId=$evidence.'standalone-fixture.safetensors'.id
 Click 'ManageRefresh';Await {$s=State;return $null -ne (Find 'ManageFiltertypesLoRA') -and $s.workspace.management.modelCoversLoaded -ge 1 -and $s.workspace.management.modelCoversFailed -ge 1 -and -not $s.workspace.management.loading} 'cached covers and corrupt cover fallback'
 Check ((State).workspace.management.modelCoverActive -le 2) 'cover work concurrency bounded'
 Checkpoint 'cached-cover-grid'
 function Toggle([string]$id){Reach $id;$script:reachedElement.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Toggle();$script:checks++}
 Toggle 'ManageFiltertypesLoRA';Await {(State).workspace.management.modelTotal -eq 41 -and -not (State).workspace.management.loading} 'LoRA classification'
 Toggle 'ManageFiltertypesCheckpoint';Await {(State).workspace.management.modelTotal -eq 65 -and -not (State).workspace.management.loading} 'OR within type multi-selection'
 Toggle 'ManageFilterfamiliesKrea 2';Await {(State).workspace.management.modelTotal -eq 35 -and -not (State).workspace.management.loading} 'AND between type and family'
 Toggle 'ManageFilterfamiliesStable Diffusion';Await {(State).workspace.management.modelTotal -eq 65 -and -not (State).workspace.management.loading} 'OR within family multi-selection'
 Check ((State).workspace.management.modelTypes.Count -eq 2 -and (State).workspace.management.modelFamilies.Count -eq 2) 'both multi-selections retained'
 Checkpoint 'multi-dimension-selection'
 Toggle 'ManageFiltertypesLoRA';Await {(State).workspace.management.modelTotal -eq 24 -and -not (State).workspace.management.loading} 'remove one type selection'
 Toggle 'ManageFiltertypesAll';Await {(State).workspace.management.modelTotal -eq 65 -and -not (State).workspace.management.loading} 'clear only type row'
 Toggle 'ManageFilterfamiliesAll';Await {(State).workspace.management.modelTotal -eq 66 -and -not (State).workspace.management.loading} 'clear family row'
 Click 'ManageModelNext';Await {(State).workspace.management.modelCount -eq 6 -and -not (State).workspace.management.loading} 'SQL second page'
 Toggle 'ManageFilterfamiliesKrea 2';Await {(State).workspace.management.modelTotal -eq 35 -and (State).workspace.management.modelPage -eq 1 -and -not (State).workspace.management.loading} 'filter invalidates old cursor'
 Click 'ManageClearModelSearch';Await {(State).workspace.management.modelTotal -eq 66 -and -not (State).workspace.management.loading} 'one reset clears all model filters'
 Toggle 'ManageFilterfamiliesUnknown';Await {(State).workspace.management.modelTotal -eq 1 -and -not (State).workspace.management.loading} 'unknown remains explicit'
 Click 'ManageClearModelSearch';Await {(State).workspace.management.modelTotal -eq 66 -and -not (State).workspace.management.loading} 'reset unknown'
 Reach ('ManageModelCard'+$evidence.'model-000.safetensors'.id);$script:reachedElement.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select();Await {(State).workspace.management.modelInspector} 'first model inspector'
 Click 'ManageModelCloseDetails';Check (((Find 'ManageList').GetCurrentPattern([Windows.Automation.SelectionPattern]::Pattern).Current.GetSelection()).Count -eq 1) 'first model selection survives return'
 Reach ('ManageModelCard'+$evidence.'model-001.safetensors'.id);$script:reachedElement.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).AddToSelection();Await {(State).workspace.management.modelInspector} 'second selected model inspector'
 Click 'ManageModelCloseDetails';Check (((Find 'ManageList').GetCurrentPattern([Windows.Automation.SelectionPattern]::Pattern).Current.GetSelection()).Count -eq 2) 'two model selections accumulate across detail return'
 SetText 'ManageModelSearch' 'standalone-fixture';Click 'ManageSearchModels';Await {(State).workspace.management.modelCount -eq 1 -and -not (State).workspace.management.loading} 'model name search'
 Reach ('ManageModelCard'+$modelId);$script:reachedElement.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select();Await {$null -ne (Find 'ManageSelectedModel')} 'same-page model information'
 Checkpoint 'model-card-details'
 Click 'ManageCopyModelPath';Check ((Find 'ManageStatus').Current.Name.Contains('已复制')) 'copy feedback'
 Click 'ManageModelCloseDetails';Await {-not (State).workspace.management.modelInspector} 'close details restores full cover grid'
 $firstProcess=(State).engine.processId
 Click 'NavGallery';Await {(State).engine.galleryOpened -and $null -ne (Find 'GalleryImportPathToggle')} 'explicit gallery'
 Check ((State).engine.processId -eq $firstProcess) 'models and images reuse persistent local services'
 $pictures=Join-Path $output 'fixtures/pictures';[IO.Directory]::CreateDirectory($pictures)|Out-Null
 & $python (Join-Path $PSScriptRoot 'create_independent_picture.py') $pictures;if($LASTEXITCODE -ne 0){throw 'Synthetic picture creation failed'}
 Click 'GalleryImportPathToggle';SetText 'GalleryImportPath' $pictures;Click 'GalleryImportStart';Await {(State).workspace.page -eq 'Jobs' -and (State).workspace.management.activeJobs -eq 0} 'single explicit path add indexes picture'
 Click 'NavModels';Await {(State).workspace.management.modelCount -gt 0 -and -not (State).workspace.management.loading} 'models retained'
 Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'first process safe close'
 $firstState=State;$pointer=[IO.File]::ReadAllText((Join-Path $data 'winui-library-v1.json'))
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --text-scale '+$TextScale
 $process=[Diagnostics.Process]::Start($start);Await {$process.Refresh();return $process.MainWindowHandle -ne [IntPtr]::Zero} 'normal-mode reopen';$root=[Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
 function ClickNormal([string]$id){Reach $id;$script:reachedElement.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$script:checks++}
 ClickNormal 'NavModels';Await {$null -ne (Find 'ManageModelSearch')} 'models directly reopened'
 Check ([IO.File]::ReadAllText((Join-Path $data 'winui-library-v1.json')) -eq $pointer) 'existing application index preserved'
 SetText 'ManageModelSearch' 'standalone-fixture';ClickNormal 'ManageSearchModels';Reach ('ManageModelCard'+$modelId);$script:reachedElement.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
 ClickNormal 'ManageModelDetails';ClickNormal 'ManageCandidateImages';Await {$null -ne (Find 'ManageLinkedImage1')} 'model-associated image without gallery UI'
 ClickNormal 'ManageLinkedImage1';Await {$script:readyHeart=Find 'ViewerHeart';return $script:readyHeart -and $script:readyHeart.Current.IsEnabled -and -not $script:readyHeart.Current.IsOffscreen} 'associated viewer opens'
 Check ($script:readyHeart.Current.IsEnabled) 'indexed associated image editable'
 ClickNormal 'ViewerBack';Await {$null -ne (Find 'ManageLinkedImage1')} 'viewer return preserves model detail'
 Check ([IO.File]::ReadAllText($weight) -eq 'inert synthetic model, never deserialize') 'weight unchanged'
 ClickNormal 'ManageModelReturn'
 Await {$e=Find 'ManageModelCloseDetails';return $e -and $e.Current.IsEnabled -and -not $e.Current.IsOffscreen -and $null -eq (Find 'ManageModelReturn')} 'returned model inspector after async details refresh'
 ClickNormal 'ManageModelCloseDetails'
 Await {$e=Find 'ManageModelRoots';return $e -and $e.Current.IsEnabled -and -not $e.Current.IsOffscreen} 'cover grid and directory controls restored'
 Reach 'ManageModelRoots';(Find 'ManageModelRoots').GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
 Await {$script:rootPattern=$null;foreach($window in [Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty,$process.Id))){$entry=$window.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,'models · 66 个'));if($entry){$pattern=$null;if($entry.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)){$script:rootPattern=$pattern;return $true}}};return $false} 'actual directory option'
 try{$script:rootPattern.Select()}catch{if(-not $_.Exception.ToString().Contains('ElementNotEnabledException')){throw}}
 Await {$e=Find 'ManageRemoveModelRoot';return $e -and $e.Current.IsEnabled} 'actual folder selected for removal'
 ClickNormal 'ManageRemoveModelRoot' 
 Await {$null -ne $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,'继续'))} 'remove exact folder confirmation'
 $confirm=$root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,'继续'));$confirm.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$checks++
 Await {$null -ne (Find 'ManageModelEmpty')} 'directory registration removed'
 Check ([IO.Directory]::GetFiles($models).Count -eq 66) 'delete directory keeps every weight file'
 ClickNormal 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'normal process safe close'
 @{result='passed';count=$checks;syntheticOnly=$true;state=$firstState;normalReopen=$true;scope='add auto-index; cached cover grid; SQL dimensions OR/AND; bounded paging; normal reopen; association; safe directory removal; native picker wiring present but actual picker not automated'}|ConvertTo-Json -Depth 14|Set-Content -LiteralPath (Join-Path $output 'model-cards-window-report.json');Write-Output ('MODEL_CARDS_WINDOW_PASSED '+$checks)
}catch{(State)|ConvertTo-Json -Depth 14|Set-Content -LiteralPath (Join-Path $output 'failure-state.json');($_|Format-List * -Force|Out-String)|Set-Content -LiteralPath (Join-Path $output 'exception.txt');throw}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null}}
