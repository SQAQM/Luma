using System.Collections.ObjectModel;using System.Diagnostics;
using Luma.Core.Viewer;using Luma.Core.Gallery;using Luma.Core.Engine;using Luma.Preview.Services;
using Microsoft.UI.Xaml;using Microsoft.UI.Xaml.Controls;using Microsoft.UI.Xaml.Input;using Microsoft.UI.Xaml.Media;using Microsoft.UI.Xaml.Automation;
using Windows.System;using Windows.Foundation;
namespace Luma.Preview.Views;
public sealed partial class ViewerView:UserControl,IDisposable
{
 private ViewerModel? _model;private ViewerImages? _images;private ImageSourceProvider? _thumbs;private WindowsShellActions _shell=new(true);
 private readonly Viewport _viewport=new();private readonly ObservableCollection<GalleryTile> _tiles=[];private IReadOnlyList<GalleryItem>? _shownWindow;private GalleryDetail? _shownDetail;private ViewerFrame? _shownFrame;
 private bool _sync,_info,_disposed,_drag,_actualRequested;private Point _last;private string _mode="standard";private double _scale=1;
 private int _pressCount,_moveCount,_releaseCount;private string _pointerKind="";private bool _leftPressed,_captureAccepted;
 private readonly string[] _sorts=["name","name_desc","newest","oldest","modified_desc","modified_asc","size_desc","size_asc","resolution_desc","resolution_asc","rating_desc","rating_asc"];
 private readonly string[] _sortNames=["名称 A–Z","名称 Z–A","最新添加","最早添加","最近修改","最早修改","大小从大到小","大小从小到大","分辨率从高到低","分辨率从低到高","评分从高到低","评分从低到高"];
 private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer _heartbeat;private readonly Stopwatch _clock=new();private readonly List<double> _stalls=[];private object? _lastMetrics;private double _maxHeartbeat;private long _heartbeatCount;
 public bool IsOpen=>Visibility==Visibility.Visible;public bool Fullscreen{get;private set;}
 internal bool MetadataFailureOnce{get;set;}
 public event Action? Changed;public event Action? AnnotationChanged;public event Action<GalleryItem?>? BackRequested;public event Action<bool>? FullscreenRequested;public event Action<long>? TagRequested;
 public object Snapshot=>new{open=IsOpen,fullscreen=Fullscreen,immersive=Fullscreen,controlsVisible=_controlsVisible,currentFavorite=_favorite,currentRating=_rating,annotationWriting=_annotationWriting,viewportWidth=ActualWidth,viewportHeight=ActualHeight,stageWidth=Stage.ActualWidth,stageHeight=Stage.ActualHeight,filmstripVisible=Filmstrip.Visibility==Visibility.Visible,input=new{pressed=_pressCount,moved=_moveCount,released=_releaseCount,kind=_pointerKind,leftPressed=_leftPressed,captured=_captureAccepted,dragging=_drag},currentId=_model?.Current?.Id,displayedId=_images?.Frame?.Item.Id,neighborCount=_model?.Window.Count??0,previousId=_model?.PreviousId,nextId=_model?.NextId,total=_model?.Total??0,infoVisible=_info,zoom=_viewport.Scale,panX=_viewport.X,panY=_viewport.Y,rotation=_viewport.Turns,isFit=_viewport.IsFit,kind=_images?.Frame?.Asset.Kind,mode=_mode,decodedBytes=_images?.Bytes??0,cacheCount=_images?.CacheCount??0,detailError=_model?.DetailError??"",promptLength=_model?.Detail?.Prompt.Length??0,negativeLength=_model?.Detail?.NegativePrompt.Length??0,lastCopyLength=_shell.LastCopyLength,lastAction=_shell.LastAction,error=_images?.Error??_model?.Error??"",metrics=Metrics()};
 private object? Metrics()=>_images is null?_lastMetrics:new{latenciesMs=_images.Latencies.ToArray(),heartbeatIntervalsMs=_stalls.ToArray(),maxHeartbeatMs=_maxHeartbeat,heartbeatCount=_heartbeatCount,completed=_images.Completed,cacheHits=_images.CacheHits,discarded=_images.Discarded,decodedBytes=_images.Bytes};
 public ViewerView(){InitializeComponent();Visibility=Visibility.Collapsed;Pixels.Clip=new RectangleGeometry();
  InitializeImmersive();Reactions.ChangeRequested+=ChangeAnnotation;
  GotFocus+=(_,e)=>{if(e.OriginalSource is Button button)Controls.ScrollFocus.Request(button);};
  Sort.ItemsSource=_sortNames;Filmstrip.ItemsSource=_tiles;SizeChanged+=(_,_)=>LayoutInfo();
  _heartbeat=DispatcherQueue.CreateTimer();_heartbeat.Interval=TimeSpan.FromMilliseconds(40);_heartbeat.Tick+=(_,_)=>{double ms=_clock.Elapsed.TotalMilliseconds;_clock.Restart();_maxHeartbeat=Math.Max(_maxHeartbeat,ms);_heartbeatCount++;if(_stalls.Count>=512)_stalls.RemoveAt(0);_stalls.Add(ms);};
  AddKey(VirtualKey.Left,()=>_=_model?.MoveAsync(-1));AddKey(VirtualKey.Right,()=>_=_model?.MoveAsync(1));AddKey(VirtualKey.F11,()=>FullscreenRequested?.Invoke(!Fullscreen));AddKey(VirtualKey.Escape,()=>{if(Fullscreen)FullscreenRequested?.Invoke(false);else CloseViewer();});
  AddKey(VirtualKey.Number0,Fit);AddKey(VirtualKey.Number1,()=>Actual());AddKey(VirtualKey.I,()=>ToggleInfo());
  Stage.RightTapped+=(_,e)=>{if(!IsOpen||_model?.Detail is not{} detail)return;RestoreControls();var menu=new MenuFlyout();_contextMenu=menu;menu.Closed+=(_,_)=>{if(ReferenceEquals(_contextMenu,menu))_contextMenu=null;RestartIdle();};foreach(var pair in new[]{("所在位置","select"),("打开原图","open"),("复制路径","copy")}){var item=new MenuFlyoutItem{Text=pair.Item1};item.Click+=async(_,_)=>await FileAction(pair.Item2,detail);menu.Items.Add(item);}menu.ShowAt(Stage,e.GetPosition(Stage));e.Handled=true;};
 }
 private void AddKey(VirtualKey key,Action action){var shortcut=new KeyboardAccelerator{Key=key};shortcut.Invoked+=(_,e)=>{if(!IsOpen)return;RestoreControls();if(key==VirtualKey.Escape&&CloseOverlay()){e.Handled=true;return;}var focus=FocusManager.GetFocusedElement(XamlRoot);bool global=key is VirtualKey.F11 or VirtualKey.Escape;if(!global&&(focus is TextBox||focus is ComboBox||focus is TextBlock{IsTextSelectionEnabled:true}))return;action();e.Handled=true;};KeyboardAccelerators.Add(shortcut);}
 internal void Configure(double scale){_scale=scale;InfoTitle.FontSize=PositiveLabel.FontSize=NegativeLabel.FontSize=MetadataError.FontSize=14*scale;InfoTitle.FontWeight=Microsoft.UI.Text.FontWeights.SemiBold;ModelName.FontSize=12*scale;foreach(var button in new[]{CopyPrompt,CopyNegative,RetryDetails,OpenOriginal,CopyPath})button.FontSize=14*scale;LayoutInfo();}
 internal async void Open(EngineClient client,string data,GalleryItem item,Dictionary<string,object?> filters,bool diagnostics){
  if(_disposed)return;Release();_shell=new(diagnostics);_mode="standard";_actualRequested=false;_info=false;_film=false;Feedback.Text="";Filmstrip.Visibility=Visibility.Collapsed;_stalls.Clear();_maxHeartbeat=0;_heartbeatCount=0;_lastMetrics=null;_shownFrame=null;_shownDetail=null;_shownWindow=null;
  IViewerApi api=new ViewerApi(client);if(MetadataFailureOnce){api=new MetadataFailureOnceApi(api);MetadataFailureOnce=false;}_model=new(api);_images=new(client,data);_thumbs=new(client,data);_model.Changed+=Update;_images.Changed+=ImageChanged;_thumbs.Changed+=ThumbChanged;
  Visibility=Visibility.Visible;_clock.Restart();_heartbeat.Start();_sync=true;Sort.SelectedIndex=Math.Max(0,Array.IndexOf(_sorts,filters.GetValueOrDefault("sort")?.ToString()??"name"));_sync=false;
  LayoutInfo();RestoreControls();var model=_model;await model.OpenAsync(item,filters);if(IsOpen&&ReferenceEquals(_model,model))Focus(FocusState.Programmatic);
 }
 private void ThumbChanged(){if(IsOpen)Changed?.Invoke();}
 private void Update(){
  if(!IsOpen||_model is null)return;
  OpenOriginal.IsEnabled=CopyPath.IsEnabled=_model.Detail is not null;SyncAnnotations();
  MetadataError.Visibility=RetryDetails.Visibility=_model.DetailError.Length>0?Visibility.Visible:Visibility.Collapsed;if(_model.DetailError.Length>0)Feedback.Text="生成信息读取失败，打开信息栏可重试。";
  if(_model.Current is{} current){FileName.Text=current.Name;ToolTipService.SetToolTip(FileName,current.Name);_images?.Show(current,_mode,_model.Window);}
  else{Picture.Source=null;FileName.Text="当前范围没有图片";}
  Previous.IsEnabled=_model.PreviousId is not null;Next.IsEnabled=_model.NextId is not null;
  if(!ReferenceEquals(_shownWindow,_model.Window)){
   _shownWindow=_model.Window;_sync=true;_thumbs?.Reset();_tiles.Clear();foreach(var item in _model.Window)_tiles.Add(new GalleryTile(item){LabelSize=12*_scale});Filmstrip.SelectedItem=_tiles.FirstOrDefault(t=>t.Item.Id==_model.Current?.Id);_sync=false;
  }
  if(_info&&!ReferenceEquals(_shownDetail,_model.Detail)){
   _shownDetail=_model.Detail;Prompt.ItemsSource=TextChunkRow.Create(_shownDetail?.Prompt??"",_scale);Negative.ItemsSource=TextChunkRow.Create(_shownDetail?.NegativePrompt??"",_scale);CopyPrompt.IsEnabled=(_shownDetail?.Prompt.Length??0)>0;CopyNegative.IsEnabled=(_shownDetail?.NegativePrompt.Length??0)>0;ModelName.Text=_shownDetail is null?"":$"{_shownDetail.Width} × {_shownDetail.Height} · {_shownDetail.Family}\n{_shownDetail.Model}";Tags.Children.Clear();
   if(_shownDetail is not null)foreach(var tag in _shownDetail.Tags){var button=new Button{Content=tag.Name,IsEnabled=tag.Effective,Style=(Style)Application.Current.Resources["LumaChip"],FontSize=12*_scale};button.Click+=(_,_)=>{TagRequested?.Invoke(tag.Id);CloseViewer();};Tags.Children.Add(button);}
  }
  if(_model.Error!="")Feedback.Text="浏览范围载入失败，可返回图库后刷新。";
  Changed?.Invoke();
 }
 private void ImageChanged(){
  if(!IsOpen||_images is null)return;var frame=_images.Frame;Picture.Source=frame?.Image;
  if(!ReferenceEquals(_shownFrame,frame)){
   _shownFrame=frame;if(frame is not null){Picture.Width=frame.Asset.Width;Picture.Height=frame.Asset.Height;_viewport.SetImage(frame.Asset.Width,frame.Asset.Height);if(_actualRequested)_viewport.Actual(XamlRoot?.RasterizationScale??1);ApplyTransform();}
  }
  Progress.IsActive=_images.Loading;Progress.Visibility=_images.Loading?Visibility.Visible:Visibility.Collapsed;
  LoadingBadge.Visibility=frame is null?Visibility.Visible:Visibility.Collapsed;RetryButton.Visibility=_images.Error!=""?Visibility.Visible:Visibility.Collapsed;
  StageMessage.Text=_images.Error!=""?(_images.Error=="not_found"?"文件离线或已移走":"无法显示图片，请重试或打开原图") :"正在读取图片";
  ImageNote.Text=frame is null?"":$"{frame.Asset.SourceWidth} × {frame.Asset.SourceHeight} · "+(frame.Asset.Kind=="original"?"原始像素":frame.Asset.Kind=="compatibility"?"1200px兼容预览":"标准预览")+(frame.Asset.Animated?" · 首帧":"");Changed?.Invoke();
 }
 private void StageResized(object sender,SizeChangedEventArgs e){_viewport.Resize(Stage.ActualWidth,Stage.ActualHeight);Pixels.Clip=new RectangleGeometry{Rect=new Rect(0,0,Stage.ActualWidth,Stage.ActualHeight)};ApplyTransform();}
 private void ApplyTransform(){Canvas.SetLeft(Picture,(_viewport.Width-_viewport.ImageWidth)/2);Canvas.SetTop(Picture,(_viewport.Height-_viewport.ImageHeight)/2);Transform.ScaleX=Transform.ScaleY=_viewport.Scale;Transform.Rotation=_viewport.Turns*90;Transform.TranslateX=_viewport.X;Transform.TranslateY=_viewport.Y;ZoomText.Text=$"{_viewport.Scale*100*(XamlRoot?.RasterizationScale??1):0}%";Changed?.Invoke();}
 private void LayoutInfo(){if(Body is null)return;bool wide=ActualWidth>=1000;string label=_info?"关闭图片信息 (I)":"图片信息 (I)";AutomationProperties.SetName(InfoToggle,label);ToolTipService.SetToolTip(InfoToggle,label);Info.Visibility=_info&&_controlsVisible?Visibility.Visible:Visibility.Collapsed;StageContainer.Visibility=_info&&!wide&&!Fullscreen?Visibility.Collapsed:Visibility.Visible;StageColumn.Width=_info&&!wide&&!Fullscreen?new GridLength(0):new GridLength(1,GridUnitType.Star);InfoColumn.Width=!_info?new GridLength(0):Fullscreen?new GridLength(Math.Min(340,Math.Max(0,ActualWidth))):wide?new GridLength(340):new GridLength(1,GridUnitType.Star);LayoutImmersive();}
 private void FilmToggleClicked(object s,RoutedEventArgs e){_film=!_film;RestoreControls();LayoutImmersive();AutomationProperties.SetItemStatus(FilmToggle,_film?"已展开":"已收起");Changed?.Invoke();}
 private void ToggleInfo(){_info=!_info;RestoreControls();LayoutInfo();if(_info)Update();Changed?.Invoke();}
 private void Wheel(object sender,PointerRoutedEventArgs e){if(_images?.Frame is null)return;var p=e.GetCurrentPoint(Stage);_viewport.ZoomAt(Math.Pow(1.15,p.Properties.MouseWheelDelta/120.0),p.Position.X,p.Position.Y);ApplyTransform();e.Handled=true;}
 private void Pressed(object sender,PointerRoutedEventArgs e){_pressCount++;_pointerKind=e.Pointer.PointerDeviceType.ToString();if(e.Pointer.PointerDeviceType==Microsoft.UI.Input.PointerDeviceType.Touch)return;var p=e.GetCurrentPoint(Stage);_leftPressed=p.Properties.IsLeftButtonPressed;if(!_leftPressed||_images?.Frame is null)return;_drag=Stage.CapturePointer(e.Pointer);_captureAccepted=_drag;_last=p.Position;e.Handled=true;Changed?.Invoke();}
 private void Moved(object sender,PointerRoutedEventArgs e){_moveCount++;if(!_drag)return;var p=e.GetCurrentPoint(Stage).Position;_viewport.Pan(p.X-_last.X,p.Y-_last.Y);_last=p;ApplyTransform();e.Handled=true;}
 private void Released(object sender,PointerRoutedEventArgs e){_releaseCount++;if(!_drag)return;_drag=false;Stage.ReleasePointerCaptures();Changed?.Invoke();}
 private void Manipulated(object sender,ManipulationDeltaRoutedEventArgs e){if(_images?.Frame is null)return;if(Math.Abs(e.Delta.Scale-1)>.00001)_viewport.ZoomAt(e.Delta.Scale,e.Position.X,e.Position.Y);if(e.PointerDeviceType==Microsoft.UI.Input.PointerDeviceType.Touch)_viewport.Pan(e.Delta.Translation.X,e.Delta.Translation.Y);ApplyTransform();e.Handled=true;}
 private void StageDoubleTapped(object sender,DoubleTappedRoutedEventArgs e){if(_viewport.IsFit)Actual();else Fit();e.Handled=true;}
 private void Actual(){_actualRequested=true;if(_mode!="original"){_mode="original";if(_model?.Current is{} item)_images?.Show(item,_mode,_model.Window);Feedback.Text="100%表示当前预览像素；兼容预览会明确标注。";}else{_viewport.Actual(XamlRoot?.RasterizationScale??1);ApplyTransform();}}
 private void FilmChanging(ListViewBase sender,ContainerContentChangingEventArgs e){if(e.Item is not GalleryTile tile)return;AutomationProperties.SetAutomationId(e.ItemContainer,"ViewerFilm"+tile.Item.Id);AutomationProperties.SetName(e.ItemContainer,tile.Item.Name);ToolTipService.SetToolTip(e.ItemContainer,tile.Item.Name);_thumbs?.Visible(tile,!e.InRecycleQueue);}
 private void FilmClicked(object sender,ItemClickEventArgs e){if(!_sync&&e.ClickedItem is GalleryTile tile)_=_model?.JumpAsync(tile.Item);}
 internal void SetFullscreen(bool value){if(value!=Fullscreen){if(value){_savedInfo=_info;_savedFilm=_film;_info=false;_film=false;}else{_info=_savedInfo;_film=_savedFilm;}Fullscreen=value;}string label=value?"退出全屏 (Esc)":"全屏 (F11)";AutomationProperties.SetName(FullButton,label);ToolTipService.SetToolTip(FullButton,label);RestoreControls();LayoutInfo();if(_info)Update();Changed?.Invoke();}
 private void PreviousClicked(object s,RoutedEventArgs e)=>_=_model?.MoveAsync(-1);private void NextClicked(object s,RoutedEventArgs e)=>_=_model?.MoveAsync(1);
 private void ZoomInClicked(object s,RoutedEventArgs e){_viewport.ZoomAt(1.25,_viewport.Width/2,_viewport.Height/2);ApplyTransform();}private void ZoomOutClicked(object s,RoutedEventArgs e){_viewport.ZoomAt(.8,_viewport.Width/2,_viewport.Height/2);ApplyTransform();}
 private void Fit(){_actualRequested=false;_viewport.Fit();ApplyTransform();}
 private void FitClicked(object s,RoutedEventArgs e)=>Fit();private void ActualClicked(object s,RoutedEventArgs e)=>Actual();private void RotateClicked(object s,RoutedEventArgs e){_viewport.Rotate();ApplyTransform();}
 private void FullClicked(object s,RoutedEventArgs e)=>FullscreenRequested?.Invoke(!Fullscreen);private void InfoClicked(object s,RoutedEventArgs e)=>ToggleInfo();private void BackClicked(object s,RoutedEventArgs e)=>CloseViewer();private void RetryClicked(object s,RoutedEventArgs e)=>_images?.Retry();
 private void SortChanged(object s,SelectionChangedEventArgs e){if(!_sync&&IsOpen&&Sort.SelectedIndex>=0)_=_model?.SetSortAsync(_sorts[Sort.SelectedIndex]);}
 private void CopyPromptClicked(object s,RoutedEventArgs e)=>CopyText(false);private void CopyNegativeClicked(object s,RoutedEventArgs e)=>CopyText(true);
 private async void RetryDetailsClicked(object s,RoutedEventArgs e){if(_model is not{} model)return;string? identity=model.Current?.Identity;Feedback.Text="正在重试生成信息…";await model.RetryDetailAsync();if(!IsOpen||!ReferenceEquals(_model,model)||model.Current?.Identity!=identity)return;if(model.Detail is not null)Feedback.Text="生成信息已恢复。";Changed?.Invoke();}
 private void CopyText(bool negative){if(!IsOpen||_model?.Detail is not{} detail||_model.Current?.Id!=detail.Id||_model.Current.Identity!=detail.Identity)return;string text=negative?detail.NegativePrompt:detail.Prompt;if(text.Length==0)return;try{_shell.Copy(text,negative?"negative":"prompt");Feedback.Text="已复制";}catch(Exception){Feedback.Text="复制失败，请重试。";}Changed?.Invoke();}
 private async void FileClicked(object s,RoutedEventArgs e){if(_model?.Detail is{} detail)await FileAction((string)((Button)s).Tag,detail);}
 private async Task FileAction(string action,GalleryDetail detail){if(!IsOpen||_model?.Current?.Identity!=detail.Identity)return;try{await _shell.ExecuteAsync(action,detail);if(IsOpen)Feedback.Text=action=="copy"?"已复制路径":"已请求系统打开";}catch(Exception){if(IsOpen)Feedback.Text="文件操作失败，请检查文件是否在线。";}Changed?.Invoke();}
 public void CloseViewer(){if(!IsOpen)return;var current=_model?.Current;if(Fullscreen)FullscreenRequested?.Invoke(false);Visibility=Visibility.Collapsed;_lastMetrics=Metrics();Release();BackRequested?.Invoke(current);Changed?.Invoke();}
 private void Release(){_idle.Stop();_pressedPointers.Clear();_manipulating=false;_contextMenu?.Hide();_contextMenu=null;Sort.IsDropDownOpen=false;_annotationGeneration++;_annotationWriting=false;_annotationEdits.Clear();_annotationFeedbackFor=null;_favorite=false;_rating=0;_heartbeat.Stop();_clock.Stop();_model?.Dispose();_model=null;_images?.Dispose();_images=null;_thumbs?.Dispose();_thumbs=null;Picture.Source=null;_shownFrame=null;_shownDetail=null;_shownWindow=null;_tiles.Clear();Prompt.ItemsSource=Negative.ItemsSource=null;ModelName.Text="";Tags.Children.Clear();_drag=false;Stage.ReleasePointerCaptures();_idle.Stop();}
 public void Dispose(){if(_disposed)return;_disposed=true;Release();SetManagement(null);}
}
