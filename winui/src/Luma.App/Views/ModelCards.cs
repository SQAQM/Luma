using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Luma.Preview.Services;
using Luma.Preview.Views.Controls;
namespace Luma.Preview.Views;

public sealed partial class ManagementView
{
 private readonly SortedSet<string> _modelTypes=new(StringComparer.Ordinal),_modelFamilies=new(StringComparer.Ordinal);
 private JsonElement _modelFacets;private bool _modelInspector;
 private CancellationTokenSource _coverReads=new();private long _coverEpoch;private int _coverActive,_coverCompleted,_coverFailed;
 private readonly Queue<ManagementRow> _coverQueue=new();private readonly HashSet<ManagementRow> _coverRequested=[],_coverVisible=[];
 private static string ModelDimensions(JsonElement value){var parts=new[]{S(value,"type"),S(value,"family")}.Where(part=>part is not ("" or "Unknown")).Distinct().ToArray();return parts.Length==0?"未分类":string.Join(" · ",parts);}
 private static string ModelDimensionLabel(string value)=>value is "" or "Unknown"?"未知":value;
 private IEnumerable<string> ModelFacetValues(string key,SortedSet<string> chosen)
 {
  var values=_modelFacets.ValueKind==JsonValueKind.Object&&_modelFacets.TryGetProperty(key,out var rows)&&rows.ValueKind==JsonValueKind.Array?rows.EnumerateArray().Where(v=>v.ValueKind==JsonValueKind.String).Select(v=>v.GetString()!).ToArray():[];
  return values.Concat(chosen).Distinct(StringComparer.Ordinal).OrderBy(v=>v=="Unknown"?1:0).ThenBy(v=>v,StringComparer.Ordinal);
 }
 private void BuildModelFilters()
 {
  void Row(string title,string key,SortedSet<string> selected)
  {
   string rowId="ManageFilters"+key;
   var line=ModelControls.Children.OfType<TagChipPanel>().FirstOrDefault(row=>AutomationProperties.GetAutomationId(row)==rowId);
   if(line is null){line=new TagChipPanel();AutomationProperties.SetAutomationId(line,rowId);var caption=Text(title);caption.FontSize=12*_scale;caption.VerticalAlignment=VerticalAlignment.Center;caption.MinWidth=36;line.Children.Add(caption);ModelControls.Children.Add(line);}
   string[] values=ModelFacetValues(key,selected).ToArray();var wanted=values.Select(value=>"ManageFilter"+key+value).Append("ManageFilter"+key+"All").ToHashSet();
   foreach(var obsolete in line.Children.OfType<ToggleButton>().Where(chip=>!wanted.Contains(AutomationProperties.GetAutomationId(chip))).ToArray())line.Children.Remove(obsolete);
   int position=1;
   void Chip(string value,string label,bool all=false)
   {
    string id="ManageFilter"+key+value;var chip=line.Children.OfType<ToggleButton>().FirstOrDefault(item=>AutomationProperties.GetAutomationId(item)==id);
    if(chip is null){
     chip=new ToggleButton{Content=label,FontSize=12*_scale,Style=(Style)Resources["ModelFilterChip"]};AutomationProperties.SetAutomationId(chip,id);AutomationProperties.SetName(chip,title+"："+label);ToolTipService.SetToolTip(chip,all?"清除此行筛选":"可多选，同一行匹配任一选项");
     chip.Click+=async(_,_)=>{if(!all&&chip.IsChecked==true&&selected.Count>=32){chip.IsChecked=false;Status.Text="同一类最多同时选择32项。";Notify();return;}if(all)selected.Clear();else if(chip.IsChecked==true)selected.Add(value);else selected.Remove(value);_modelInspector=false;Invalidate();_cursor=null;_history.Clear();await RefreshPage();};line.Children.Insert(position,chip);
    }
    chip.IsChecked=all?selected.Count==0:selected.Contains(value);position++;
   }
   Chip("All","全部",true);foreach(string value in values)Chip(value,ModelDimensionLabel(value));
  }
  Row("类型","types",_modelTypes);Row("模型系","families",_modelFamilies);
 }
 private Task CloseModelInspector()
 {
  _modelInspector=false;ClearForm();_sub="";Layout();ResumeModelCovers();ModelCards.Focus(FocusState.Programmatic);Notify();return Task.CompletedTask;
 }
 private Task OpenSelectedModelDetails(){if(Records.SelectedItems.Count!=1||Selected is not{} row)return Missing("先选择一个模型查看详情。");Invalidate();RenderSelection(row);return Task.CompletedTask;}
 private async void ModelCardDoubleTapped(object sender,Microsoft.UI.Xaml.Input.DoubleTappedRoutedEventArgs e){if(Records.SelectedItems.Count==1){e.Handled=true;await OpenSelectedModelDetails();}}
 private async void ModelCardKeyDown(object sender,Microsoft.UI.Xaml.Input.KeyRoutedEventArgs e){if(e.Key==Windows.System.VirtualKey.Enter&&Records.SelectedItems.Count==1){e.Handled=true;await OpenSelectedModelDetails();}}
 private void UpdateModelPager()
 {
  bool ready=!_formLoading&&_state is{Writing:false,Uncertain:false};
  foreach(var button in ModelPager.Children.OfType<Button>())button.IsEnabled=ready&&(AutomationProperties.GetAutomationId(button) switch{"ManageModelPrevious"=>_history.Count>0,"ManageModelNext"=>_next is not null,"ManageCancelModelTask"=>_jobs.Any(j=>ModelJob(j)&&ActiveJob(j)),_=>true});
 }
 private void UpdateModelEmpty()
 {
  bool empty=_page=="Models"&&_models.Length==0;ModelEmpty.Visibility=empty?Visibility.Visible:Visibility.Collapsed;
  ModelEmptyTitle.Text=_roots.Length==0?"添加模型目录":_jobs.Any(j=>ModelJob(j)&&ActiveJob(j))?"正在添加模型…":"没有匹配的模型";
  ModelEmptyNote.Text="";
 }
 private void ModelContainerChanging(ListViewBase sender,ContainerContentChangingEventArgs e)
 {
  if(e.Item is not ManagementRow row)return;
  if(e.InRecycleQueue){_coverVisible.Remove(row);return;}
  AutomationProperties.SetAutomationId(e.ItemContainer,"ManageModelCard"+N(row.Value,"id"));AutomationProperties.SetName(e.ItemContainer,row.Name+"，"+row.Meta);QueueModelCover(row);
  if(ModelCards.ItemsPanelRoot is ItemsWrapGrid panel&&!ReferenceEquals(_sizedModelPanel,panel))QueueModelGridLayout();
  if(e.ItemContainer.ContextFlyout is null){var container=e.ItemContainer;var menu=new MenuFlyout();foreach(var action in new[]{("所在位置","select"),("复制路径","copy")}){var item=new MenuFlyoutItem{Text=action.Item1};item.Click+=async(_,_)=>{if(container.Content is ManagementRow current)await ModelFileAction(action.Item2,current.Value);};menu.Items.Add(item);}e.ItemContainer.ContextFlyout=menu;}
 }
 private void QueueModelCover(ManagementRow row)
 {
  if(!_visible||_page!="Models"||!B(row.Value,"cover_available"))return;
  _coverVisible.Add(row);if(row.Cover is not null||!_coverRequested.Add(row))return;
  row.CoverStatus="加载封面…";_coverQueue.Enqueue(row);PumpModelCovers();
 }
 private void PumpModelCovers()
 {
  while(_coverActive<2&&_coverQueue.TryDequeue(out var row)){
   if(!_coverVisible.Contains(row)){_coverRequested.Remove(row);continue;}
   if(_api is null||!_visible||_page!="Models")return;
   _coverActive++;_=LoadModelCover(row,_coverEpoch,_coverReads.Token,_api,_data);
  }
 }
 private async Task LoadModelCover(ManagementRow row,long epoch,CancellationToken token,Luma.Core.Management.ManagementApi api,string data)
 {
  try{
   var asset=await api.ReadAsync("modelCover",new{modelId=N(row.Value,"id")},token);
   if(token.IsCancellationRequested||epoch!=_coverEpoch)return;
   if(asset.ValueKind==JsonValueKind.Object){var image=await ModelAsset.Load(data,asset,token,360);if(token.IsCancellationRequested||epoch!=_coverEpoch)return;row.Cover=image;row.CoverStatus="";_coverCompleted++;}
   else row.CoverStatus="暂无封面";
  }catch(OperationCanceledException){}catch(Exception){if(epoch==_coverEpoch){row.CoverStatus="封面不可用";_coverFailed++;}}
  finally{if(epoch==_coverEpoch){_coverActive--;PumpModelCovers();Notify();}}
 }
 private void ResumeModelCovers(){if(!_visible||_page!="Models")return;foreach(var row in ModelCards.Items.OfType<ManagementRow>())if(ModelCards.ContainerFromItem(row)is not null)QueueModelCover(row);}
 private void ResetModelCovers(){_coverEpoch++;_coverReads.Cancel();_coverReads.Dispose();_coverReads=new();_coverQueue.Clear();_coverVisible.Clear();_coverRequested.Clear();_coverActive=_coverCompleted=_coverFailed=0;foreach(var row in ModelCards.Items.OfType<ManagementRow>())row.Cover=null;}
 private double LinkedModelHeight()=>Math.Clamp((Body.ActualHeight>0?Body.ActualHeight:172)-8,64,164);
 private void InitializeManagementLayout()
 {
  void UpdateStatusVisibility()=>StatusHost.Visibility=Status.Text.Length>0||Resolve.Visibility==Visibility.Visible?Visibility.Visible:Visibility.Collapsed;
  Status.RegisterPropertyChangedCallback(TextBlock.TextProperty,(_,_)=>UpdateStatusVisibility());
  Resolve.RegisterPropertyChangedCallback(VisibilityProperty,(_,_)=>UpdateStatusVisibility());
  ModelCards.SizeChanged+=(_,_)=>QueueModelGridLayout();
  ModelCards.Loaded+=(_,_)=>QueueModelGridLayout();
 }
 private ItemsWrapGrid? _sizedModelPanel;
 private bool _modelGridLayoutQueued;
 private void QueueModelGridLayout()
 {
  if(_modelGridLayoutQueued||_disposed)return;
  _modelGridLayoutQueued=DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low,()=>{
   _modelGridLayoutQueued=false;
   if(_page!="Models"||ModelCards.Visibility!=Visibility.Visible||ModelCards.ItemsPanelRoot is not ItemsWrapGrid panel)return;
   double width=ModelCards.ActualWidth;
   for(DependencyObject? node=panel;node is not null;node=Microsoft.UI.Xaml.Media.VisualTreeHelper.GetParent(node))
    if(node is ScrollViewer scroll&&scroll.ViewportWidth>0){width=Math.Min(width,scroll.ViewportWidth);break;}
   if(width<2)return;
   _sizedModelPanel=panel;
   bool compact=width<438||ActualHeight<466;
   var template=(DataTemplate)Resources[compact?"CompactModelTemplate":"ModelTemplate"];
   if(!ReferenceEquals(ModelCards.ItemTemplate,template))ModelCards.ItemTemplate=template;
   double preferred=compact?210+56*(_scale-1):170+94*(_scale-1);
   int columns=Math.Clamp((int)((width-2)/preferred),1,7);
   double cellWidth=Math.Floor((width-2)/columns);
   double coverHeight=Math.Clamp(cellWidth*.48,82,108);
   double cellHeight=compact?Math.Max(72,Math.Ceiling(50*_scale)+12):coverHeight+16+Math.Ceiling(36*_scale);
   if(panel.MaximumRowsOrColumns!=columns)panel.MaximumRowsOrColumns=columns;
   if(panel.ItemWidth!=cellWidth)panel.ItemWidth=cellWidth;
   if(panel.ItemHeight!=cellHeight)panel.ItemHeight=cellHeight;
   foreach(var row in ModelCards.Items.OfType<ManagementRow>())row.CoverHeight=coverHeight;
  });
 }
 private void LayoutModelToolbar()
 {
  if(_rootChoice is null||_modelSearch is not { Parent: Grid } search||_modelToolbarActions is not{} actions)return;
  double available=ActualWidth;
  if(available<=0)return;
  var label=new TextBlock{Text=_rootChoice.SelectedItem?.ToString()??"全部目录",FontSize=_rootChoice.FontSize,FontFamily=_rootChoice.FontFamily};
  label.Measure(new(double.PositiveInfinity,double.PositiveInfinity));
  double rootWidth=Math.Min(available,Math.Clamp(label.DesiredSize.Width+52,134,260));
  _rootChoice.Width=rootWidth;
  double actionWidth=-8;
  foreach(var child in actions.Children){
   child.Measure(new(double.PositiveInfinity,double.PositiveInfinity));actionWidth+=child.DesiredSize.Width+8;
  }
  actionWidth=Math.Ceiling(actionWidth)+1;
  bool searchBelow=available<rootWidth+120+16;
  bool actionsBelow=searchBelow||available<rootWidth+120+actionWidth+16;
  Grid.SetColumnSpan(_rootChoice,searchBelow?3:1);
  Grid.SetColumn(search,searchBelow?0:1);Grid.SetColumnSpan(search,searchBelow?3:actionsBelow?2:1);
  Grid.SetRow(search,searchBelow?1:0);search.Margin=searchBelow?new(0,6,0,0):new(0);
  Grid.SetColumn(actions,actionsBelow?0:2);Grid.SetColumnSpan(actions,actionsBelow?3:1);
  Grid.SetRow(actions,searchBelow?2:actionsBelow?1:0);actions.Margin=actionsBelow?new(0,6,0,0):new(0);
  actions.Width=Math.Min(actionWidth,available);
 }
 private void LayoutModels()
 {
  bool compact=ActualWidth<680;bool details=_modelInspector&&(Selected is not null||_sub.Length>0);
  LayoutModelToolbar();
  ModelControlScroll.Visibility=Visibility.Visible;
  ModelEmpty.Visibility=_models.Length==0&&!details?Visibility.Visible:Visibility.Collapsed;
  RecordList.Visibility=Visibility.Collapsed;ModelCards.Visibility=compact&&details?Visibility.Collapsed:Visibility.Visible;FormScroll.Visibility=details?Visibility.Visible:Visibility.Collapsed;FormScrollEdge.Visibility=Visibility.Collapsed;
  ListColumn.Width=details&&!compact?new(_models.Length==1?Math.Min(240,ActualWidth*.32):Math.Max(180,ActualWidth-Math.Clamp(ActualWidth*.4,270,360)-18)):new(1,GridUnitType.Star);
  ListRow.Height=new(1,GridUnitType.Star);FormRow.Height=new(0);Grid.SetColumnSpan(ModelCards,details&&!compact?1:2);Grid.SetRow(FormScroll,0);Grid.SetColumn(FormScroll,compact?0:1);Grid.SetColumnSpan(FormScroll,compact?2:1);
  ModelControlScroll.MaxHeight=Math.Clamp(ActualHeight*.43,100,248);
  QueueModelGridLayout();
  if(_linkedHost is not null)foreach(var grid in _linkedHost.Children.OfType<GridView>()){grid.Height=LinkedModelHeight();foreach(var tile in grid.Items.OfType<GalleryTile>())tile.ImageHeight=Math.Clamp(grid.Height-12*_scale-30,24,96);}
 }
}
