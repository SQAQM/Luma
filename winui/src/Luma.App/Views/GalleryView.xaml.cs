using System.Collections.ObjectModel;
using Luma.Core.Engine;
using Luma.Core.Gallery;
using Luma.Preview.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;
namespace Luma.Preview.Views;

public sealed partial class GalleryView:UserControl,IDisposable
{
    private bool _listMode,_filtersOpen;
    private ListViewBase ActiveImages=>_listMode?Rows:Images;
    private System.Text.Json.JsonElement? _facets;
    private readonly string[] _filterKeys=["model","format","artist","content","prompt"];
    private readonly Dictionary<string,string> _wanted=new(){["model"]="",["format"]="",["artist"]="",["prompt"]=""};
    private int _tokenPage;
    private bool _syncFilter;
    internal bool ThumbnailPressure{get;set;}
    private bool _pressureStarted;
    private static async Task HoldLane(EngineClient client){try{await client.CallAsync("diagnostics.wait",new{milliseconds=300});}catch(Exception){}}
    private GalleryModel? _model;private GalleryApi? _api;private EngineClient? _client;
    private ImageSourceProvider? _images;private WindowsShellActions _shell=new(false);
    private readonly ObservableCollection<GalleryTile> _tiles=[];
    private readonly List<long?> _rootIds=[null];
    private readonly string[] _sortKeys=["name","name_desc","newest","oldest","modified_desc","modified_asc","size_desc","size_asc","resolution_desc","resolution_asc","rating_desc","rating_asc"];
    private readonly string[] _sortNames=["名称 A–Z","名称 Z–A","最新添加","最早添加","最近修改","最早修改","大小从大到小","大小从小到大","分辨率从高到低","分辨率从低到高","评分从高到低","评分从低到高"];
    private IReadOnlyList<GalleryItem>? _shown;private GalleryDetail? _shownDetail;
    private bool _updating,_inspector,_disposed,_active,_ready;private double _scale=1;private long? _tag;private long _shownGeneration=-1;
    private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer _searchTimer;
    private readonly MenuFlyout _menu=new();
    public event Action? Changed;
    public event Action<GalleryItem,Dictionary<string,object?>>? OpenViewerRequested;
    private void OpenViewer(){if(_active&&_model?.Current is{} item)OpenViewerRequested?.Invoke(item,_model.ScopeFilters());}
    private static bool IsInteractiveChild(DependencyObject? node)
    {
        while(node is not null){if(node is Controls.ImageReactions or Microsoft.UI.Xaml.Controls.Primitives.ButtonBase or TextBox or ComboBox)return true;node=VisualTreeHelper.GetParent(node);}return false;
    }
    private void ImagesKeyDown(object sender,KeyRoutedEventArgs e){if(_active&&e.Key==VirtualKey.Enter&&!IsInteractiveChild(e.OriginalSource as DependencyObject)&&!IsInteractiveChild(FocusManager.GetFocusedElement(XamlRoot) as DependencyObject)){OpenViewer();e.Handled=true;}}
    private bool ImagesHaveFocus(){if(XamlRoot is null)return false;var node=FocusManager.GetFocusedElement(XamlRoot) as DependencyObject;while(node is not null){if(ReferenceEquals(node,ActiveImages))return true;node=VisualTreeHelper.GetParent(node);}return false;}
    internal void ReturnFromViewer(GalleryItem? item){SetActive(true);if(item is not null&&_model?.Items.FirstOrDefault(i=>i.Id==item.Id&&i.Identity==item.Identity) is{} original){ActiveImages.SelectedItem=_tiles.FirstOrDefault(t=>t.Item.Id==original.Id);_=_model.SelectAsync(original);}}
    internal void FilterContent(long tag){_tag=tag;ChangeScope();}
    internal long[] SelectedIds=>_model?.SelectedIds.Select(id=>(long)id).Take(120).ToArray()??[];
    public object Snapshot=>new{density=_densityFactor<1?"compact":"comfortable",columns=Images.ItemsPanelRoot is ItemsWrapGrid grid&&grid.ItemWidth>0?Math.Max(1,(int)(ImagesPanel.ActualWidth/grid.ItemWidth)):0,tileWidth=Images.ItemsPanelRoot is ItemsWrapGrid sizes&&double.IsFinite(sizes.ItemWidth)?sizes.ItemWidth:0,tileHeight=Images.ItemsPanelRoot is ItemsWrapGrid heights&&double.IsFinite(heights.ItemHeight)?heights.ItemHeight:0,annotationWriting=_annotationWriting,active=_active,feedback=Feedback.Text,managementWriting=_management?.Writing??false,managementUncertain=_management?.Uncertain??false,managementOutcome=_management?.Outcome,viewMode=_listMode?"list":"grid",modelFilter=_model?.ModelFilter,formatFilter=_model?.FormatFilter,artistFilter=_model?.ArtistFilter,promptTagFilter=_model?.PromptTagFilter,connected=_client?.Connected==true,itemCount=_model?.Items.Count??0,total=_model?.Total??0,page=_model?.PageNumber??1,loading=_model?.Loading==true||_catalogReloading,queryLength=_model?.Query.Length??0,selectedCount=_model?.SelectedIds.Count??0,currentId=_model?.Current?.Id,detailLoading=_model?.DetailLoading??false,detailError=_model?.DetailError??"",detailId=_model?.Detail?.Id,promptLength=_model?.Detail?.Prompt.Length??0,negativeLength=_model?.Detail?.NegativePrompt.Length??0,contentTagCount=_model?.Detail?.Tags.Length??0,tagFilter=_tag,ids=_model?.Items.Select(i=>i.Id).ToArray()??[],realizedContainers=_tiles.Count(t=>ActiveImages.ContainerFromItem(t)is not null),thumbnailQueue=_images?.Queued??0,thumbnailActive=_images?.Active??0,thumbnailCompleted=_images?.Completed??0,thumbnailFailed=_images?.Failed??0,thumbnailBusyRetries=_images?.BusyRetries??0,decodedBytes=_images?.DecodedBytes??0,cacheCount=_images?.CacheCount??0,cacheEvicted=_images?.Evicted??0,preferredThumbnailSize=_preferredThumbnailSize,currentFavorite=_model?.Current?.Favorite??false,currentRating=_model?.Current?.Rating??0,inspectorVisible=Inspector.Visibility==Visibility.Visible,lastAction=_shell.LastAction,lastCopyLength=_shell.LastCopyLength,lastCopyHash=_shell.LastCopyHash,error=_model?.Error??""};
    public GalleryView()
    {
        DoubleTapped+=(_,e)=>{if(IsInteractiveChild(e.OriginalSource as DependencyObject))return;var node=e.OriginalSource as DependencyObject;while(node is not null){if(node is FrameworkElement element&&element.DataContext is GalleryTile){OpenViewer();e.Handled=true;break;}node=VisualTreeHelper.GetParent(node);}};
        InitializeComponent();AddManagementControls();Images.ItemsSource=_tiles;
        ToolbarScroll.GotFocus+=(_,e)=>{if(e.OriginalSource is FrameworkElement element)element.StartBringIntoView(new BringIntoViewOptions{AnimationDesired=false,VerticalAlignmentRatio=.5});};
        Images.AddHandler(UIElement.KeyDownEvent,new KeyEventHandler(ImagesKeyDown),true);Rows.AddHandler(UIElement.KeyDownEvent,new KeyEventHandler(ImagesKeyDown),true);
        InspectorScroll.ViewChanged+=(_,e)=>{if(!e.IsIntermediate&&!_disposed)Changed?.Invoke();};
        _searchTimer=DispatcherQueue.CreateTimer();_searchTimer.Interval=TimeSpan.FromMilliseconds(220);_searchTimer.IsRepeating=false;
        _searchTimer.Tick+=(_,_)=>ChangeScope();
        foreach(var name in _sortNames)SortBox.Items.Add(name);SortBox.SelectedIndex=0;FilterKind.SelectedIndex=0;
        foreach(var pair in new[]{("打开原图","open"),("所在位置","select"),("复制路径","copy")})
        {var item=new MenuFlyoutItem{Text=pair.Item1,Tag=pair.Item2};item.Click+=(_,_)=>RunFile((string)item.Tag);_menu.Items.Add(item);}
        Images.ContextFlyout=_menu;Images.AddHandler(UIElement.RightTappedEvent,new RightTappedEventHandler(ImageRightTapped),true);Rows.ContextFlyout=_menu;Rows.AddHandler(UIElement.RightTappedEvent,new RightTappedEventHandler(ImageRightTapped),true);
        SizeChanged+=(_,_)=>Layout();
        ImagesPanel.SizeChanged+=(_,_)=>Layout();Images.Loaded+=(_,_)=>Layout();
        ToolbarActions.SizeChanged+=(_,_)=>Layout();
        var details=new KeyboardAccelerator{Key=VirtualKey.Enter,Modifiers=VirtualKeyModifiers.Menu};details.Invoked+=(_,e)=>{_inspector=!_inspector;Layout();Changed?.Invoke();e.Handled=true;};KeyboardAccelerators.Add(details);
        var copy=new KeyboardAccelerator{Key=VirtualKey.C,Modifiers=VirtualKeyModifiers.Control};copy.Invoked+=(_,e)=>{if(ImagesHaveFocus()&&_model?.Detail is not null){RunFile("copy");e.Handled=true;}};KeyboardAccelerators.Add(copy);
    }
    internal void Configure(double scale,bool diagnostics)
    {
        _scale=scale;_shell=new(diagnostics);
        DetailName.FontSize=Message.FontSize=14*scale;ModelText.FontSize=Feedback.FontSize=Count.FontSize=12*scale;
        PromptText.FontSize=NegativeText.FontSize=14*scale;ScaleManagement(ManagementActions);
    }
    internal async void Bind(EngineClient client,string data,Luma.Core.Management.ManagementState shared)
    {
        if(ReferenceEquals(_client,client))return;
        Detach();_management=shared;_management.Changed+=UpdateManagement;_client=client;_api=new(client);_model=new(_api);_model.Changed+=Update;
        _images=new(client,data);_images.SetSize(_preferredThumbnailSize);_images.Changed+=ImagesChanged;
        _updating=true;RootsBox.Items.Clear();RootsBox.Items.Add("全部目录");_rootIds.Clear();_rootIds.Add(null);RootsBox.SelectedIndex=0;_updating=false;
        var model=_model;
        try
        {
            var roots=await client.CallAsync("catalog.roots",new{});
            var facets=await client.CallAsync("catalog.facets",new{});
            if(!ReferenceEquals(_model,model)||_disposed)return;
            _updating=true;_facets=facets;
            SetRootOptions(roots,null);
        }
        catch(Exception){if(ReferenceEquals(_model,model))Feedback.Text="目录列表暂不可用，可以继续查看全部图片。";}
        finally{_updating=false;UpdateFilterValues();}
        if(ReferenceEquals(_model,model)){_ready=true;if(_active)await model.RefreshAsync();}
    }
    internal void SetActive(bool value)
    {
        if(_active==value)return;_active=value;
        if(value){_model?.Activate();_images?.Resume();if(_ready&&_catalogDirty)_=ReloadManaged();else if(_ready&&_model is not null&&_model.NeedsRefresh)_= _model.RefreshAsync();else if(_model?.Current is not null&&_model.Detail is null)_=_model.SelectAsync(_model.Current);foreach(var tile in _tiles)if(ActiveImages.ContainerFromItem(tile)is not null)_images?.Visible(tile,true);}
        else{if(_catalogReloading){_catalogDirty=true;_reloadVersion++;_catalogReloading=false;}_searchTimer.Stop();_model?.Deactivate();_images?.Pause();}
        if(value){ApplyPendingSort();if(_galleryJobId.Length>0)_jobPoll?.Start();}else _jobPoll?.Stop();
    }
    private void ImagesChanged(){if(!_disposed)Changed?.Invoke();}
    private void Update()
    {
        if(_disposed||_model is null)return;
        UpdateManagement();
        _updating=true;
        try
        {
            if(!ReferenceEquals(_shown,_model.Items))
            {
                _shown=_model.Items;_images?.Reset();_tiles.Clear();foreach(var item in _shown)_tiles.Add(new(item));
                if(ThumbnailPressure&&!_pressureStarted&&_shown.Count>0&&_client is not null){_pressureStarted=true;for(int i=0;i<14;i++)_=HoldLane(_client);}
                _shownGeneration=_model.Generation;UpdateManagement();
            }
            Previous.IsEnabled=!_annotationWriting&&!_catalogReloading&&_model.CanPrevious;Next.IsEnabled=!_annotationWriting&&!_catalogReloading&&_model.CanNext;CancelButton.IsEnabled=_model.Loading||_galleryJob.CanCancel;
            Count.Text=$"{_model.Total:N0} 项 · 第 {_model.PageNumber} 页 · 已选 {_model.SelectedIds.Count}";
            ToolTipService.SetToolTip(Count,$"本页 {_model.Items.Count} 项，当前范围共 {_model.Total:N0} 项");
            bool loading=_model.Loading||_catalogReloading;Images.IsEnabled=Rows.IsEnabled=!_catalogReloading;
            Ring.IsActive=loading;Ring.Visibility=loading?Visibility.Visible:Visibility.Collapsed;
            MessagePanel.Visibility=loading||_model.Items.Count==0?Visibility.Visible:Visibility.Collapsed;
            Message.Text=loading?"读取中":_model.Error.Length>0?ErrorText(_model.Error):_model.Total==0?"没有图片":"";
            DetailsButton.IsEnabled=_inspector||_model.Current is not null;
            if(!ReferenceEquals(_shownDetail,_model.Detail))
            {
                _shownDetail=_model.Detail;ContentTags.Children.Clear();_tokenPage=0;RenderTokens();
                DetailName.Text=_shownDetail?.Name??(_model.DetailLoading?"正在读取详情…":"未选择图片");
                ModelText.Text=_shownDetail is null?"":$"{_shownDetail.Width} × {_shownDetail.Height} · {_shownDetail.Family}\n{_shownDetail.Model}";
                PromptText.ItemsSource=TextChunkRow.Create(_shownDetail?.Prompt??"",_scale);NegativeText.ItemsSource=TextChunkRow.Create(_shownDetail?.NegativePrompt??"",_scale);
                if(_shownDetail is not null)foreach(var tag in _shownDetail.Tags)
                {
                    var button=new Button{Content=tag.Effective?tag.Name:tag.Name+" · 已失效",IsEnabled=tag.Effective,Style=(Style)Application.Current.Resources["LumaChip"],FontSize=12*_scale};
                    AutomationProperties.SetAutomationId(button,"GalleryTag"+tag.Id);button.Click+=(_,_)=>{_tag=tag.Id;_inspector=false;ChangeScope();};ContentTags.Children.Add(button);
                }
            }
            if(_model.DetailError.Length>0)DetailName.Text=ErrorText(_model.DetailError);
            CopyPrompt.IsEnabled=_shownDetail?.Prompt.Length>0;CopyNegative.IsEnabled=_shownDetail?.NegativePrompt.Length>0;
            foreach(var button in FileActions.Children.OfType<Button>())button.IsEnabled=_shownDetail is not null;
            foreach(var item in _menu.Items.OfType<MenuFlyoutItem>())item.IsEnabled=_shownDetail is not null;
            Layout();
        }
        finally{_updating=false;}
        Changed?.Invoke();
    }
    private void Layout()
    {
        double toolRow=Math.Max(36,ToolbarActions.Children.OfType<FrameworkElement>().Select(e=>e.ActualHeight).DefaultIfEmpty(36).Max());
        double budget=ActualHeight-SearchRow.ActualHeight-Pagination.ActualHeight-36-Math.Min(132,ActualHeight*.5);
        ToolbarScroll.MaxHeight=Math.Max(toolRow,Math.Floor((budget+8)/(toolRow+8))*(toolRow+8)-8);
        bool wide=ActualWidth>=880;bool inspector=_inspector;
        SearchRow.Visibility=Pagination.Visibility=RootsBox.Visibility=Visibility.Visible;
        CancelButton.Visibility=Visibility.Visible;
        Inspector.Visibility=inspector?Visibility.Visible:Visibility.Collapsed;
        ImagesPanel.Visibility=wide||!inspector?Visibility.Visible:Visibility.Collapsed;
        InspectorColumn.Width=inspector&&wide?new GridLength(320):new GridLength(0);
        Grid.SetColumn(Inspector,wide?1:0);Grid.SetColumnSpan(Inspector,wide?1:2);
        AutomationProperties.SetName(DetailsButton,_inspector?"关闭图片信息 (Alt+Enter)":"图片信息 (Alt+Enter)");
        ToolTipService.SetToolTip(DetailsButton,_inspector?"关闭图片信息 (Alt+Enter)":"图片信息 (Alt+Enter)");
        if(!_listMode&&Images.ItemsPanelRoot is ItemsWrapGrid panel){double width=Math.Max(180,ImagesPanel.ActualWidth);int columns=Math.Max(1,(int)(width/(_densityFactor<1?168:240)));panel.ItemWidth=Math.Floor((width-12)/columns);double chrome=22*_scale+48;double available=Math.Max(40,ImagesPanel.ActualHeight-chrome-8);double height=Math.Min(available,Math.Clamp((panel.ItemWidth-24)*(_densityFactor<1?.57:.72),64,_densityFactor<1?180:280));panel.ItemHeight=height+chrome;foreach(var tile in _tiles)tile.ImageHeight=height;}
        foreach(var tile in _tiles)tile.RowHeight=(_densityFactor<1?68:92)*Math.Sqrt(_scale);
        if(ViewButton.Content is FontIcon viewIcon)viewIcon.Glyph=_listMode?"\uE80A":"\uE8FD";AutomationProperties.SetName(ViewButton,_listMode?"网格视图":"列表视图");ToolTipService.SetToolTip(ViewButton,_listMode?"网格视图":"列表视图");FilterFields.Visibility=_filtersOpen&&!(inspector&&!wide)?Visibility.Visible:Visibility.Collapsed;ActiveFilters.Visibility=!(inspector&&!wide)&&ActiveFilters.Children.Count>0?Visibility.Visible:Visibility.Collapsed;
    }
    private void ContainerChanging(ListViewBase sender,ContainerContentChangingEventArgs e)
    {
        if(e.Item is not GalleryTile tile)return;
        if(sender!=ActiveImages){AutomationProperties.SetAutomationId(e.ItemContainer,"");return;}
        if(e.InRecycleQueue){AutomationProperties.SetAutomationId(e.ItemContainer,"");_images?.Visible(tile,false);return;}
        AutomationProperties.SetAutomationId(e.ItemContainer,tile.AutomationId);AutomationProperties.SetName(e.ItemContainer,tile.Name);
        if(e.Phase==0){ScaleFonts(e.ItemContainer);e.RegisterUpdateCallback(ContainerChanging);}
        else {ScaleFonts(e.ItemContainer);ToolTipService.SetToolTip(e.ItemContainer,tile.Name);_images?.Visible(tile,true);}
    }
    private void ScaleFonts(DependencyObject node)
    {if(node is TextBlock text)text.FontSize=ReferenceEquals(text.Style,Application.Current.Resources["LumaCaption"])?12*_scale:14*_scale;for(int i=0;i<VisualTreeHelper.GetChildrenCount(node);i++)ScaleFonts(VisualTreeHelper.GetChild(node,i));}
    private void SelectionChanged(object sender,SelectionChangedEventArgs e)
    {
        if(_updating||_model is null||!ReferenceEquals(sender,ActiveImages))return;
        _model.SetSelection(ActiveImages.SelectedItems.OfType<GalleryTile>().Select(t=>t.Item.Id));
        var tile=e.AddedItems.OfType<GalleryTile>().LastOrDefault()??ActiveImages.SelectedItems.OfType<GalleryTile>().LastOrDefault();_=_model.SelectAsync(tile?.Item);
    }
    private void ImageRightTapped(object sender,RightTappedRoutedEventArgs e)
    {
        for(var node=e.OriginalSource as DependencyObject;node is not null;node=VisualTreeHelper.GetParent(node))
        {
            GalleryTile? tile=node is ContentControl container&&container.Content is GalleryTile content?content:node is FrameworkElement element?element.DataContext as GalleryTile:null;
            if(tile is null)continue;
            if(!ActiveImages.SelectedItems.Contains(tile))ActiveImages.SelectedItem=tile;
            else if(_model?.Current?.Id!=tile.Item.Id)_=_model?.SelectAsync(tile.Item);
            break;
        }
    }
    private void ViewClicked(object sender,RoutedEventArgs e)
    {
        if(_model is null)return;_updating=true;_images?.Pause();_listMode=!_listMode;
        Images.ItemsSource=null;Rows.ItemsSource=null;ActiveImages.ItemsSource=_tiles;
        Images.Visibility=_listMode?Visibility.Collapsed:Visibility.Visible;Rows.Visibility=_listMode?Visibility.Visible:Visibility.Collapsed;
        foreach(var tile in _tiles)if(_model.SelectedIds.Contains(tile.Item.Id))ActiveImages.SelectedItems.Add(tile);
        _updating=false;_images?.Resume();Layout();Changed?.Invoke();
    }
    private void FiltersClicked(object sender,RoutedEventArgs e){_filtersOpen=!_filtersOpen;Layout();Changed?.Invoke();}
    private void FilterKindChanged(object sender,SelectionChangedEventArgs e){if(!_updating){UpdateFilterValues();if(_model is not null)Changed?.Invoke();}}
    private void UpdateFilterValues()
    {
        if(FilterValue is null)return;_syncFilter=true;FilterValue.Items.Clear();FilterValue.Items.Add("不限");
        int index=Math.Clamp(FilterKind.SelectedIndex,0,4);string key=_filterKeys[index];
        if(_facets is System.Text.Json.JsonElement facets&&facets.TryGetProperty(key,out var values))foreach(var value in values.EnumerateArray())FilterValue.Items.Add(value.GetProperty("name").GetString());
        FilterInput.Text=key!="content"?_wanted.GetValueOrDefault(key)??"":"";
        FilterValue.SelectedIndex=-1;_syncFilter=false;
    }
    private void FilterSelectionChanged(object sender,SelectionChangedEventArgs e){if(!_syncFilter&&FilterValue.SelectedItem is string value)FilterInput.Text=value=="不限"?"":value;}
    private void ApplyFilterClicked(object sender,RoutedEventArgs e)=>ApplyFilter(FilterInput.Text);
    private void ApplyFilter(string value)
    {
        string key=_filterKeys[Math.Clamp(FilterKind.SelectedIndex,0,4)];value=value.Trim();if(value=="不限")value="";
        if(value.Length>(key=="prompt"?2048:key=="format"?30:key=="model"?1024:1000)){Feedback.Text="筛选文字过长。";Changed?.Invoke();return;}
        if(key=="content")
        {
            long? candidate=null;
            if(value.Length>0&&_facets is System.Text.Json.JsonElement facets)foreach(var tag in facets.GetProperty("content").EnumerateArray())if(tag.GetProperty("name").GetString()==value)candidate=tag.GetProperty("id").GetInt64();
            if(value.Length>0&&candidate is null){Feedback.Text="请选择已有的内容标签。";Changed?.Invoke();return;}
            _tag=candidate;
        }
        else _wanted[key]=value;
        _filtersOpen=false;ChangeScope();
    }
    private void RenderActiveFilters()
    {
        ActiveFilters.Children.Clear();
        foreach(var pair in _wanted.Where(p=>p.Value.Length>0))
        {
            string key=pair.Key;string label=(key=="model"?"模型":key=="format"?"格式":key=="artist"?"画师":"词项")+" · "+pair.Value+" ×";
            var button=new Button{Content=new TextBlock{Text=label,TextTrimming=TextTrimming.CharacterEllipsis},MaxWidth=250,Style=(Style)Application.Current.Resources["LumaChip"],FontSize=12*_scale};
            ToolTipService.SetToolTip(button,label);button.Click+=(_,_)=>{_wanted[key]="";ChangeScope();};ActiveFilters.Children.Add(button);
        }
        if(_tag is not null){var button=new Button{Content="内容标签筛选 ×",Style=(Style)Application.Current.Resources["LumaChip"],FontSize=12*_scale};button.Click+=(_,_)=>{_tag=null;ChangeScope();};ActiveFilters.Children.Add(button);}
    }
    private void RenderTokens()
    {
        PromptTokens.Children.Clear();var tokens=_shownDetail?.PromptTags??[];
        foreach(string token in tokens.Skip(_tokenPage*100).Take(100))
        {
            var button=new Button{Content=new TextBlock{Text=token,TextTrimming=TextTrimming.CharacterEllipsis},MaxWidth=240,Style=(Style)Application.Current.Resources["LumaChip"],FontSize=12*_scale};
            ToolTipService.SetToolTip(button,token);button.Click+=(_,_)=>{_wanted["prompt"]=token;_inspector=false;ChangeScope();};PromptTokens.Children.Add(button);
            AutomationProperties.SetAutomationId(button,"GalleryPromptToken"+(PromptTokens.Children.Count-1));
        }
        TokensNext.IsEnabled=tokens.Length>100;TokensNext.Content=$"词项 {_tokenPage*100+1}–{Math.Min(tokens.Length,(_tokenPage+1)*100)} / {tokens.Length} · 下一组";
    }
    private void TokensNextClicked(object sender,RoutedEventArgs e){int count=_shownDetail?.PromptTags.Length??0;if(count>100)_tokenPage=(_tokenPage+1)%((count+99)/100);RenderTokens();Changed?.Invoke();}
    private void SearchChanged(object sender,TextChangedEventArgs e){if(!_updating&&_model is not null){_searchTimer.Stop();_searchTimer.Start();}}
    private void SortChanged(object sender,SelectionChangedEventArgs e){if(!_updating&&_model is not null)ChangeScope();}
    private void RootChanged(object sender,SelectionChangedEventArgs e){if(!_updating&&_model is not null)ChangeScope();}
    private void ChangeScope(){if(_model is null||!_active)return;_reloadVersion++;_catalogReloading=false;_catalogDirty=false;Feedback.Text="";RenderActiveFilters();_=_model.ChangeScopeAsync(SearchBox.Text,_sortKeys[Math.Max(0,SortBox.SelectedIndex)],_tag,_rootIds.ElementAtOrDefault(RootsBox.SelectedIndex),_wanted["model"],_wanted["format"],_wanted["artist"],_wanted["prompt"]);}
    private void ClearClicked(object sender,RoutedEventArgs e){_updating=true;SearchBox.Text="";RootsBox.SelectedIndex=_rootIds.IndexOf(null);_tag=null;_inspector=false;foreach(var key in _wanted.Keys.ToArray())_wanted[key]="";_updating=false;_searchTimer.Stop();ChangeScope();}
    private void DetailsClicked(object sender,RoutedEventArgs e){_inspector=!_inspector;Layout();Changed?.Invoke();}
    internal void FocusSearch(){if(!_active)return;_inspector=false;Layout();SearchBox.Focus(FocusState.Keyboard);Changed?.Invoke();}
    private void RefreshClicked(object sender,RoutedEventArgs e){RefreshManagedLibrary();}
    private async void CancelClicked(object sender,RoutedEventArgs e){if(_galleryJob.CanCancel)await CancelGalleryJob();else _model?.Cancel();}
    private void FirstClicked(object sender,RoutedEventArgs e){if(_model is not null)_=_model.RefreshAsync();}
    private void NextClicked(object sender,RoutedEventArgs e){if(_model is not null)_=_model.NextAsync();}
    private void PreviousClicked(object sender,RoutedEventArgs e){if(_model is not null)_=_model.PreviousAsync();}
    private void CopyPromptClicked(object sender,RoutedEventArgs e)=>Copy(_model?.Detail?.Prompt??"","prompt");
    private void CopyNegativeClicked(object sender,RoutedEventArgs e)=>Copy(_model?.Detail?.NegativePrompt??"","negative");
    private void Copy(string value,string kind){if(value.Length==0)return;try{_shell.Copy(value,kind);Feedback.Text=kind=="negative"?"负面提示词已复制":"提示词已复制";}catch(Exception){Feedback.Text="复制失败，请重试。";}Changed?.Invoke();}
    private void FileClicked(object sender,RoutedEventArgs e)=>RunFile((string)((Button)sender).Tag);
    private async void RunFile(string action)
    {
        var item=_model?.Current;if(!_active||item is null||_api is null)return;
        try{var detail=await _api.DetailAsync(item,new EngineContext(),CancellationToken.None);await _shell.ExecuteAsync(action,detail);if(!_disposed){Feedback.Text=action=="copy"?"路径已复制":action=="select"?"已请求资源管理器选中文件":action=="folder"?"已请求打开所在目录":"已请求打开原图";Changed?.Invoke();}}
        catch(Exception){if(!_disposed){Feedback.Text="文件操作失败，文件可能离线或已变化。";Changed?.Invoke();}}
    }
    private static string ErrorText(string code)=>code switch{"stale_cursor"=>"图库已更新，请回到首页刷新。","stale_identity"=>"图片信息已变化，请刷新后重新选择。","cancelled"=>"加载已取消，可以刷新重试。",_=>"读取失败，请检查连接后刷新。"};
    internal void Detach()
    {
        _jobPoll?.Stop();_galleryJobId="";_galleryJob.Select("");
        if(_management is not null)_management.Changed-=UpdateManagement;_management=null;
        _searchTimer.Stop();_ready=false;_model?.Dispose();if(_model is not null)_model.Changed-=Update;_images?.Dispose();
        _client=null;_model=null;_api=null;_images=null;_shown=null;_shownDetail=null;_facets=null;_reloadVersion++;_catalogReloading=false;_catalogDirty=false;
        bool updating=_updating;_updating=true;
        try
        {
            _tiles.Clear();_tag=null;_inspector=false;_filtersOpen=false;_pressureStarted=false;_shownGeneration=-1;
            foreach(var key in _wanted.Keys.ToArray())_wanted[key]="";
            SearchBox.Text="";SortBox.SelectedIndex=0;FilterInput.Text="";FilterValue.SelectedIndex=-1;ActiveFilters.Children.Clear();
            PromptText.ItemsSource=NegativeText.ItemsSource=null;ModelText.Text=Feedback.Text=Count.Text="";DetailName.Text="未选择图片";
            ContentTags.Children.Clear();PromptTokens.Children.Clear();CopyPrompt.IsEnabled=CopyNegative.IsEnabled=false;
            foreach(var button in FileActions.Children.OfType<Button>())button.IsEnabled=false;
            foreach(var item in _menu.Items.OfType<MenuFlyoutItem>())item.IsEnabled=false;
        }
        finally{_updating=updating;}
    }
    public void Dispose(){_disposed=true;_searchTimer.Stop();Detach();}
}
