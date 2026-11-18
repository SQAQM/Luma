using System.Text.Json;
using Luma.Core.Contracts;
using Luma.Core.Gallery;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.Foundation;

namespace Luma.Preview.Views;

// Opt-in native layout/focus regression. The caller supplies an isolated profile;
// this probe imports only its own twelve generated, inert PNG gradients.
public sealed partial class ShellView
{
 internal async Task VerifyViewerGlassUi(string directory, Func<Task> capture)
 {
  if (!_diagnostics || Environment.GetEnvironmentVariable("LUMA_VIEWER_GLASS_UI_TEST") != "1") return;
  var checks = new List<object>(); var samples = new List<object>(); var captureErrors = new List<string>();
  void Check(bool passed, string name) => checks.Add(new { passed, name });
  async Task Until(Func<Task<bool>> condition)
  {
   for (int i = 0; i < 200; i++) { if (await condition()) return; await Task.Delay(50); }
   throw new TimeoutException("Own viewer glass fixture timed out");
  }
  async Task Capture(string name)
  {
   try
   {
    await capture();
    string frame = Directory.EnumerateFiles(directory, "capture-*.png").MaxBy(File.GetLastWriteTimeUtc)
     ?? throw new InvalidOperationException("Own viewer glass capture missing");
    File.Copy(frame, Path.Combine(directory, "viewer-glass-" + name + ".png"), true);
   }
   catch (Exception error) { captureErrors.Add(error.Message); Check(false, name + " native capture"); }
  }
  try
  {
   Navigate(WorkspacePage.Models);
   await Until(() => Task.FromResult(_engine?.Connected == true && _managementState is not null));
   string images = Path.Combine(directory + "-fixtures", "viewer-glass"); Directory.CreateDirectory(images);
   for (int i = 0; i < 12; i++) await SyntheticPng(Path.Combine(images, $"glass-{i:00}.png"), i,materialTexture:i==5);
   if (!await _managementState!.WriteAsync("catalog.import", new { path = images, recursive = true }))
    throw new InvalidOperationException("Own viewer glass import failed");
   var client = _engine!.Client!; QueryPage page = null!;
   await Until(async () =>
   {
    page = await client.QueryAsync(new QueryRequest(new Dictionary<string, object?> { { "sort", "name" } }, null, 120));
    return page.Total == 12 && page.Items.Length == 12;
   });
   Management.SuspendForViewer(); LiveGallery.SetActive(false); Workspace.Visibility = Rail.Visibility = Visibility.Collapsed;
   _viewer.Open(client, _engine.DataDirectory!, GalleryApi.ParseItem(page.Items[5]), new() { { "sort", "name" } }, true);
   await _viewer.VerifyViewerGlassUi(Check, samples, Capture);
   foreach(string theme in new[]{"Light","Dark"}){
    RestorePreferences(theme,false);Changed?.Invoke();await Task.Delay(220);
    await _viewer.VerifyGlassAppearanceUi(checks,Capture,theme,theme=="Light");
   }
  }
  catch (Exception error) { checks.Add(new { passed = false, name = "probe completed", error = error.ToString() }); }
  bool passed = checks.Count > 0 && checks.All(c => JsonSerializer.SerializeToElement(c).GetProperty("passed").GetBoolean());
  await File.WriteAllTextAsync(Path.Combine(directory, "viewer-glass-ui-report.json"), JsonSerializer.Serialize(new
  {
   result = passed ? "passed_viewer_glass_ui" : "failed_viewer_glass_ui", checks, samples, captureErrors,
   privateDataRead = false,
   scope = "twelve synthetic PNGs; actual WinUI stage/dock/panel bounds, fullscreen idle timer, native control focus and input ownership; no private library or native pointer-input claim"
  }));
 }
}

