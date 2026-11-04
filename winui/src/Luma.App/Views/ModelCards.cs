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
 private JsonElement _modelFacets;private bool _modelInspector;private string? _modelFilterFocus;
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
   var line=new TagChipPanel();var caption=Text(title);caption.FontSize=12*_scale;caption.VerticalAlignment=VerticalAlignment.Center;caption.MinWidth=36;line.Children.Add(caption);
   void Chip(string value,string label,bool all=false)
   {
    var chip=new ToggleButton{Content=label,FontSize=12*_scale,Style=(Style)Resources["ModelFilterChip"],IsChecked=all?selected.Count==0:selected.Contains(value)};
    AutomationProperties.SetAutomationId(chip,"ManageFilter"+key+value);AutomationProperties.SetName(chip,title+"："+label);ToolTipService.SetToolTip(chip,all?"清除此行筛选":"可多选，同一行匹配任一选项");
    chip.Click+=async(_,_)=>{if(!all&&chip.IsChecked==true&&selected.Count>=32){chip.IsChecked=false;Status.Text="同一类最多同时选择32项。";Notify();return;}_modelFilterFocus="ManageFilter"+key+value;if(all)selected.Clear();else if(chip.IsChecked==true)selected.Add(value);else selected.Remove(value);_modelInspector=false;Invalidate();_cursor=null;_history.Clear();await RefreshPage();};line.Children.Add(chip);
    if(_modelFilterFocus=="ManageFilter"+key+value){_modelFilterFocus=null;long gen=_generation;DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low,()=>{if(gen==_generation&&_visible&&_page=="Models"&&chip.XamlRoot is not null)chip.Focus(FocusState.Programmatic);});}
   }
   Chip("All","全部",true);foreach(string value in ModelFacetValues(key,selected))Chip(value,ModelDimensionLabel(value));ModelControls.Children.Add(line);
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
 private void LayoutModels()
 {
  bool compact=ActualWidth<680;bool details=_modelInspector&&(Selected is not null||_sub.Length>0);
  if(_rootChoice is not null){_rootChoice.MinWidth=compact?112:140;_rootChoice.Width=compact?112:160;}
  if(_modelSearch is not null)_modelSearch.Width=compact?148:160;
  var template=(DataTemplate)Resources[compact?"CompactModelTemplate":"ModelTemplate"];if(!ReferenceEquals(ModelCards.ItemTemplate,template))ModelCards.ItemTemplate=template;
  if(_modelProgressRow is not null)_modelProgressRow.Visibility=Visibility.Visible;
  ModelControlScroll.Visibility=Visibility.Visible;
  ModelEmpty.Visibility=_models.Length==0&&!details?Visibility.Visible:Visibility.Collapsed;
  RecordList.Visibility=Visibility.Collapsed;ModelCards.Visibility=compact&&details?Visibility.Collapsed:Visibility.Visible;FormScroll.Visibility=details?Visibility.Visible:Visibility.Collapsed;FormScrollEdge.Visibility=Visibility.Collapsed;
  ListColumn.Width=details&&!compact?new(_models.Length==1?Math.Min(240,ActualWidth*.32):Math.Max(180,ActualWidth-Math.Clamp(ActualWidth*.4,270,360)-18)):new(1,GridUnitType.Star);
  ListRow.Height=new(1,GridUnitType.Star);FormRow.Height=new(0);Grid.SetColumnSpan(ModelCards,details&&!compact?1:2);Grid.SetRow(FormScroll,0);Grid.SetColumn(FormScroll,compact?0:1);Grid.SetColumnSpan(FormScroll,compact?2:1);
  ModelControlScroll.MaxHeight=Math.Clamp(ActualHeight*.43,100,248);
  double height=Math.Clamp(Body.ActualHeight-72*_scale,76,168);foreach(var row in ModelCards.Items.OfType<ManagementRow>())row.CoverHeight=height;
  if(_linkedHost is not null)foreach(var grid in _linkedHost.Children.OfType<GridView>()){grid.Height=LinkedModelHeight();foreach(var tile in grid.Items.OfType<GalleryTile>())tile.ImageHeight=Math.Clamp(grid.Height-12*_scale-30,24,96);}
 }
}
