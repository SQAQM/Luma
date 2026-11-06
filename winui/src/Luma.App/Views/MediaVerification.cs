using System.Text.Json;
using Luma.Core.Gallery;
using Luma.Core.Contracts;
using Luma.Core.Management;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;

namespace Luma.Preview.Views;
public sealed partial class ShellView
{
 internal async Task VerifyMediaUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_MEDIA_UI_TEST")!="1")return;
  var checks=new List<object>();void Check(bool value,string name){checks.Add(new{passed=value,name});if(!value)throw new InvalidOperationException(name);}
  async Task Until(Func<Task<bool>> test){for(int i=0;i<180;i++){if(await test())return;await Task.Delay(100);}throw new TimeoutException("Own media fixture timed out");}
  async Task Capture(string name){await capture();string frame=Directory.EnumerateFiles(directory,"capture-*.png").MaxBy(File.GetLastWriteTimeUtc)!;File.Copy(frame,Path.Combine(directory,name+".png"),true);}
  try
  {
   Navigate(WorkspacePage.Models);await Until(()=>Task.FromResult(_engine?.Connected==true&&_managementState is not null));
   var client=_engine!.Client!;var state=_managementState!;
   string images=directory+"-fixtures";
   if(!File.Exists(Path.Combine(images,"generated-media-fixture.txt")))throw new InvalidOperationException("Own fixture marker missing");
   Check(await state.WriteAsync("catalog.import",new{path=images,recursive=true}),"own media imported");
   QueryPage gallery=null!;await Until(async()=>{gallery=await client.QueryAsync(new(new Dictionary<string,object?>{{"sort","name"}},null,120));return gallery.Items.Length==3;});
   var items=gallery.Items.Select(GalleryApi.ParseItem).ToArray();
   LiveGallery.SetActive(false);Workspace.Visibility=Rail.Visibility=Visibility.Collapsed;
   foreach(var item in items)
   {
    Management.SuspendForViewer();LiveGallery.SetActive(false);Workspace.Visibility=Rail.Visibility=Visibility.Collapsed;
    _viewer.SetMediaReducedMotion(item.Name.EndsWith(".webp",StringComparison.OrdinalIgnoreCase));
    _viewer.Open(client,_engine.DataDirectory!,item,new(){{"sort","name"}},true);
    await _viewer.VerifyMedia(Check,()=>{Check(Workspace.Visibility==Visibility.Collapsed&&Rail.Visibility==Visibility.Collapsed,"library stays hidden behind viewer");return Capture("viewer-"+Path.GetExtension(item.Name)[1..]);});
    Check(!_viewer.IsOpen,"viewer closes "+Path.GetExtension(item.Name));
   }
   File.WriteAllText(Path.Combine(directory,"media-ui-report.json"),JsonSerializer.Serialize(new{result="passed_media_ui",checks,privateDataRead=false}));
  }
  catch(Exception error){File.WriteAllText(Path.Combine(directory,"media-ui-report.json"),JsonSerializer.Serialize(new{result="failed_media_ui",checks,error=error.ToString()}));}
 }
}
public sealed partial class ViewerView
{
 internal async Task VerifyMedia(Action<bool,string> check,Func<Task> capture)
 {
  async Task Until(Func<bool> test){for(int i=0;i<180;i++){if(test())return;await Task.Delay(50);}throw new TimeoutException("Own media frame not ready: "+_images?.Error);}
  await Until(()=>_images?.Frame is not null&&!_model!.Loading);
  bool animated=_images!.Frame!.Asset.Animated;bool webp=_model!.Current!.Name.EndsWith(".webp",StringComparison.OrdinalIgnoreCase);
  check(_media.Count==(animated?3:2),"native frame count "+_model.Current.Name);
  check(MediaPager.Visibility==Visibility.Visible&&MediaCounter.Text.Length>0,"page navigation visible");
  if(animated)
  {
   check(_media.Playing!=webp,"autoplay follows reduced motion");
   if(webp)TogglePlayback();
   await Until(()=>_media.Index>0);check(_images.Completed>=2,"animation advances decoded pixels");
   PausePlayback();await Task.Delay(150);int paused=_media.Index;await Task.Delay(300);
   check(_media.Index==paused&&!_media.Playing,"pause holds pixels");
   // Two fast requests must resolve to the requested last frame, without blanking pixels.
   while(_media.Index>0){StepMedia(-1);await Until(()=>!_media.Waiting);}
   StepMedia(1);StepMedia(1);check(Picture.Source is not null,"pending frame keeps current pixels");await Until(()=>_media.Index==2&&!_media.Waiting);
   check(_images.Frame!.Asset.Frame==2,"rapid frame requests keep latest");
   _viewport.Actual(XamlRoot?.RasterizationScale??1);_viewport.Rotate();ApplyTransform();double zoom=_viewport.Scale;int rotation=_viewport.Turns;
   TogglePlayback();await Until(()=>_media.Index!=2);PausePlayback();
   check(_viewport.Scale==zoom&&_viewport.Turns==rotation,"animation keeps zoom and rotation");Fit();
  }
  else
  {
   check(!_media.Playing,"TIFF stays on selected page");StepMedia(1);check(Picture.Source is not null,"page load keeps pixels");
   await Until(()=>_media.Index==1&&!_media.Waiting);check(_images.Frame!.Asset.SourceWidth==800&&_images.Frame.Asset.SourceHeight==800,"second TIFF page dimensions");
   check(!NextFrame.IsEnabled&&PreviousFrame.IsEnabled,"page boundary controls");
   Actual();await Until(()=>_images.Frame?.Asset.Kind=="original");check(_images.Frame!.Asset.Frame==1,"original pixels keep selected page");Fit();
  }
  var bounds=MediaPager.TransformToVisual(StageContainer).TransformBounds(new(0,0,MediaPager.ActualWidth,MediaPager.ActualHeight));
  check(bounds.Left>=0&&bounds.Right<=StageContainer.ActualWidth&&bounds.Bottom<=StageContainer.ActualHeight,"pager fits stage");
  check(_images.Bytes<=128L*1024*1024&&_images.CacheCount<=4,"pixel cache stays bounded");
  UpdateLayout();var pictureBounds=Picture.TransformToVisual(StageContainer).TransformBounds(new(0,0,Picture.ActualWidth,Picture.ActualHeight));var dockBounds=ToolDock.TransformToVisual(StageContainer).TransformBounds(new(0,0,ToolDock.ActualWidth,ToolDock.ActualHeight));
  check(pictureBounds.Bottom<=dockBounds.Top+1,"fit picture stays above tool dock");
  await capture();SetFullscreen(true);check(Microsoft.UI.Xaml.Controls.Grid.GetRow(ToolDock)==0,"fullscreen uses overlay controls");SetFullscreen(false);
  CloseViewer();check(!_mediaTimer.IsRunning&&!_media.Playing&&!_playbackTimer.IsRunning,"close stops both playback timers");
 }
}
