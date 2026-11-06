using System.Diagnostics;
using Luma.Core.Viewer;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace Luma.Preview.Views;

public sealed partial class ViewerView
{
 private readonly SlideshowClock _playback=new(TimeSpan.FromSeconds(5));
 private readonly Stopwatch _playbackTime=Stopwatch.StartNew();
 private DispatcherQueueTimer _playbackTimer=null!;
 private bool _playbackMoving;
 private void InitializePlayback()
 {
  _playbackTimer=DispatcherQueue.CreateTimer();
  _playbackTimer.Interval=TimeSpan.FromMilliseconds(200);
  _playbackTimer.Tick+=async(_,_)=>
  {
   if(_playbackMoving)return;
   if(!IsOpen||_disposed||_model is not{} model||_images is null||model.Error.Length>0||_images.Error.Length>0)
   {PausePlayback();return;}
   if(_drag||_manipulating||_contextMenu is not null||Sort.IsDropDownOpen){PausePlayback();return;}
   var current=model.Current;var frame=_images.Frame;
   bool ready=!model.Loading&&current is not null&&frame?.Item.Id==current.Id&&frame.Item.Identity==current.Identity;
   bool advance=_playback.Observe(current is null?null:$"{current.Id}|{current.Identity}",ready,model.NextId is not null,_playbackTime.Elapsed);
   if(!_playback.Playing){PausePlayback();return;}
   if(!advance)return;
   _playbackMoving=true;
   try{await model.MoveAsync(1);if(ReferenceEquals(model,_model)&&model.Current?.Identity==current?.Identity&&model.Current?.Id==current?.Id)PausePlayback();}
   catch(OperationCanceledException){PausePlayback();}
   finally{_playbackMoving=false;}
  };
  Unloaded+=(_,_)=>PausePlayback();
  SyncPlayback();
 }
 internal void PausePlayback()
 {
  bool changed=_playback.Playing;_playback.Stop();_playbackTimer?.Stop();SyncPlayback();if(changed)Changed?.Invoke();
 }
 private void TogglePlayback()
 {
  if(_playback.Playing){PausePlayback();return;}
  if(!IsOpen||_model?.Current is null||_model.NextId is null)return;
  _playback.Start();_playbackTimer.Start();RestoreControls();SyncPlayback();Changed?.Invoke();
 }
 private void SyncPlayback()
 {
  if(PlaybackButton is null)return;
  string label=_playback.Playing?"暂停播放 (空格)":"自动播放 (空格)";
  PlaybackIcon.Glyph=_playback.Playing?"\uE769":"\uE768";
  PlaybackButton.IsEnabled=_playback.Playing||IsOpen&&_model?.Current is not null&&_model.NextId is not null;
  AutomationProperties.SetName(PlaybackButton,label);ToolTipService.SetToolTip(PlaybackButton,label);
 }
 private void PlaybackClicked(object sender,RoutedEventArgs args)=>TogglePlayback();
 private void Navigate(int direction){PausePlayback();_=_model?.MoveAsync(direction);}
}
