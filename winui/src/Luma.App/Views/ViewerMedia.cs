using Luma.Core.Viewer;
using Luma.Preview.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace Luma.Preview.Views;
public sealed partial class ViewerView
{
 private readonly SequencePlayback _media=new();private DispatcherQueueTimer _mediaTimer=null!;private bool _mediaReduced;
 private int CurrentMediaFrame=>_model?.Current is{} item&&_images?.Frame?.Item is{} shown&&item.Id==shown.Id&&item.Identity==shown.Identity?_media.RequestedIndex:0;
 private bool IsAnimation=>_media.Animated&&_model?.Current is{} item&&_images?.Frame?.Item is{} shown&&item.Id==shown.Id&&item.Identity==shown.Identity;
 private void InitializeMedia()
 {
  _mediaTimer=DispatcherQueue.CreateTimer();_mediaTimer.IsRepeating=false;
  _mediaTimer.Tick+=(_,_)=>
  {
   if(!IsOpen||_disposed||_images?.Error.Length>0){PausePlayback();return;}
   if(_drag||_manipulating||_contextMenu is not null||Sort.IsDropDownOpen){PausePlayback();return;}
   var next=_media.Due(_playbackTime.Elapsed);
   if(next is{} index)RequestMediaFrame(index);else if(_media.Playing)ScheduleMedia();else SyncPlayback();
  };
 }
 internal void SetMediaReducedMotion(bool reduced){_mediaReduced=reduced;if(reduced&&_media.Playing)PausePlayback();}
 private void MediaFrameChanged(ViewerFrame? frame)
 {
  if(frame is null){_mediaTimer.Stop();_media.Reset();MediaPager.Visibility=Visibility.Collapsed;SyncPlayback();return;}
  var a=frame.Asset;_media.Accept($"{frame.Item.Id}|{frame.Item.Identity}",a.Frame,a.FrameCount,a.Animated,a.RepeatLimit,a.DurationMs,_playbackTime.Elapsed,_mediaReduced);
  MediaPager.Visibility=a.FrameCount>1?Visibility.Visible:Visibility.Collapsed;
  MediaCounter.Text=$"{a.Frame+1} / {a.FrameCount} "+(a.Animated?"帧":"页");
  PreviousFrame.IsEnabled=a.Frame>0;NextFrame.IsEnabled=a.Frame+1<a.FrameCount;
  if(a.Animated){_playback.Stop();_playbackTimer.Stop();}SyncPlayback();ScheduleMedia();
 }
 private void ScheduleMedia(){_mediaTimer.Stop();if(_media.Playing&&!_media.Waiting){_mediaTimer.Interval=TimeSpan.FromMilliseconds(Math.Max(20,_images?.Frame?.Asset.DurationMs??100));_mediaTimer.Start();}}
 private void RequestMediaFrame(int frame){if(_model?.Current is{} item)_images?.Show(item,_mode,_model.Window,frame);}
 private void StepMedia(int direction){PausePlayback();if(_media.Step(direction) is{} frame)RequestMediaFrame(frame);}
 private void PreviousFrameClicked(object sender,RoutedEventArgs args)=>StepMedia(-1);
 private void NextFrameClicked(object sender,RoutedEventArgs args)=>StepMedia(1);
}
