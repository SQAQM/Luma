using System.Text.Json;
using System.ComponentModel;
using Luma.Core.Engine;
using Luma.Core.Gallery;
using Luma.Core.Management;
using Luma.Preview.Services;
using Luma.Preview.Views.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
namespace Luma.Preview.Views;

[Microsoft.UI.Xaml.Data.Bindable]
public sealed class ManagementRow(JsonElement value,string label,double fontSize):INotifyPropertyChanged
{
 public JsonElement Value{get;set;}=value;public string Label{get;set;}=label;public double FontSize{get;set;}=fontSize;public double CaptionSize=>FontSize*12/14;public string Name{get;set;}="";public string Meta{get;set;}="";public string State{get;set;}="";public double CardWidth=>Math.Min(220,180*FontSize/14);
 private BitmapImage? _cover;private string _coverStatus="暂无封面";private double _coverHeight=144;
 public BitmapImage? Cover{get=>_cover;set{_cover=value;PropertyChanged?.Invoke(this,new(nameof(Cover)));PropertyChanged?.Invoke(this,new(nameof(CoverPlaceholder)));}}
 public Visibility CoverPlaceholder=>Cover is null?Visibility.Visible:Visibility.Collapsed;
 public string CoverStatus{get=>_coverStatus;set{_coverStatus=value;PropertyChanged?.Invoke(this,new(nameof(CoverStatus)));}}
 public double CoverHeight{get=>_coverHeight;set{if(Math.Abs(_coverHeight-value)<1)return;_coverHeight=value;PropertyChanged?.Invoke(this,new(nameof(CoverHeight)));}}
 public event PropertyChangedEventHandler? PropertyChanged;
}

