param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory,[int]$TextScale=100,[int]$Width=1100,[int]$Height=740,[switch]$Baseline,[switch]$PointerWake,[int]$PanelStress=0,[switch]$NoCapture,[string]$DumpTool)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;using System.Runtime.InteropServices;
public static class PolishNative {
 [StructLayout(LayoutKind.Sequential)]public struct Point{public int X,Y;public Point(int x,int y){X=x;Y=y;}}
 [DllImport("user32.dll")]static extern IntPtr WindowFromPoint(Point p);
 [DllImport("user32.dll")]public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")]public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
 [DllImport("user32.dll")]static extern int GetSystemMetrics(int index);
 public static void MovePointer(int x,int y){mouse_event(0x8001,(uint)Math.Round(x*65535.0/(GetSystemMetrics(0)-1)),(uint)Math.Round(y*65535.0/(GetSystemMetrics(1)-1)),0,UIntPtr.Zero);}
 public static bool OwnPoint(int pid,int x,int y){uint actual;GetWindowThreadProcessId(WindowFromPoint(new Point(x,y)),out actual);return actual==pid;}
 public delegate bool EnumProc(IntPtr h,IntPtr l);
 [DllImport("user32.dll")]static extern bool EnumWindows(EnumProc p,IntPtr l);
 [DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 public static IntPtr[] OwnWindows(int pid){var items=new System.Collections.Generic.List<IntPtr>();EnumWindows((h,l)=>{uint actual;GetWindowThreadProcessId(h,out actual);if(actual==pid)items.Add(h);return true;},IntPtr.Zero);return items.ToArray();}
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int w,int height,uint flags);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern void keybd_event(byte key,byte scan,uint flags,UIntPtr extra);
}
'@
$out=[IO.Path]::GetFullPath($OutputDirectory);if(Test-Path -LiteralPath $out){throw 'Fresh own output required'}
[IO.Directory]::CreateDirectory($out)|Out-Null;$data=Join-Path $out 'isolated-data';$statePath=Join-Path $data 'window-state.json';$process=$null;$checks=0
function State {if(Test-Path -LiteralPath $statePath){try{$r=[IO.StreamReader]::new([IO.File]::Open($statePath,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));try{return $r.ReadToEnd()|ConvertFrom-Json}finally{$r.Dispose()}}catch [IO.IOException]{}}}
function Gallery {return (State).workspace.gallery}
function Viewer {return (State).workspace.viewer}
function Check([bool]$ok,[string]$name){if(-not $ok){throw ('PHOTO_POLISH_FAIL '+$name)};$script:checks++}
function Await([scriptblock]$predicate,[string]$name){$until=[DateTime]::UtcNow.AddSeconds(35);do{if(&$predicate){return};if($process.HasExited){throw ('EXIT '+$name+' '+$process.ExitCode)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $until);throw ('TIMEOUT '+$name)}
function Find([string]$id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
function Click([string]$id){Await {$script:clickTarget=Find $id;return $null -ne $clickTarget -and $clickTarget.Current.IsEnabled} ('available '+$id);$e=$clickTarget;Check ($null -ne $e) ('exists '+$id);try{$e.SetFocus()}catch{};Await {$script:clickTarget=Find $id;return $null -ne $clickTarget -and $clickTarget.Current.IsEnabled -and -not $clickTarget.Current.IsOffscreen} ('reachable '+$id);$before=(State).revision;$clickTarget.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();if($id -ne 'ClosePreview'){Await {(State).revision -gt $before} ('handled '+$id)}}
function Key([byte]$key){Check ([PolishNative]::GetForegroundWindow() -eq $hwnd) 'own foreground for keyboard';[PolishNative]::keybd_event($key,0,0,[UIntPtr]::Zero);[PolishNative]::keybd_event($key,0,2,[UIntPtr]::Zero)}
function SelectImage([long]$id){Await {$script:imageTarget=Find ('GalleryImage'+$id);return $null -ne $imageTarget -and -not (Gallery).loading -and -not (Gallery).annotationWriting} 'image realized';$imageTarget.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select();Await {(Gallery).currentId -eq $id -and -not (Gallery).detailLoading -and -not (Gallery).loading} 'selected image'}
function Capture([string]$name){if($NoCapture){return};Await {$s=State;return $s.captureRevision -eq $s.revision} 'capture settled';$s=State;Copy-Item -LiteralPath (Join-Path $data $s.captures[-1]) -Destination (Join-Path $out ($name+'.png'))}
function Choose([string]$id,[string]$name){
 Await {$script:choiceBox=Find $id;$s=State;return $null -ne $choiceBox -and -not $s.workspace.management.loading -and ($NoCapture -or $s.captureRevision -eq $s.revision)} ('choice available '+$id)
 $box=$choiceBox;[PolishNative]::SetForegroundWindow($hwnd)|Out-Null;try{$box.SetFocus()}catch{}
 $box.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
 Await {$box.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Expanded} 'dropdown expanded'
 $condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,$name)
 Await {
  foreach($handle in [PolishNative]::OwnWindows($process.Id)){
   try{$surface=[Windows.Automation.AutomationElement]::FromHandle($handle);foreach($candidate in $surface.FindAll([Windows.Automation.TreeScope]::Descendants,$condition)){$pattern=$null;if(-not $candidate.Current.IsOffscreen -and $candidate.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)){$script:choicePattern=$pattern;return $true}}}catch [Windows.Automation.ElementNotAvailableException]{}
  };return $false
 } ('choice '+$id+' '+$name)
 $choicePattern.Select();$box.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse()
}
try {
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --diagnostics --text-scale '+$TextScale;if(-not $NoCapture){$start.Arguments+=' --capture'}
 $process=[Diagnostics.Process]::Start($start);Await {(State).ready} 'ready';$hwnd=[IntPtr][long](State).hwnd;$root=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
 if($DumpTool){if((Get-Process -Id $process.Id).Path -ne [IO.Path]::GetFullPath($Executable)){throw 'Debugger must attach only this owned synthetic test executable'};$debugStart=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($DumpTool));$debugStart.UseShellExecute=$false;$debugStart.WindowStyle='Hidden';$debugStart.CreateNoWindow=$true;$debugStart.RedirectStandardOutput=$true;$debugStart.RedirectStandardError=$true;foreach($argument in @('-accepteula','-nobanner','-ma','-e',[string]$process.Id,$out)){$debugStart.ArgumentList.Add($argument)};$monitor=[Diagnostics.Process]::Start($debugStart);$monitorOut=$monitor.StandardOutput.ReadToEndAsync();$monitorErr=$monitor.StandardError.ReadToEndAsync()}
 [PolishNative]::SetWindowPos($hwnd,[IntPtr]::Zero,0,0,$Width,$Height,0x14)|Out-Null
 Click 'GalleryOpen';Await {(Gallery).itemCount -eq 120 -and -not (Gallery).loading} 'synthetic gallery'
 SelectImage 1;Click 'GalleryClear';Await {-not (Gallery).loading -and (Gallery).itemCount -eq 120} 'reset'
 $action=Find 'GalleryEditTags';if($null -eq $action){$scroll=(Find 'GalleryToolbarScroll').GetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern);if($scroll.Current.VerticallyScrollable){$scroll.SetScrollPercent(-1,100);Await {$null -ne (Find 'GalleryEditTags')} 'reset tools in scrolled toolbar'};$action=Find 'GalleryEditTags'};Check ($null -ne $action) 'selection actions remain present after clearing filters; narrow toolbar may scroll'
 if($Baseline){throw 'Expected baseline regression was not reproduced'}
 Check (-not $action.Current.IsEnabled) 'unselected edit disabled'
 Check ((Find 'GalleryClear').Current.Name -eq '清除筛选') 'reset label says actual action'
 Check ($null -eq (Find 'GalleryViewer')) 'redundant viewer button removed'
 Check ($null -eq (Find 'GalleryFolder')) 'duplicate folder button removed'
 Check ($null -ne (Find 'GalleryHeart1')) 'heart under image'
 SelectImage 1;(Find 'GalleryEditTags').SetFocus();Await {-not (Find 'GalleryEditTags').Current.IsOffscreen} 'selected actions reachable in scrolling toolbar';Check $true 'selection tools remain reachable';Click 'GalleryDetails';Click 'GalleryClear';Await {-not (Gallery).loading} 'clear from information'
 Check (-not (Gallery).inspectorVisible) 'clear from information returns to photos without trapping empty inspector'
 SelectImage 1;Click 'GalleryHeart1';Await {(Gallery).currentFavorite -and -not (Gallery).annotationWriting} 'favorite saved'
 [PolishNative]::SetForegroundWindow($hwnd)|Out-Null;Await {[PolishNative]::GetForegroundWindow() -eq $hwnd} 'own keyboard foreground';(Find 'GalleryStar1_4').SetFocus();Key 0x0D
 Await {(Gallery).currentRating -eq 4 -or (Viewer).open} 'keyboard star action'
 Check (-not (Viewer).open) 'Enter on a star edits rating without opening viewer'
 Await {(Gallery).currentRating -eq 4 -and -not (Gallery).annotationWriting} 'rating saved'
 if($TextScale -eq 100){
  Await {$s=State;return $NoCapture -or $s.revision -eq $s.captureRevision} 'physical click frame settled'
  $star=(Find 'GalleryStar1_2').Current.BoundingRectangle;$px=[int]($star.Left+$star.Width/2);$py=[int]($star.Top+$star.Height/2)
  Check ([PolishNative]::OwnPoint($process.Id,$px,$py)) 'double tap targets only own star';[PolishNative]::MovePointer($px,$py);Start-Sleep -Milliseconds 50
  for($i=0;$i -lt 2;$i++){Check ([PolishNative]::OwnPoint($process.Id,$px,$py)) 'own target before click';[PolishNative]::mouse_event(2,0,0,0,[UIntPtr]::Zero);[PolishNative]::mouse_event(4,0,0,0,[UIntPtr]::Zero);Start-Sleep -Milliseconds 80}
  Await {-not (Gallery).annotationWriting} 'double tap settled';Check (-not (Viewer).open) 'double tap on star does not open photo'
 }
 SelectImage 1;if((Gallery).currentRating -ne 4){Click 'GalleryStar1_4'};Await {(Gallery).currentRating -eq 4 -and -not (Gallery).annotationWriting} 'explicit score'
 Click 'GalleryStar1_4';Await {(Gallery).currentRating -eq 0 -and -not (Gallery).annotationWriting} 'same star clears rating'
 Click 'GalleryNext';Await {-not (Gallery).loading} 'page after annotation'
 Check ((Gallery).error -eq '' -and (Gallery).page -eq 2 -and (Gallery).itemCount -eq 11) 'annotation refreshes pagination cursors'
 Click 'GalleryFirst';Await {-not (Gallery).loading -and (Gallery).itemCount -eq 120} 'back to range first page';SelectImage 1
 Capture 'gallery-comfortable'
 $comfortable=Gallery;$comfortableWidth=(Find 'GalleryImage1').Current.BoundingRectangle.Width
 Click 'NavSettings';Await {$null -ne (Find 'ManagePreferenceDensity')} 'settings'
 Choose 'ManagePreferenceDensity' '紧凑'
 Click 'ManageSavePreferences';Await {-not (State).workspace.management.writing} 'save density';Click 'NavGallery';Await {-not (Gallery).loading -and (Gallery).density -eq 'compact'} 'compact gallery'
 Await {$item=Find 'GalleryImage1';return $null -ne $item -and $item.Current.BoundingRectangle.Width -lt $comfortableWidth} 'density actually changes realized tile width'
 Check ((Gallery).tileWidth -lt $comfortable.tileWidth -or (Gallery).columns -gt $comfortable.columns) 'compact changes tile widths or columns'
 Capture 'gallery-compact'
 SelectImage 1;[PolishNative]::SetForegroundWindow($hwnd)|Out-Null;Await {[PolishNative]::GetForegroundWindow() -eq $hwnd} 'foreground';(Find 'GalleryImage1').SetFocus();Key 0x0D
 Await {(Viewer).displayedId -eq 1} 'Enter opens viewer';Click 'ViewerStar_5';Await {(Viewer).currentRating -eq 5 -and -not (Viewer).annotationWriting} 'viewer rating persists'
 Capture 'viewer-standard'
 if($PanelStress -gt 0){
  for($cycle=0;$cycle -lt $PanelStress;$cycle++){
   Write-Output ('PANEL_CYCLE '+$cycle);$beforeId=(Viewer).currentId;Click $(if($beforeId -eq 1){'ViewerNext'}else{'ViewerPrevious'});Await {(Viewer).currentId -ne $beforeId} 'stress async navigation'
   Key 0x7A;Await {(Viewer).fullscreen} 'stress popup enter';$box=Find 'ViewerSort';$box.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand();Key 0x1B;Await {$box.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Collapsed} 'stress popup close';Key 0x1B;Await {-not (Viewer).fullscreen} 'stress popup exit'
   Click 'ViewerInfo';Click 'ViewerFilmToggle';Key 0x7A;Await {(Viewer).fullscreen} 'stress enter';Key 0x1B;Await {-not (Viewer).fullscreen -and (Viewer).infoVisible -and (Viewer).filmstripVisible} 'stress restore';Click 'ViewerInfo';Click 'ViewerFilmToggle';Await {-not (Viewer).infoVisible -and -not (Viewer).filmstripVisible} 'stress panels closed'
  }
  Click 'ViewerBack';Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe panel stress close';@{result='passed';cycles=$PanelStress;checks=$checks;captureEnabled=(-not $NoCapture);scope='own synthetic viewer; fast focus, panels, F11 and Esc; no deliberate layout sleeps'}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $out 'panel-stress-report.json');Write-Output ('PANEL_STRESS_PASSED '+$PanelStress);return
 }
 Click 'ViewerFullscreen';Await {(Viewer).fullscreen -and (Viewer).immersive} 'immersive fullscreen'
 (Find 'ViewerSurface').SetFocus();Await {-not (Viewer).controlsVisible} 'idle hides controls'
 Check ((Viewer).stageHeight -ge (State).windowHeight-2) 'image stage fills fullscreen height'
 Capture 'viewer-immersive'
 if($PointerWake){$screen=$root.Current.BoundingRectangle;$mx=[int]($screen.Left+$screen.Width/2);$my=[int]($screen.Top+$screen.Height/2);Check ([PolishNative]::OwnPoint($process.Id,$mx,$my) -and [PolishNative]::OwnPoint($process.Id,$mx+20,$my+20)) 'pointer restore targets own fullscreen';[PolishNative]::MovePointer($mx,$my);[PolishNative]::MovePointer($mx+20,$my+20);Await {(Viewer).controlsVisible} 'pointer restores controls'}
 Key 0x27;Await {(Viewer).currentId -eq 2} 'keyboard next in fullscreen'
 $sort=Find 'ViewerSort';$sort.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand();Start-Sleep -Milliseconds 2200;Check ((Viewer).fullscreen -and (Viewer).controlsVisible) 'open dropdown prevents auto hide';Key 0x1B
 Await {$sort.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Collapsed} 'Esc closes dropdown';Check ((Viewer).fullscreen) 'first Esc preserves fullscreen';Key 0x1B;Await {-not (Viewer).fullscreen} 'Esc exits fullscreen'
 Click 'ViewerInfo';Click 'ViewerFilmToggle';Key 0x7A;Await {(Viewer).fullscreen} 'F11 with panels';Key 0x1B;Await {-not (Viewer).fullscreen -and (Viewer).infoVisible -and (Viewer).filmstripVisible} 'exit restores panels';Click 'ViewerInfo';Click 'ViewerFilmToggle'
 Click 'ViewerBack';Await {-not (Viewer).open -and -not (Gallery).loading} 'return to gallery'
 SelectImage 1;Check ((Gallery).currentFavorite -and (Gallery).currentRating -eq 5) 'viewer annotations restored in gallery'
 Choose 'GallerySort' '评分从高到低';Await {-not (Gallery).loading -and (Gallery).ids[0] -eq 1} 'rating order';SelectImage 1
 Click 'GalleryStar1_1';Await {-not (Gallery).annotationWriting -and -not (Gallery).loading -and (Gallery).currentRating -eq 1} 'lower first score'
 $challenger=(Gallery).ids[1];Click ('GalleryStar'+$challenger+'_5');Await {-not (Gallery).annotationWriting -and -not (Gallery).loading -and (Gallery).ids[0] -eq $challenger} 'rating edit reorders page'
 Check ((Gallery).error -eq '') 'rating sort uses fresh query'
 Click 'NavStorage';Check ($null -eq (Find 'StorageImport') -and $null -eq (Find 'StorageLegacyPath') -and $null -eq (Find 'StorageLegacyCachePreview')) 'legacy migration removed from product UI'
 Capture 'data'
 Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe close'
 @{result='passed';checks=$checks;textScale=$TextScale;scope='own synthetic images and isolated data; XAML captures only'}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $out 'photo-polish-report.json')
 Write-Output ('PHOTO_POLISH_PASSED '+$checks)
}catch {State|ConvertTo-Json -Depth 15|Set-Content -LiteralPath (Join-Path $out 'failure-state.json');throw}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null};if($monitor){if(-not $monitor.WaitForExit(5000)){$monitor.Kill();$monitor.WaitForExit()};[IO.File]::WriteAllText((Join-Path $out 'procdump.log'),$monitorOut.GetAwaiter().GetResult());[IO.File]::WriteAllText((Join-Path $out 'procdump-error.log'),$monitorErr.GetAwaiter().GetResult())}}