public sealed partial class ViewerView
{
 internal async Task VerifyViewerGlassUi(Action<bool, string> check, List<object> samples, Func<string, Task> capture)
 {
  await ViewerUsabilityReady();
  Rect Bounds(FrameworkElement element) => element.TransformToVisual(ViewerRoot).TransformBounds(new(0, 0, element.ActualWidth, element.ActualHeight));
  static bool Same(Rect a, Rect b) => Math.Abs(a.X - b.X) < .1 && Math.Abs(a.Y - b.Y) < .1 && Math.Abs(a.Width - b.Width) < .1 && Math.Abs(a.Height - b.Height) < .1;
  bool Inside(FrameworkElement element)
  {
   Rect rect = Bounds(element);
   return rect.Width > 0 && rect.Height > 0 && rect.Left >= -.1 && rect.Top >= -.1 && rect.Right <= ActualWidth + .1 && rect.Bottom <= ActualHeight + .1;
  }
  static bool Separate(Rect a, Rect b) => a.Right <= b.Left + .1 || b.Right <= a.Left + .1 || a.Bottom <= b.Top + .1 || b.Bottom <= a.Top + .1;
  async Task Settle() { UpdateLayout(); await Task.Delay(300); UpdateLayout(); }
  void Sample(string scenario) => samples.Add(new
  {
   scenario, width = ActualWidth, height = ActualHeight, stage = Bounds(Stage), dock = Bounds(ToolDock),
   header = Bounds(Header), info = _info ? Bounds(Info) : (Rect?)null,
   filmstrip = _film ? Bounds(Filmstrip) : (Rect?)null, controlsVisible = _controlsVisible, zoom = _viewport.Scale
  });
  void VisibleChrome(string mode)
  {
   check(Inside(HeaderMetadata) && Inside(HeaderCommands), mode + " filename and command groups stay onscreen");
   check(Separate(Bounds(HeaderMetadata), Bounds(HeaderCommands)), mode + " filename and commands do not overlap");
   check(Inside(ToolDock), mode + " shared dock stays onscreen");
   check(Separate(Bounds(Header), Bounds(ToolDock)), mode + " header and dock do not overlap");
   check(Bounds(ReactionsHost).Bottom <= Bounds(ToolDock).Bottom + .1, mode + " heart and stars remain at the bottom of the shared dock");
  }

  SetFullscreen(false); Focus(FocusState.Programmatic); await Settle();
  Rect normalStage = Bounds(Stage);
  check(Math.Abs(Stage.ActualHeight - ActualHeight) < .1 && Math.Abs(Stage.ActualWidth - ActualWidth) < .1,
   "ordinary image stage uses the full viewer without reserved chrome rows");
  check(Inside(DockOptions)&&Separate(Bounds(DockOptions),Bounds(DockTools)),"dock command groups stay separate at the current width");
  if(ActualWidth>=652)check(Math.Abs(Bounds(DockOptions).Top-Bounds(DockTools).Top)<8&&ToolDock.ActualHeight<54,"ordinary controls use one slim row");
  VisibleChrome("ordinary"); Sample("ordinary"); await capture("ordinary");

  FilmToggleClicked(FilmToggle, new RoutedEventArgs()); await Settle();
  check(_film && Filmstrip.Visibility == Visibility.Visible && Inside(Filmstrip), "film toggle opens the bounded native strip onscreen");
  check(Same(normalStage, Bounds(Stage)), "film toggle does not resize the image stage");
  check(Separate(Bounds(ToolDock), Bounds(FilmstripSurface)), "shared dock and filmstrip do not overlap");
  check(_tiles.Count <= 9 && _tiles.Select(t => t.Item.Id).SequenceEqual(_model!.Window.Select(i => i.Id)), "floating filmstrip keeps the existing bounded model order");
  VisibleChrome("filmstrip"); Sample("filmstrip"); await capture("filmstrip");

  ToggleInfo(); await Settle();
  check(Info.Visibility == Visibility.Visible && Inside(Info), "information toggle opens a visible bounded panel");
  check(Separate(Bounds(Header), Bounds(Info)), "information panel stays below the filename and command groups");
  check(Prompt.Visibility==Visibility.Collapsed&&Negative.Visibility==Visibility.Collapsed&&CopyNegative.Visibility==Visibility.Collapsed,
   "absent prompt data leaves headings without empty scroll regions or copy buttons");
  if(CanSplitInfo)check(StageContainer.Visibility==Visibility.Visible&&Bounds(Stage).Width>360,"medium window keeps the image beside its information");
  if (StageContainer.Visibility == Visibility.Visible)
   check(Separate(Bounds(ToolDock), Bounds(Info)), "wide information panel does not cover dock controls");
  else check(StageColumn.Width.Value == 0, "narrow information panel keeps its explicit replacement layout");
  if(!CanSplitInfo)
  {
   check(ShowViewerFileActions(Info)&&_contextMenu?.Items.Count==3,"narrow information pane can open the shared file menu");
   CloseOverlay();await Settle();
  }
  Sample("information"); await capture("information");
  ToggleInfo(); await Settle();
  check(Same(normalStage, Bounds(Stage)), "closing information restores the ordinary stage bounds");
  FilmToggleClicked(FilmToggle, new RoutedEventArgs()); await Settle();
  check(!_film && Filmstrip.Visibility == Visibility.Collapsed, "film toggle closes the strip");

  // Exercise the real fullscreen idle callback. Keyboard focus in the native
  // ComboBox owns its arrow keys and must keep its surrounding controls visible.
  SetFullscreen(true); Sort.Focus(FocusState.Keyboard); await Settle();
  check(ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot), Sort) && KeepControls(), "sort keeps native keyboard focus and idle ownership");
  await Task.Delay(2250);
  check(_controlsVisible && ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot), Sort), "fullscreen idle preserves focused sort input");
  Sort.IsDropDownOpen = true; await Settle();
  check(Sort.IsDropDownOpen && KeepControls(), "open sort popup keeps fullscreen controls visible");
  Sort.IsDropDownOpen = false; Focus(FocusState.Programmatic); await Settle();
  FilmToggleClicked(FilmToggle, new RoutedEventArgs()); ToggleInfo(); await Settle();
  Rect fullscreenStage = Bounds(Stage); double zoom = _viewport.Scale, panX = _viewport.X, panY = _viewport.Y; int turns = _viewport.Turns;
  check(Inside(Info),"fullscreen information stays onscreen");
  if(CanSplitInfo)
  {
   check(Inside(Filmstrip)&&Separate(Bounds(Info),Bounds(ToolDock))&&Separate(Bounds(Info),Bounds(FilmstripSurface)),"fullscreen information and bottom overlays do not overlap");
   VisibleChrome("fullscreen");
  }
  else
  {
   check(ToolDock.Visibility==Visibility.Collapsed&&Filmstrip.Visibility==Visibility.Collapsed,"narrow fullscreen information does not obscure focusable dock controls");
   ShowCopyNotice("已复制");await Settle();
   check(FeedbackSurface.Visibility==Visibility.Visible&&Inside(FeedbackSurface),"copy feedback is visible over the narrow information panel");
   Feedback.Text="";
  }
  Sample("fullscreen-visible"); await capture("fullscreen-visible");
  // Pointer/programmatic button focus is returned to the viewer before hiding.
  var idleEvents=new List<object>();var idleClock=System.Diagnostics.Stopwatch.StartNew();
  void TraceIdle(string kind){if(idleEvents.Count<64)idleEvents.Add(new{kind,ms=idleClock.ElapsedMilliseconds,visible=_controlsVisible,focus=FocusManager.GetFocusedElement(XamlRoot)?.GetType().Name,rootFocus=FocusState.ToString(),timer=_idle.IsRunning});}
  RoutedEventHandler traceFocus=(_,e)=>TraceIdle("focus:"+e.OriginalSource?.GetType().Name);
  PointerEventHandler tracePointer=(_,_)=>TraceIdle("pointer");
  Windows.Foundation.TypedEventHandler<Microsoft.UI.Dispatching.DispatcherQueueTimer,object> traceTick=(_,_)=>TraceIdle("tick");
  GotFocus+=traceFocus;AddHandler(PointerMovedEvent,tracePointer,true);_idle.Tick+=traceTick;
  try{Next.Focus(FocusState.Programmatic);RestoreControls();await Task.Delay(2250);await Settle();}
  finally{GotFocus-=traceFocus;RemoveHandler(PointerMovedEvent,tracePointer);_idle.Tick-=traceTick;}
  samples.Add(new{scenario="idle-events",events=idleEvents});
  check(!_controlsVisible && Header.Visibility == Visibility.Collapsed && ToolDock.Visibility == Visibility.Collapsed && Filmstrip.Visibility == Visibility.Collapsed,
   "actual fullscreen idle callback hides the overlay controls");
  check(ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot), this), "idle hide returns overlay focus to the viewer");
  check(Same(fullscreenStage, Bounds(Stage)) && Math.Abs(_viewport.Scale - zoom) < .00001 && _viewport.X == panX && _viewport.Y == panY && _viewport.Turns == turns,
   "fullscreen auto-hide preserves stage geometry, fit, pan and rotation");
  Sample("fullscreen-hidden"); await capture("fullscreen-hidden");
  RestoreControls(); await Settle();
  check(_controlsVisible && Info.Visibility == Visibility.Visible && Filmstrip.Visibility == (CanSplitInfo?Visibility.Visible:Visibility.Collapsed) && Same(fullscreenStage, Bounds(Stage)),
   "restoring controls preserves the same image geometry and panel choices");
  SetFullscreen(false); await Settle();
  check(!_info && !_film && Same(normalStage, Bounds(Stage)), "leaving fullscreen restores saved ordinary panel choices");
  VisibleChrome("restored"); Sample("restored"); await capture("restored");
 }
}