public sealed partial class ManagementView:UserControl,IDisposable
{
 private EngineClient? _client;private ManagementApi? _api;private ManagementState? _state;private string _data="",_page="Tags",_sub="";
 private string _actionError="";private readonly ManagementReadScope _loads=new();private long _generation=>_loads.Generation;private bool _loading=>_loads.Busy;private bool _formLoading;private int _reading;private CancellationTokenSource _reads=new();private bool _disposed,_setting,_visible;private double _scale=1;
 private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer _poll;
 private readonly ContentControl _settingsGate=new(){HorizontalContentAlignment=HorizontalAlignment.Stretch,IsTabStop=false};private readonly StackPanel _settingsFields=new(){Spacing=12,HorizontalAlignment=HorizontalAlignment.Left};private StackPanel Fields=>_page=="Settings"?_settingsFields:Form;private bool _rebuildingForm;
 private readonly List<Button> _writes=[];private readonly Dictionary<Button,Func<bool>> _enabledWhen=[];private JsonElement[] _definitions=[],_roots=[],_models=[],_jobs=[],_registry=[];private long[] _selection=[];
 private JsonElement? _labelState;private JsonElement? Selected=>Records.SelectedItem is ManagementRow row?row.Value:null;
 private ListViewBase Records=>_page=="Models"?ModelCards:RecordList;
 private TextBox? _name,_description,_match,_note;private NumberBox? _threshold;private CheckBox? _enabled,_remember;private ComboBox? _rootChoice,_kind,_family;private PasswordBox? _token;
 private string? _next;private readonly Stack<string?> _history=new();private string? _cursor;private string _query="";private long? _rootId;
 private long? _tagId;private bool _newTag;private JsonElement? _credential;private JsonElement? _vision;
 private ImageSourceProvider? _linkedProvider;private StackPanel? _linkedHost;
 private long _linkedTotal;private int _linkedCount;
 private bool _suspendedForViewer;private TextBlock? _linkedHeading;private string _associationMode="";
 public event Action<JsonElement>? PreferencesChanged;
 public UIElement? AppearanceContent{get;set;}
 public event Action? OpenSettingsLibraryRequested,StorageRequested;
 public nint WindowHandle{get;set;}
 public event Action? Changed;public event Action? LibraryChanged;
 public event Action<GalleryItem,Dictionary<string,object?>>? OpenViewerRequested;
 public object Snapshot=>new{page=_page,subpage=_sub,associationMode=_associationMode,linkedTotal=_linkedTotal,linkedCount=_linkedCount,newTagDraft=_newTag,loading=_loading||_reading>0,writing=_state?.Writing??false,uncertain=_state?.Uncertain??false,feedback=Status.Text,actionError=_actionError,tagCount=_definitions.Length,rootCount=_roots.Length,modelCount=_models.Length,modelTotal=_modelTotal,modelPage=_history.Count+1,modelRootId=_rootId,modelTaskStatus=_modelProgress?.Text??"",modelTypes=_modelTypes.ToArray(),modelFamilies=_modelFamilies.ToArray(),modelInspector=_modelInspector,modelCoverActive=_coverActive,modelCoversLoaded=_coverCompleted,modelCoversFailed=_coverFailed,jobCount=_jobs.Length,activeJobs=_jobs.Count(j=>S(j,"status") is "queued" or "running"),selectionCount=_selection.Length,labelSnapshotReady=_labelState is not null,modelToolbarVisible=ModelControlScroll.Visibility==Visibility.Visible,selectedModelCount=Records.SelectedItems.Count,modelFileAction=_fileActions.LastAction,registryCount=_registry.Length,credentialConfigured=_credential is{} c&&B(c,"configured"),credentialRemembered=_credential is{} r&&B(r,"remembered")};
 public ManagementView(){InitializeComponent();var refresh=new Microsoft.UI.Xaml.Input.KeyboardAccelerator{Key=Windows.System.VirtualKey.F5};refresh.Invoked+=async(_,e)=>{if(_visible){e.Handled=true;await RefreshFromStart();}};KeyboardAccelerators.Add(refresh);var pane=new Microsoft.UI.Xaml.Input.KeyboardAccelerator{Key=Windows.System.VirtualKey.F6};pane.Invoked+=(_,e)=>{if(_visible&&_page=="Models"){e.Handled=true;var focus=Microsoft.UI.Xaml.Input.FocusManager.GetFocusedElement(XamlRoot) as DependencyObject;bool cards=false;while(focus is not null){if(ReferenceEquals(focus,ModelCards)){cards=true;break;}focus=Microsoft.UI.Xaml.Media.VisualTreeHelper.GetParent(focus);}if(cards)_rootChoice?.Focus(FocusState.Programmatic);else if(ReferenceEquals(Microsoft.UI.Xaml.Input.FocusManager.GetFocusedElement(XamlRoot),_rootChoice))_modelSearch?.Focus(FocusState.Programmatic);else if(ModelCards.ContainerFromIndex(Math.Max(0,ModelCards.SelectedIndex)) is Control item)item.Focus(FocusState.Programmatic);}};KeyboardAccelerators.Add(pane);_settingsGate.Content=_settingsFields;_poll=DispatcherQueue.CreateTimer();_poll.Interval=TimeSpan.FromMilliseconds(750);_poll.Tick+=async(_,_)=>{if(_visible&&!_loading){if(_page=="Jobs")await PollJobs();else if(_page=="Models")await PollModelJobs();}};FormScroll.ViewChanged+=(_,_)=>{FormScrollEdge.Visibility=FormScroll.VerticalOffset>1?Visibility.Visible:Visibility.Collapsed;};FormScroll.GotFocus+=(_,e)=>{if(!_rebuildingForm&&e.OriginalSource is FrameworkElement element)element.StartBringIntoView(new BringIntoViewOptions{AnimationDesired=false,VerticalAlignmentRatio=.5});};SizeChanged+=(_,_)=>Layout();Body.SizeChanged+=(_,_)=>{if(_page=="Models")LayoutModels();};}
 public void Configure(double scale){_scale=scale;Status.FontSize=14*scale;ScopeText.FontSize=ReadIndicator.FontSize=12*scale;Resolve.FontSize=14*scale;}
 public void Bind(EngineClient client,string directory,ManagementState shared)
 {
  if(ReferenceEquals(_client,client))return;Detach();_client=client;_data=directory;_api=new(client);_state=shared;_modelAdd=new(shared);_state.Changed+=StateChanged;if(_visible&&_page=="Settings")ShowPage("Settings");
 }
 public void Detach(){ResetModelCovers();_poll.Stop();Invalidate();_linkedProvider?.Dispose();_linkedProvider=null;if(_state is not null)_state.Changed-=StateChanged;_state=null;_modelAdd=null;_api=null;_client=null;_formLoading=false;_definitions=[];_roots=[];_models=[];_jobs=[];_selection=[];_labelState=null;_credential=null;_token?.Password="";if(_visible&&_page=="Settings"&&!_disposed){SettingsForm();Status.Text=SettingsConnectionFeedback?.Invoke()??"";Notify();}}
 public void ShowPage(string page,long[]? selection=null)
 {
  _suspendedForViewer=false;
  bool same=_page==page&&selection is null;_visible=true;Visibility=Visibility.Visible;
  if(!same){_page=page;_sub="";_cursor=null;_history.Clear();_query="";_labelState=null;}
  if(page=="Models"){_cursor=null;_history.Clear();}
  if(selection is not null){_selection=selection.Take(120).ToArray();_labelState=null;}
  Invalidate();Actions.Visibility=Visibility.Visible;Actions.Children.Clear();ModelControls.Children.Clear();ModelControlScroll.Visibility=page=="Models"?Visibility.Visible:Visibility.Collapsed;ClearForm();Records.ItemsSource=null;if(page=="Models"){BuildModelControls();Layout();}else Actions.Children.Add(Button("刷新","ManageRefresh",RefreshFromStart));Status.Text="正在读取资料…";ScopeText.Text=page=="Settings"?"":page=="Tags"?$"已选 {_selection.Length} 张":page=="Models"?"读取中":"读取中";_poll.Stop();if(page=="Jobs")_poll.Start();if(page=="Settings"&&_api is null){SettingsForm();Status.Text=SettingsConnectionFeedback?.Invoke()??"读取中";Layout();Notify();}else _ = RefreshPage();
 }
 public void Hide(){_modelSearchTimer?.Stop();ResetModelCovers();_linkedProvider?.Pause();if(_token is not null)_token.Password="";_visible=false;Visibility=Visibility.Collapsed;_poll.Stop();Invalidate();}
 internal void SuspendForViewer(){_suspendedForViewer=true;Hide();}
 internal bool IsSuspendedForViewer=>_suspendedForViewer;
 internal void ResumeFromViewer(){if(!_suspendedForViewer)return;_suspendedForViewer=false;_visible=true;Visibility=Visibility.Visible;_linkedProvider?.Resume();if(_page=="Models"){_poll.Start();ResumeModelCovers();}if(_linkedHost is not null)foreach(var grid in _linkedHost.Children.OfType<GridView>())foreach(var tile in grid.Items.OfType<GalleryTile>())if(grid.ContainerFromItem(tile)is not null)_linkedProvider?.Visible(tile,true);Notify();}
 public bool IsShowing(string page)=>_visible&&_page==page;
 private void Invalidate(){_reading=0;ReadIndicator.Opacity=0;_loads.Invalidate();_reads.Cancel();_reads.Dispose();_reads=new();_state?.ChangeScope();}
 private static string S(JsonElement e,string k)=>e.ValueKind==JsonValueKind.Object&&e.TryGetProperty(k,out var v)&&v.ValueKind is JsonValueKind.String?v.GetString()??"":"";
 private static long N(JsonElement e,string k)=>e.ValueKind==JsonValueKind.Object&&e.TryGetProperty(k,out var v)&&v.ValueKind==JsonValueKind.Number&&v.TryGetInt64(out long n)?n:0;
 private static bool B(JsonElement e,string k)=>e.ValueKind==JsonValueKind.Object&&e.TryGetProperty(k,out var v)&&v.ValueKind==JsonValueKind.True;
 private static JsonElement[] Rows(JsonElement value)=>value.ValueKind==JsonValueKind.Array?value.EnumerateArray().Select(e=>e.Clone()).ToArray():[];
 private TextBlock Text(string text,bool heading=false)=>new(){Text=text,TextWrapping=TextWrapping.Wrap,FontSize=(heading?20:14)*_scale,Style=(Style)Application.Current.Resources[heading?"LumaHeading":"LumaBody"]};
 private Button Button(string title,string id,Func<Task> action,bool write=false,Func<bool>? when=null)
 {
  var b=new Button{Content=title,Style=(Style)Application.Current.Resources["LumaButton"],FontSize=14*_scale};AutomationProperties.SetAutomationId(b,id);ToolTipService.SetToolTip(b,title);if(title.StartsWith("刷新",StringComparison.Ordinal))ActionIcons.Refresh(b,title);
  b.Click+=async(_,_)=>{try{await action();}catch(OperationCanceledException){}catch(Exception error){_actionError=error.GetType().Name+":"+error.HResult;Status.Text="操作失败";}finally{if(!_disposed)Notify();}};
  if(write)_writes.Add(b);if(when is not null)_enabledWhen[b]=when;b.IsEnabled=(!write||_state is{Writing:false,Uncertain:false})&&(when?.Invoke()??true);return b;
 }
 private TextBox Input(string label,string id,string value="",int max=1024,bool multiline=false)
 {
  Fields.Children.Add(Text(label));var t=new TextBox{Text=value,MaxLength=max,FontSize=14*_scale,AcceptsReturn=multiline,TextWrapping=multiline?TextWrapping.Wrap:TextWrapping.NoWrap,MinHeight=multiline?80:36,MaxHeight=multiline?160:double.PositiveInfinity};AutomationProperties.SetAutomationId(t,id);AutomationProperties.SetName(t,label);Fields.Children.Add(t);return t;
 }
 private ComboBox Choice(string label,string id,IEnumerable<string> values)
 {
  Fields.Children.Add(Text(label));var c=new ComboBox{FontSize=14*_scale,HorizontalAlignment=HorizontalAlignment.Stretch};foreach(string v in values)c.Items.Add(v);if(c.Items.Count>0)c.SelectedIndex=0;AutomationProperties.SetAutomationId(c,id);AutomationProperties.SetName(c,label);Fields.Children.Add(c);return c;
 }
 private void Buttons(params Button[] buttons){var p=new TagChipPanel();foreach(var b in buttons)p.Children.Add(b);Fields.Children.Add(p);}
 private void EnsureSettingsLayout()
 {
  if(_page!="Settings"||Form.Children.Contains(_settingsGate))return;
  Form.Children.Clear();if(AppearanceContent is not null)Form.Children.Add(AppearanceContent);Form.Children.Add(_settingsGate);
 }
 private void ClearForm()
 {
  _formRevision++;
  _rebuildingForm=true;
  try{
   if(_token is not null){_token.Password="";_token=null;}
   _writes.RemoveAll(b=>!Actions.Children.Contains(b)&&!_modelButtons.Contains(b));foreach(var button in _enabledWhen.Keys.Where(b=>!Actions.Children.Contains(b)&&!_modelButtons.Contains(b)).ToArray())_enabledWhen.Remove(button);
   _linkedProvider?.Reset();_linkedHost=null;_linkedHeading=null;_associationMode="";
   EnsureSettingsLayout();
   if(XamlRoot is not null){var focus=Microsoft.UI.Xaml.Input.FocusManager.GetFocusedElement(XamlRoot) as DependencyObject;while(focus is not null){if(ReferenceEquals(focus,Fields)){FormScroll.Focus(FocusState.Programmatic);break;}focus=Microsoft.UI.Xaml.Media.VisualTreeHelper.GetParent(focus);}}
   Fields.Children.Clear();
  }finally{_rebuildingForm=false;}
 }
 private void Notify(){Records.IsEnabled=!_formLoading&&_state is{Writing:false,Uncertain:false};UpdateModelPager();ModelControlScroll.IsEnabled=!_formLoading&&_modelAdd?.Busy!=true&&_state is{Writing:false,Uncertain:false};FormScroll.IsEnabled=_page=="Settings"||(!_formLoading&&_state is{Writing:false,Uncertain:false});_settingsGate.IsEnabled=!_formLoading&&(_api is null||_state is{Writing:false,Uncertain:false});foreach(var pair in _enabledWhen.Where(pair=>!_writes.Contains(pair.Key)))pair.Key.IsEnabled=!_formLoading&&pair.Value();foreach(var b in _writes)b.IsEnabled=!_formLoading&&_state is{Writing:false,Uncertain:false}&&(!_enabledWhen.TryGetValue(b,out var when)||when());Changed?.Invoke();}
 private void StateChanged(){if(_disposed)return;Status.Text=ModelOperationFeedback();foreach(var b in _writes)b.IsEnabled=_state is{Writing:false,Uncertain:false}&&(!_enabledWhen.TryGetValue(b,out var when)||when());Resolve.Visibility=_state?.Uncertain==true?Visibility.Visible:Visibility.Collapsed;Resolve.IsEnabled=_state?.OperationId.Length==32;Notify();}
 private bool _resolvingOperation;
 private async void ResolveClicked(object sender,RoutedEventArgs e)
 {
  if(_resolvingOperation)return;_resolvingOperation=true;Resolve.IsEnabled=false;
  try{
   var state=_state;if(state is null)return;string action=state.LastAction;var flow=_modelAdd;
   if(flow is not null&&action is "models.add" or "models.scan")await flow.ResolveAsync();else await state.ResolveAsync();
   if(!ReferenceEquals(state,_state))return;
   if(action=="models.add"&&flow?.RootId is not null)AdoptAddedModelRoot(flow);
   if(!state.Uncertain&&state.Outcome=="completed"){
    LibraryChanged?.Invoke();if(action=="settings.save")PreferencesChanged?.Invoke(state.Result);await RefreshPage();
    if(action=="models.add"&&flow?.RootId is not null&&!flow.IndexRequested)Status.Text="已添加";
   }else if(action=="models.add"&&flow?.RootId is not null){await RefreshPage();Status.Text=state.Feedback;}
  }catch(Exception){Status.Text="状态核对失败，请保持当前操作，不要重复提交。";}
  finally{_resolvingOperation=false;Resolve.IsEnabled=_state?.OperationId.Length==32;Notify();}
 }
 private async Task<bool> Write(string action,object args,bool refresh=true)
 {
  var state=_state;if(state is null)return false;long generation=_generation;
  bool ok=await state.WriteAsync(action,args);
  if(ok){LibraryChanged?.Invoke();if(action=="settings.save")PreferencesChanged?.Invoke(state.Result);if(generation==_generation&&_page=="Models"&&action is "models.scan" or "models.sync" or "models.rebuild" or "models.reclassify"){_cursor=null;_history.Clear();}if(refresh&&generation==_generation&&_visible)await RefreshPage();}
  return ok;
 }
 private async Task<JsonElement> Read(string section,object? args=null)
 {
  long gen=_generation;var api=_api??throw new InvalidOperationException();var token=_reads.Token;_reading++;ReadIndicator.Opacity=1;Notify();
  try{return await api.ReadAsync(section,args??new{},token);}finally{if(gen==_generation){_reading=Math.Max(0,_reading-1);ReadIndicator.Opacity=_reading>0?1:0;Notify();}}
 }
 private Task RefreshFromStart(){Invalidate();if(_page=="Models"){_cursor=null;_history.Clear();}return RefreshPage();}
 private async Task RefreshPage()
 {
  if(_api is null||!_visible||_disposed||!_loads.TryRefresh())return;long gen=_generation;_formLoading=true;Notify();
  try
  {
   if(_page=="Tags") {var defs=await Read("tags");var vision=await Read("vision");if(gen!=_generation)return;_definitions=Rows(defs);_vision=vision;}
   if(_page=="Models") {var jobs=await Read("jobs");var roots=await Read("modelRoots");if(gen!=_generation)return;var available=Rows(roots);if(_rootId.HasValue&&!available.Any(r=>N(r,"id")==_rootId))_rootId=null;
    var models=await ReadModelPage();var cred=await Read("credential");if(gen!=_generation)return;_roots=available;_jobs=Rows(jobs);_models=Rows(models.GetProperty("items"));_modelTotal=N(models,"total");_modelFacets=models.TryGetProperty("facets",out var facets)?facets.Clone():default;_next=models.TryGetProperty("next_cursor",out var next)&&next.ValueKind==JsonValueKind.String?next.GetString():null;_credential=cred;UpdateModelPolling();}
   if(_page=="Jobs"){var jobs=await Read("jobs");if(gen!=_generation)return;var nextJobs=Rows(jobs);if(nextJobs.Any(j=>S(j,"status") is "completed" or "completed_with_errors"))LibraryChanged?.Invoke();_jobs=nextJobs;}
   if(_page=="Settings"){var preferences=await Read("settings");var cred=await Read("credential");if(gen!=_generation)return;_preferences=preferences;_credential=cred;}
   if(gen==_generation)Build();
  }
  catch(OperationCanceledException){}
  catch(Exception){if(gen==_generation){Status.Text="资料读取失败，可点击刷新重试。";Notify();}}
  finally{_formLoading=false;_loads.Complete();if(_loads.RefreshPending&&_visible&&!_disposed)_=RefreshPage();else Notify();}
 }
 private void Build()
 {
  _setting=true;try
  {
   bool modelDetails=_page=="Models"&&_modelInspector&&_sub.Length==0;var modelSelection=_page=="Models"?Records.SelectedItems.OfType<ManagementRow>().Select(row=>N(row.Value,"id")).ToHashSet():[];Actions.Visibility=Visibility.Visible;string? selected=(_page!="Models"||_modelInspector)&&Selected is{} old&&old.TryGetProperty("id",out var oldId)?oldId.ToString():null;Actions.Children.Clear();_modelButtons.Clear();ModelControls.Children.Clear();ClearForm();_writes.Clear();
   Actions.Children.Add(Button("刷新","ManageRefresh",RefreshFromStart));
   ScopeText.Text=_page=="Settings"?"":_page=="Tags"?$"已选 {_selection.Length} 张":_page=="Models"?$"{_modelTotal} 个模型 · 第 {_history.Count+1} 页":"";
   JsonElement[] values=_page=="Settings"?[]:_page=="Tags"?_definitions:_page=="Models"?_models:_jobs;
   Records.SelectionMode=_page=="Models"?ListViewSelectionMode.Extended:ListViewSelectionMode.Single;
   Records.ItemTemplate=(DataTemplate)Resources[_page=="Models"?"ModelTemplate":"RecordTemplate"];
   if(_page=="Models")ResetModelCovers();
   Records.ItemsSource=values.Select(v=>new ManagementRow(v,_page=="Tags"?S(v,"name"):_page=="Models"?S(v,"name"):JobText(v),14*_scale){Name=S(v,"model_name").Length>0?S(v,"model_name"):S(v,"name"),Meta=_page=="Models"?ModelDimensions(v):ModelSize(N(v,"size")),State=ModelStatus(v),CoverStatus=CivitaiFeedback.CoverStatus(v)}).ToArray();
   if(_page=="Tags"){
    Actions.Children.Add(Button("新增类别","ManageTagNew",()=>{_newTag=true;_tagId=null;TagForm(null);Notify();return Task.CompletedTask;}));
    Actions.Children.Add(Button("保存类别","ManageTagSave",SaveTag,true));
    {Actions.Children.Add(Button("读取校正快照","ManageLabelPreview",LabelPreview,false,()=>_selection.Length>0));Actions.Children.Add(Button("确认","ManageConfirmTag",()=>ApplyLabel("include"),true,()=>_selection.Length>0&&_labelState is not null&&_tagId is not null&&_definitions.Any(d=>N(d,"id")==_tagId&&B(d,"enabled"))));Actions.Children.Add(Button("排除","ManageExcludeTag",()=>ApplyLabel("exclude"),true,()=>_selection.Length>0&&_labelState is not null&&_tagId is not null));Actions.Children.Add(Button("恢复自动","ManageAutoTag",()=>ApplyLabel("auto"),true,()=>_selection.Length>0&&_labelState is not null&&_tagId is not null));}
    TagForm(_definitions.FirstOrDefault());}
   else if(_page=="Models"){BuildModelControls();ModelsForm(null);}
   else if(_page=="Settings")SettingsForm();else JobsForm(null);
   if(_page=="Models"){foreach(var row in Records.Items.OfType<ManagementRow>())if(modelSelection.Contains(N(row.Value,"id")))Records.SelectedItems.Add(row);}else if(values.Length>0)Records.SelectedIndex=Math.Max(0,Array.FindIndex(values,v=>v.GetProperty("id").ToString()==selected));
   if(_page=="Models"){_modelInspector=modelDetails&&Records.SelectedItems.Count==1;UpdateModelEmpty();ResumeModelCovers();}
   StateChanged();Layout();
  }finally{_setting=false;}
  if(Selected is{} current&&(_page!="Models"||_modelInspector))RenderSelection(current);if(_page=="Models")_=VerifyModelUi();
 }
 private async Task PollJobs(){if(_api is null||!_loads.TryPoll())return;long gen=_generation;try{var result=await Read("jobs");if(gen!=_generation||!_visible||_page!="Jobs")return;var rows=Rows(result);if(rows.Select(j=>j.GetRawText()).SequenceEqual(_jobs.Select(j=>j.GetRawText())))return;string selected=Selected is{} current?S(current,"id"):"";_jobs=rows;_setting=true;try{Records.ItemsSource=_jobs.Select(j=>new ManagementRow(j,JobText(j),14*_scale)).ToArray();Records.SelectedIndex=Array.FindIndex(_jobs,j=>S(j,"id")==selected);}finally{_setting=false;}UpdateJobDetails();if(_jobs.Any(j=>S(j,"status") is "completed" or "completed_with_errors"))LibraryChanged?.Invoke();Notify();}catch(OperationCanceledException){}catch(Exception){Status.Text="任务状态暂不可用，可刷新重试。";Notify();}finally{_loads.Complete();if(_loads.RefreshPending&&_visible&&!_disposed)_=RefreshPage();}}
 private static string JobText(JsonElement j)=>(S(j,"kind") switch{"import"=>"图片索引","file_preview"=>"准备文件预览","organize"=>"分类移动","undo"=>"撤销移动","remove_root"=>"移除目录索引","duplicate_scan"=>"查找完全重复","duplicate_cleanup"=>"重复隔离","duplicate_undo"=>"隔离还原","duplicate_purge"=>"永久清空","duplicate_recovery"=>"重复恢复检查","file_recovery"=>"移动恢复检查","metadata_refresh"=>"重读生成信息","install_model"=>"下载识别模型","classify_contents"=>"本机内容识别","reclassify_models"=>"缓存模型重判","model_scan"=>"模型属性索引","model_sync"=>"模型资料同步","model_links"=>"更新关联","civitai_image_lookup"=>"图片模型哈希查询",_=>"后台任务"})+" · "+(S(j,"status") switch{"queued"=>"等待中","running"=>"运行中","completed"=>"已完成","completed_with_errors"=>"完成，部分错误","cancelled"=>"已取消","interrupted"=>"已中断","failed"=>"失败",_=>"状态未知"})+$"\n{N(j,"processed")} / {N(j,"total")} · 错误 {N(j,"errors")}";
 private void RecordChanged(object sender,SelectionChangedEventArgs e){if(_setting)return;if(_page=="Models"){if(_modelInspector&&_sub.Length==0&&Records.SelectedItems.Count==1&&Selected is{} model){Invalidate();RenderSelection(model);}else if(_modelInspector)_=CloseModelInspector();else Notify();return;}if(Selected is{} value){Invalidate();RenderSelection(value);}}
 private void RenderSelection(JsonElement value){if(_page=="Tags"){_newTag=false;_tagId=N(value,"id");TagForm(value);}else if(_page=="Models"){_modelInspector=true;ModelsForm(value);}else JobsForm(value);StateChanged();}
 private void TagForm(JsonElement? item)
 {
  if(item is{} empty&&empty.ValueKind!=JsonValueKind.Object)item=null;
  ClearForm();_tagId=item is{} v&&v.ValueKind==JsonValueKind.Object?N(v,"id"):null;
  _name=Input("类别名称","ManageTagName",item is{} t?S(t,"name"):"",100);
  _description=Input("标签描述","ManageTagDescription",item is{} d?S(d,"description"):"",500,true);

  _threshold=new NumberBox{Value=item is{} f&&f.TryGetProperty("threshold",out var th)?th.GetDouble():.28,Minimum=.2,Maximum=.9,SmallChange=.01,FontSize=14*_scale,Header="相似度阈值（非概率）"};AutomationProperties.SetAutomationId(_threshold,"ManageTagThreshold");Fields.Children.Add(Text("相似度阈值（非概率）"));_threshold.Header=null;Fields.Children.Add(_threshold);
  _enabled=new CheckBox{Content="启用类别",IsChecked=item is{} x?B(x,"enabled"):true,FontSize=14*_scale};AutomationProperties.SetAutomationId(_enabled,"ManageTagEnabled");Fields.Children.Add(_enabled);
  Fields.Children.Add(Text($"人工校正 · {_selection.Length} 张图片",true));
  if(_labelState is{} state){var tag=state.GetProperty("tags").EnumerateArray().FirstOrDefault(r=>N(r,"id")==_tagId);if(tag.ValueKind==JsonValueKind.Object)Fields.Children.Add(Text($"人工确认 {N(tag,"manual_count")} · 排除 {N(tag,"excluded_count")} · 有效 {N(tag,"effective_count")}；未操作类别保持原样。"));}
  Fields.Children.Add(Text(_vision is{} status?S(status,"message"):"正在读取本机模型状态"));
  Buttons(Button("下载公开识别模型","ManageInstallVision",()=>ConfirmTask("下载公开识别模型", "内容识别模型","vision.install",new{}),true,()=>_vision is{} status&&!B(status,"ready")&&B(status,"runtime_available")),Button("识别所选图片","ManageClassify",()=>ConfirmTask("本机内容识别",$"{_selection.Length} 张图片","vision.classify",new{ids=_selection}),true,()=>_selection.Length>0&&_vision is{} status&&B(status,"ready")&&_definitions.Any(d=>B(d,"enabled"))),Button("识别整个图库","ManageClassifyAll",()=>ConfirmTask("识别当前图库","全部图片","vision.classify",new{scope="all"}),true,()=>_vision is{} status&&B(status,"ready")&&_definitions.Any(d=>B(d,"enabled"))));
 }
 private async Task SaveTag()
 {
  if(string.IsNullOrWhiteSpace(_name?.Text)){await Missing("请输入类别名称。");return;}
  string description=_description?.Text.Trim()??"";
  if(description.Length<5||description.Any(c=>c>127)||!description.Any(char.IsAsciiLetter)){await Missing("判定描述需要至少5个英文字符；类别名称可以使用中文。");return;}
  if(_threshold is null||!double.IsFinite(_threshold.Value)||_threshold.Value<.2||_threshold.Value>.9){await Missing("相似度阈值应在0.20至0.90之间。");return;}
  long gen=_generation;string savedName=_name!.Text.Trim();_labelState=null;
  var defs=_definitions.Where(d=>N(d,"id")!=_tagId).Select(d=>JsonSerializer.Deserialize<Dictionary<string,object?>>(d.GetRawText())!).ToList();
  defs.Add(new(){["id"]=_tagId,["name"]=_name!.Text,["description"]=_description!.Text,["threshold"]=_threshold!.Value,["enabled"]=_enabled!.IsChecked==true});
  if(await Write("tags.save",new{definitions=defs})&&gen==_generation&&_visible&&_page=="Tags"){_newTag=false;_labelState=null;Records.SelectedItem=Records.Items.OfType<ManagementRow>().FirstOrDefault(r=>S(r.Value,"name")==savedName);}
 }
 private async Task LabelPreview(){if(_selection.Length==0){Status.Text="先在图库选择1至120张图片，再点击校正标签。";Notify();return;}long gen=_generation;var value=await Read("labels",new{ids=_selection});if(gen!=_generation)return;_labelState=value;TagForm(Selected);Status.Text="校正范围已读取；未点击的类别保持原样。";StateChanged();Notify();}
 private async Task ApplyLabel(string action){if(_selection.Length==0||_tagId is null||_labelState is null){Status.Text="请先选择类别并读取当前图片的校正快照。";Notify();return;}long gen=_generation;var snapshot=S(_labelState.Value,"snapshot_token");if(await Write("labels.apply",new{ids=_selection,changes=new[]{new{tag_id=_tagId.Value,action}},snapshot},false)&&gen==_generation&&_visible&&_page=="Tags"){_labelState=null;await LabelPreview();}}
 private async Task ConfirmTask(string title,string description,string action,object args){if(await Confirm(title,description))await Write(action,args);}
 private async Task<bool> Confirm(string title,string description){var dialog=new ContentDialog{Title=title,Content=description,PrimaryButtonText=title.Replace("预览",""),CloseButtonText="取消",XamlRoot=XamlRoot,RequestedTheme=RequestedTheme};return await dialog.ShowAsync()==ContentDialogResult.Primary;}
 private async Task Pick(TextBox target,string title){try{var picker=new Microsoft.Windows.Storage.Pickers.FolderPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(WindowHandle)){CommitButtonText=title};var result=await picker.PickSingleFolderAsync();if(result is not null)target.Text=result.Path;else{Status.Text="已取消选择。";Notify();}}catch(Exception){Status.Text="目录选择器打开失败";Notify();}}
 private Task Missing(string message){Status.Text=message;Notify();return Task.CompletedTask;}
 private async Task LinkedImages(long modelId,string match,JsonElement page,string? cursor,long gen)
 {
  if(gen!=_generation)return;
  _associationMode=match;if(_linkedHeading is not null)_linkedHeading.Text=match=="candidate"?"同名候选 · 不证明归属":"证据关联的生成图片";
  if(_linkedHost is null){_linkedHost=new StackPanel{Spacing=8};Fields.Children.Add(_linkedHost);}else _linkedHost.Children.Clear();
  _linkedProvider?.Dispose();_linkedProvider=_client is{} client?new ImageSourceProvider(client,_data):null;if(_linkedProvider is not null)_linkedProvider.Changed+=Notify;
  var items=Rows(page.GetProperty("items"));_linkedTotal=N(page,"total");_linkedCount=items.Length;_linkedHost.Children.Add(Text($"{N(page,"total")} 张 · 本页 {items.Length} 张"));
  double listHeight=LinkedModelHeight();
  var list=new GridView{Height=listHeight,SelectionMode=ListViewSelectionMode.None,IsItemClickEnabled=true,ItemTemplate=(DataTemplate)Resources["LinkedTemplate"]};
  list.ItemsSource=items.Select(e=>new GalleryTile(GalleryApi.ParseItem(e)){LabelSize=12*_scale,ImageHeight=Math.Clamp(listHeight-12*_scale-30,24,96)}).ToArray();
  list.ContainerContentChanging+=(_,e)=>{if(e.Item is GalleryTile tile){_linkedProvider?.Visible(tile,!e.InRecycleQueue);if(!e.InRecycleQueue){AutomationProperties.SetAutomationId(e.ItemContainer,"ManageLinkedImage"+tile.Item.Id);ToolTipService.SetToolTip(e.ItemContainer,tile.Item.Name);}}};
  list.ItemClick+=(_,e)=>{if(e.ClickedItem is GalleryTile tile)OpenViewerRequested?.Invoke(tile.Item,new(){["model_file_id"]=modelId,["model_match"]=match=="candidate"?"name":match,["sort"]="name"});};_linkedHost.Children.Add(list);
  if(page.TryGetProperty("next_cursor",out var next)&&next.ValueKind==JsonValueKind.String){string token=next.GetString()!;_linkedHost.Children.Add(Button("下一页关联图片","ManageLinkedNext",async()=>{long actionGen=_generation;var result=await Read("modelImages",new{modelId,match,cursor=token});if(actionGen==_generation&&_visible)await LinkedImages(modelId,match,result,token,actionGen);}));}
  await Task.CompletedTask;
 }
 private readonly ManagementJobSelection _jobSelection=new();private TextBlock? _jobStatus;
 private void UpdateJobDetails(){_jobSelection.Update(_jobs);if(_jobStatus is not null)_jobStatus.Text=_jobSelection.Value is{} job?JobText(job):"";StateChanged();}
 internal Func<UIElement>? ConnectionContent;private StackPanel? _connectionHost;
 internal void RefreshConnection(){if(_visible&&_page=="Jobs"&&_connectionHost is not null){_connectionHost.Children.Clear();if(ConnectionContent is{} create)_connectionHost.Children.Add(create());}}
 private void JobsForm(JsonElement? job)
 {
  ClearForm();_connectionHost=new StackPanel{Spacing=8};Fields.Children.Add(_connectionHost);RefreshConnection();
  _jobSelection.Select(job is{} j?S(j,"id"):"");_jobStatus=Text("",true);AutomationProperties.SetAutomationId(_jobStatus,"ManageJobStatus");Fields.Children.Add(_jobStatus);
  Buttons(Button("取消操作","ManageCancelJob",()=>_jobSelection.Value is{} selected?Write("job.cancel",new{jobId=S(selected,"id")},false):Task.CompletedTask,true,()=>_jobSelection.CanCancel));Fields.Children.Add(Text(""));UpdateJobDetails();
 }
 private void Layout(){if(_page=="Settings")_settingsFields.Width=Math.Max(0,Math.Min(420*_scale,Body.ActualWidth-Body.ColumnSpacing));ModelPager.Visibility=_page=="Models"?Visibility.Visible:Visibility.Collapsed;if(_page=="Models"){LayoutModels();return;}ModelCards.Visibility=ModelEmpty.Visibility=Visibility.Collapsed;FormScroll.Visibility=Visibility.Visible;Grid.SetRow(FormScrollEdge,_page!="Settings"&&ActualWidth<420?1:0);Grid.SetColumn(FormScrollEdge,_page!="Settings"&&ActualWidth<420?0:1);Grid.SetColumnSpan(FormScrollEdge,_page!="Settings"&&ActualWidth<420?2:1);Records.Visibility=_page=="Settings"?Visibility.Collapsed:Visibility.Visible;if(_page=="Settings"){ListColumn.Width=new(0);ListRow.Height=new(1,GridUnitType.Star);FormRow.Height=new(0);Grid.SetRow(FormScroll,0);Grid.SetColumn(FormScroll,1);Grid.SetColumnSpan(FormScroll,1);return;}bool compact=ActualWidth<420;ListColumn.Width=compact?new(1,GridUnitType.Star):new(Math.Clamp(ActualWidth*.36,180,_page=="Models"?340:230));ListRow.Height=compact?new(80):new(1,GridUnitType.Star);FormRow.Height=compact?new(1,GridUnitType.Star):new(0);Grid.SetRow(FormScroll,compact?1:0);Grid.SetColumn(FormScroll,compact?0:1);Grid.SetColumnSpan(Records,compact?2:1);Grid.SetColumnSpan(FormScroll,compact?2:1);}
 public void Dispose(){if(_disposed)return;_disposed=true;Detach();_reads.Dispose();}
}
