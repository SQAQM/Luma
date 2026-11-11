param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$TextScale=100,[int]$Width=1000,[int]$Height=650,[switch]$EmptyLibrary,[switch]$ThumbnailPressure,[switch]$ReconnectScope)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class GalleryWindowNative {
 [StructLayout(LayoutKind.Sequential)]public struct Point{public int X,Y;public Point(int x,int y){X=x;Y=y;}}
 [DllImport("user32.dll")]static extern IntPtr WindowFromPoint(Point p);
 [DllImport("user32.dll")]static extern int GetSystemMetrics(int index);
 public static bool OwnPoint(int pid,int x,int y){uint p;GetWindowThreadProcessId(WindowFromPoint(new Point(x,y)),out p);return p==(uint)pid;}
 public static void MovePointer(int x,int y){mouse_event(0x8001,(uint)Math.Round(x*65535.0/(GetSystemMetrics(0)-1)),(uint)Math.Round(y*65535.0/(GetSystemMetrics(1)-1)),0,UIntPtr.Zero);}

 public delegate bool EnumProc(IntPtr h,IntPtr l);
 [DllImport("user32.dll")]static extern bool EnumWindows(EnumProc p,IntPtr l);
 [DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 public static IntPtr[] OwnWindows(int pid){var v=new System.Collections.Generic.List<IntPtr>();EnumWindows((h,l)=>{uint p;GetWindowThreadProcessId(h,out p);if(p==pid)v.Add(h);return true;},IntPtr.Zero);return v.ToArray();}
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr info);
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int w,int height,uint flags);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
}
'@
$taskExe=[IO.Path]::GetFullPath($Executable);$taskOutput=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $taskOutput){throw 'Fresh output required'}
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
$taskData=Join-Path $taskOutput 'isolated-data';$taskState=Join-Path $taskData 'window-state.json'
$checks=New-Object 'System.Collections.Generic.List[object]';$process=$null
function Check([bool]$value,[string]$name){if(-not $value){State | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'failure-state.json');throw ('ENGINE_UI_FAIL '+$name)};$checks.Add(@{name=$name;passed=$true})}
function State {
 if(Test-Path -LiteralPath $taskState){$reader=$null;try{$reader=[IO.StreamReader]::new([IO.File]::Open($taskState,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));return $reader.ReadToEnd() | ConvertFrom-Json}catch [IO.IOException] {}finally{if($reader){$reader.Dispose()}}};return $null
}
function Await([scriptblock]$predicate,[string]$name){$end=[DateTime]::UtcNow.AddSeconds(50);do{if(&$predicate){return};if($process.HasExited){throw ('ENGINE_UI_EXIT '+$process.ExitCode+' '+$name)};Start-Sleep -Milliseconds 40}while([DateTime]::UtcNow -lt $end);throw ('ENGINE_UI_TIMEOUT '+$name)}
try{
 $start=[Diagnostics.ProcessStartInfo]::new($taskExe);$start.UseShellExecute=$false;$start.WorkingDirectory=Split-Path -Parent $taskExe;$start.WindowStyle='Hidden'
 $start.Arguments='--data-dir "'+$taskData+'" --diagnostics --capture --text-scale '+$TextScale
 if($ThumbnailPressure){$start.Arguments+=' --thumbnail-pressure'}
 $start.EnvironmentVariables['PATH']=$env:SystemRoot+'\System32;'+$env:SystemRoot;$start.EnvironmentVariables['DOTNET_ROOT']=Join-Path $taskOutput 'absent-dotnet';$start.EnvironmentVariables['PYTHONPATH']=Join-Path $taskOutput 'absent-python'
 $start.EnvironmentVariables['LUMA_ENGINE_UI_TEST']='1'
 $process=[Diagnostics.Process]::Start($start)
 Await {$s=State;return $s.ready -and $s.captureRevision -eq $s.revision} 'ready'
 $hwnd=[IntPtr][long](State).hwnd;$root=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
 [GalleryWindowNative]::SetWindowPos($hwnd,[IntPtr]::Zero,0,0,$Width,$Height,0x16) | Out-Null
 Await {$v=State;return $v.windowWidth -eq $Width -and $v.windowHeight -eq $Height -and $v.captureRevision -eq $v.revision} 'requested dimensions'
 function Find([string]$id){
  $condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id);$fallback=$null
  foreach($handle in [GalleryWindowNative]::OwnWindows($process.Id)){try{$surface=[Windows.Automation.AutomationElement]::FromHandle($handle);$element=$surface.FindFirst([Windows.Automation.TreeScope]::Descendants,$condition);if($element){if(-not $element.Current.IsOffscreen){return $element};$fallback=$element}}catch{}}
  return $fallback
 }
 function Click([string]$id){$element=Find $id;if($element){$element.SetFocus();Await {$e=Find $id;$b=$e.Current.BoundingRectangle;$w=$root.Current.BoundingRectangle;$script:lastBounds=@{id=$id;element=$b;window=$w;offscreen=$e.Current.IsOffscreen;enabled=$e.Current.IsEnabled};return -not $e.Current.IsOffscreen -and $b.Width -gt 0 -and $b.Left -ge $w.Left -and $b.Top -ge $w.Top -and $b.Right -le $w.Right -and $b.Bottom -le $w.Bottom} ('focused visible '+$id)};Check ($null -ne $element) ('exists '+$id);Check ($element.Current.IsEnabled -and -not $element.Current.IsOffscreen) ('reachable '+$id);$b=$element.Current.BoundingRectangle;$w=$root.Current.BoundingRectangle;Check ($b.Left -ge $w.Left -and $b.Top -ge $w.Top -and $b.Right -le $w.Right -and $b.Bottom -le $w.Bottom) ('bounds '+$id);$before=(State).revision;$element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();if($id -ne 'ClosePreview'){Await {$v=State;return $v.revision -gt $before -and $v.captureRevision -eq $v.revision} ('layout settled '+$id)}}

 Check (-not (State).engineStarted) 'no automatic engine'
 Check ($null -ne (Find 'GalleryOpen')) 'gallery entry exists'
 if($EmptyLibrary){Click 'NavJobs';Click 'EngineConnect';Await {(State).engine.connected -and (State).captureRevision -eq (State).revision} 'empty engine ready';Click 'NavGallery'}
 Click 'GalleryOpen';Await {$g=(State).workspace.gallery;return $g.connected -and -not $g.loading -and (($g.itemCount -eq 120) -or ($EmptyLibrary -and $g.itemCount -eq 0)) -and (State).captureRevision -eq (State).revision} 'real gallery page'

 function Choose([string]$id,[string]$name){
  [GalleryWindowNative]::SetForegroundWindow($hwnd) | Out-Null
  Await {$null -ne (Find $id)} ("available "+$id)
  $combo=Find $id;$combo.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
  $condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,$name)
  $choice=$null;$endChoice=[DateTime]::UtcNow.AddSeconds(8)
  do{
   foreach($handle in [GalleryWindowNative]::OwnWindows($process.Id)){
    try{$surface=[Windows.Automation.AutomationElement]::FromHandle($handle);$matches=$surface.FindAll([Windows.Automation.TreeScope]::Descendants,$condition)
     foreach($item in $matches){$pattern=$null;if($item.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)){$choice=$pattern;break}}
    }catch{}
    if($choice){break}
   }
   if($choice){break};Start-Sleep -Milliseconds 40
  }while([DateTime]::UtcNow -lt $endChoice)
  if(-not $choice){
   $names=@();foreach($handle in [GalleryWindowNative]::OwnWindows($process.Id)){try{$surface=[Windows.Automation.AutomationElement]::FromHandle($handle);foreach($item in $surface.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition) | Select-Object -First 150){$names+=@{name=$item.Current.Name;type=$item.Current.ControlType.ProgrammaticName;class=$item.Current.ClassName}}}catch{}}
   $names | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskOutput 'choice-candidates.json')
   throw ('CHOICE_NOT_FOUND '+$id+' '+$name)
  }
  $choice.Select();$combo.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse()
  Await {$combo.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Collapsed} ("collapsed "+$id)
 }
 function Gallery {return (State).workspace.gallery}
 if($EmptyLibrary){Check ((Gallery).total -eq 0 -and -not (Find 'GalleryNext').Current.IsEnabled) 'empty library without fake rows';Click 'ClosePreview';Check ($process.WaitForExit(20000) -and $process.ExitCode -eq 0) 'empty library safe close';@{result='passed';count=$checks.Count;checks=$checks.ToArray();state=(State)} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'gallery-window-report.json');Write-Output ('GALLERY_EMPTY_PASSED '+$checks.Count);return}
 Check ((Gallery).itemCount -eq 120 -and (Gallery).total -eq 131) '120 bounded records from real SQLite'
 Await {(Gallery).thumbnailCompleted -gt 0 -and (State).captureRevision -eq (State).revision} 'actual thumbnail decode'
 if($ThumbnailPressure){Check ((Gallery).thumbnailBusyRetries -gt 0) 'real queue pressure automatically refills visible thumbnails'}
 Check ((Gallery).realizedContainers -gt 0 -and (Gallery).realizedContainers -lt 120) 'finite virtualized viewport'
 Check ((Gallery).decodedBytes -le 64*1024*1024 -and (Gallery).thumbnailActive -le 1) 'image memory and request budgets'
 if($ReconnectScope){
  Click 'GalleryFilters';Choose 'GalleryFilterKind' '格式';Choose 'GalleryFilterValue' 'PNG';Click 'GalleryApplyFilter'
  Await {(Gallery).formatFilter -eq 'PNG' -and (Gallery).total -eq 65 -and (State).captureRevision -eq (State).revision} 'old scoped library'
  (Find 'GalleryImage1').GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
  Await {(Gallery).detailId -eq 1 -and (State).captureRevision -eq (State).revision} 'old populated detail'
  Click 'NavJobs';Click 'EngineDisconnect';Await {-not (State).engine.alive -and (State).captureRevision -eq (State).revision} 'old engine released'
  Click 'EngineConnect';Await {(State).engine.connected -and (State).captureRevision -eq (State).revision} 'new independent empty engine'
  Click 'NavGallery';Click 'GalleryOpen';Await {(Gallery).connected -and -not (Gallery).loading -and (Gallery).itemCount -eq 0 -and (State).captureRevision -eq (State).revision} 'new empty library'
  Choose 'GallerySort' '名称 Z–A';Await {(State).captureRevision -eq (State).revision -and -not (Gallery).loading} 'new scope sort'
  Check ((Gallery).formatFilter -eq '' -and (Gallery).modelFilter -eq '' -and (Gallery).queryLength -eq 0 -and (Gallery).total -eq 0) 'rebind does not resurrect previous filters'
  [GalleryWindowNative]::SetWindowPos($hwnd,[IntPtr]::Zero,0,0,1600,900,0x16)|Out-Null;Await {(Gallery).inspectorVisible -and $null -ne (Find 'GalleryPrompt') -and (State).captureRevision -eq (State).revision} 'expose rebound empty inspector';Check ((Gallery).promptLength -eq 0 -and (Gallery).negativeLength -eq 0 -and $null -eq (Gallery).detailId) 'empty rebound detail has no old text'
  Check ((Find 'GalleryPrompt').FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::Text)).Count -eq 0) 'rebind clears old prompt UI'
  Click 'ClosePreview';Check ($process.WaitForExit(20000) -and $process.ExitCode -eq 0) 'rebound library safe close'
  @{result='passed';count=$checks.Count;checks=$checks.ToArray();state=(State)} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'gallery-window-report.json')
  Write-Output ('GALLERY_RECONNECT_PASSED '+$checks.Count);return
 }

 Click 'GalleryNext';Check ((Gallery).itemCount -eq 11 -and (Gallery).page -eq 2 -and (Gallery).ids -contains 1000001) '120 to 121 and large ID'
 Click 'GalleryPrevious';Await {$null -ne (Find 'GalleryImage1')} 'first item realized'
 (Find 'GalleryImage1').GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
 (Find 'GalleryImage2').GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).AddToSelection()
 Await {(Gallery).selectedCount -eq 2 -and (Gallery).detailId -eq 2 -and (State).captureRevision -eq (State).revision} 'multi selection'

 $rightItem=Find 'GalleryImage1';$rightItem.GetCurrentPattern([Windows.Automation.ScrollItemPattern]::Pattern).ScrollIntoView();Await {-not (Find 'GalleryImage1').Current.IsOffscreen -and (State).captureRevision -eq (State).revision} 'right item visible';$rect=(Find 'GalleryImage1').Current.BoundingRectangle
 $gridRect=(Find 'GalleryImages').Current.BoundingRectangle;$pointX=[int]($rect.Left+$rect.Width/2);$pointY=[int]($rect.Top+$rect.Height/2)
 Check ($pointX -gt $gridRect.Left -and $pointX -lt $gridRect.Right -and $pointY -gt $gridRect.Top -and $pointY -lt $gridRect.Bottom) 'right click point stays in own image viewport'
 [GalleryWindowNative]::SetForegroundWindow($hwnd) | Out-Null
 Check ([GalleryWindowNative]::GetForegroundWindow() -eq $hwnd) 'right click targets own window'
 Check ([GalleryWindowNative]::OwnPoint($process.Id,$pointX,$pointY)) 'right click point belongs to own process';[GalleryWindowNative]::MovePointer($pointX,$pointY);Start-Sleep -Milliseconds 50;Check ([GalleryWindowNative]::OwnPoint($process.Id,$pointX,$pointY)) 'right click remains in own process'
 [GalleryWindowNative]::mouse_event(8,0,0,0,[UIntPtr]::Zero);[GalleryWindowNative]::mouse_event(16,0,0,0,[UIntPtr]::Zero)
 $endContext=[DateTime]::UtcNow.AddSeconds(3);do{Start-Sleep -Milliseconds 40}while((Gallery).currentId -ne 1 -and [DateTime]::UtcNow -lt $endContext)
 Check ((Gallery).currentId -eq 1 -and (Gallery).selectedCount -eq 2) 'right clicked selected item becomes action target'
 Await {(Gallery).detailId -eq 1 -or (Gallery).detailError.Length -gt 0} 'context detail completed'
 Check ((Gallery).detailId -eq 1) 'context detail is available'
 $nameCondition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,'资源管理器选中')
 $script:contextItem=$null
 Await {
  foreach($handle in [GalleryWindowNative]::OwnWindows($process.Id)){
   try{$surface=[Windows.Automation.AutomationElement]::FromHandle($handle);$candidate=$surface.FindFirst([Windows.Automation.TreeScope]::Descendants,$nameCondition);if($candidate -and -not $candidate.Current.IsOffscreen -and $candidate.Current.IsEnabled){$script:contextItem=$candidate;return $true}}catch{}
  }
  return $false
 } 'native context menu available in own process'
 Check ($null -ne $contextItem) 'flat native file menu'
 $contextItem.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
 Await {(Gallery).lastAction -eq 'select' -and (State).captureRevision -eq (State).revision} 'context action'
 (Find 'GalleryImage2').GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
 Await {(Gallery).detailId -eq 2 -and (State).captureRevision -eq (State).revision} 'return to blank negative'
 Check ((Gallery).negativeLength -eq 0) 'missing negative remains blank'
 Click 'GalleryDetails';Check (-not (Find 'GalleryCopyNegative').Current.IsEnabled) 'empty negative copy disabled'
 Click 'GalleryDetails';(Find 'GalleryImage1').GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
 Await {(Gallery).detailId -eq 1 -and (Gallery).promptLength -gt 2048 -and (State).captureRevision -eq (State).revision} 'full detail replaces summary'
 Click 'GalleryDetails';Click 'GalleryCopyPrompt';Check ((Gallery).lastAction -eq 'prompt' -and (Gallery).lastCopyLength -eq (Gallery).promptLength) 'complete copy through diagnostic sink'
 Check ((Find 'GalleryFeedback').Current.Name.Contains('已复制')) 'visible copy feedback'
 $inspector=(Find 'GalleryInspectorScroll').GetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern)
 $inspector.SetScrollPercent(-1,100);Await {-not (Find 'GalleryCopyNegative').Current.IsOffscreen} 'negative copy reachable by scrolling'
 Click 'GalleryCopyNegative';Check ((Gallery).lastAction -eq 'negative' -and (Gallery).lastCopyLength -eq (Gallery).negativeLength) 'complete negative copy'
 $tokens=Find 'GalleryPromptTokens';$tokens.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
 $inspector.SetScrollPercent(-1,100);Await {$null -ne (Find 'GalleryPromptToken0')} 'separate prompt tokens'
 Check ((Find 'GalleryPromptToken0').Current.Name -eq 'synthetic-prompt-token') 'prompt token distinct from semantic chip'
 $inspector.SetScrollPercent(-1,0);Await {-not (Find 'GalleryTag1').Current.IsOffscreen} 'semantic chip restored'

 Click 'GalleryReveal';Check ((Gallery).lastAction -eq 'select') 'structured reveal intent'
 Check ($null -eq (Find 'GalleryFolder')) 'duplicate folder toolbar action removed'
 Click 'GalleryOpenOriginal';Check ((Gallery).lastAction -eq 'open') 'structured original intent'
 Click 'GalleryCopyPath';Check ((Gallery).lastAction -eq 'path') 'copy path intent'
 Click 'GalleryTag1';Check ((Gallery).tagFilter -eq 1 -and (Gallery).total -eq 87 -and $null -eq (Gallery).detailId) 'semantic tag filters indexed fixture'
 Click 'GalleryFilters';Choose 'GalleryFilterKind' '内容标签'
 Await {$null -ne (Find 'GalleryFilterInput')} 'filter input available after popup closes'
 $edit=Find 'GalleryFilterInput';Check ($null -ne $edit) 'explicit native filter text input'
 $edit.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue('missing-semantic-name')
 Click 'GalleryApplyFilter';Check ((Gallery).tagFilter -eq 1 -and (Gallery).total -eq 87) 'invalid semantic entry preserves active scope'
 Click 'GalleryFilters'

 (Find 'GallerySearch').GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue('no-such-fixture')
 Await {(Gallery).queryLength -gt 0 -and (Gallery).itemCount -eq 0 -and -not (Gallery).loading -and (State).captureRevision -eq (State).revision} 'no matches'
 Check ((Gallery).selectedCount -eq 0 -and $null -eq (Gallery).detailId) 'old details and selection cleared'
 Click 'GalleryClear';Await {(Gallery).itemCount -eq 120 -and -not (Gallery).loading -and (State).captureRevision -eq (State).revision} 'clear filters'

 Click 'GalleryViewMode';Check ((Gallery).viewMode -eq 'list' -and (Gallery).realizedContainers -lt 120) 'finite native list view'
 Click 'GalleryViewMode';Check ((Gallery).viewMode -eq 'grid') 'native grid restored'
 Choose 'GallerySort' '名称 Z–A';Await {$g=Gallery;return $g.ids.Count -gt 0 -and $g.ids[0] -eq 1000001 -and (State).captureRevision -eq (State).revision} 'descending sort actual row'
 Choose 'GallerySort' '名称 A–Z';Await {$g=Gallery;return $g.ids.Count -gt 0 -and $g.ids[0] -eq 1 -and (State).captureRevision -eq (State).revision} 'ascending sort restored'
 foreach($filter in @(@{kind='格式';value='PNG';field='formatFilter';total=65},@{kind='模型';value='合成模型 SDXL';field='modelFilter';total=66},@{kind='画师';value='合成画师 A';field='artistFilter';total=66},@{kind='提示词词项';value='synthetic-prompt-token';field='promptTagFilter';total=131})){
  Click 'GalleryFilters';Choose 'GalleryFilterKind' $filter.kind;Choose 'GalleryFilterValue' $filter.value
  Click 'GalleryApplyFilter';Await {(Gallery).total -eq $filter.total -and -not (Gallery).loading -and (State).captureRevision -eq (State).revision} ('applied '+$filter.kind)
  Check ((Gallery).($filter.field) -eq $filter.value) ('exact filter '+$filter.kind)
  Click 'GalleryClear';Await {(Gallery).total -eq 131 -and -not (Gallery).loading -and (State).captureRevision -eq (State).revision} 'filters cleared'
 }
 Choose 'GalleryRoots' 'demo-images';Await {(Gallery).itemCount -eq 120 -and -not (Gallery).loading -and (State).captureRevision -eq (State).revision} 'directory filter'
 Click 'GalleryClear'
 Click 'NavModels';Click 'NavGallery';Check ((Gallery).itemCount -eq 120 -and (Gallery).page -eq 1) 'navigation retains gallery window'
 Click 'NavSettings';Click 'ThemeDark';Click 'ThemeLight';Click 'NavGallery';Check (-not (State).catalogOpened) 'production catalog remains closed'
 Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'native gallery safe close'
 @{result='passed';count=$checks.Count;checks=$checks.ToArray();state=(State)} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'gallery-window-report.json')
 Write-Output ('GALLERY_WINDOW_PASSED '+$checks.Count)
}catch{if($script:lastBounds){$script:lastBounds|ConvertTo-Json -Depth 5|Set-Content -LiteralPath (Join-Path $taskOutput 'bounds-readout.json')};($_ | Format-List * -Force | Out-String) | Set-Content -LiteralPath (Join-Path $taskOutput 'exception-detail.txt');throw}finally{if($process -and -not $process.HasExited){$checks.ToArray() | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskOutput 'partial-checks.json');$process.CloseMainWindow() | Out-Null;$process.WaitForExit(30000) | Out-Null}}
