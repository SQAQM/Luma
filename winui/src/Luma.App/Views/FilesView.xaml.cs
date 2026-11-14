using System.Text.Json;
using Luma.Core.Engine;
using Luma.Core.Management;
using Luma.Preview.Services;
using Luma.Preview.Views.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;

[Microsoft.UI.Xaml.Data.Bindable]
public sealed class FileRow(JsonElement value,string label,double fontSize)
{
 public JsonElement Value{get;}=value;
 public string Label{get;}=label;
 public double FontSize{get;}=fontSize;
}

public sealed partial class FilesView:UserControl,IDisposable
{
 private ManagementApi? _api;
 private ManagementState? _state;
 private EngineClient? _client;
 private readonly FileReviewState _review=new();
 private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer _poll;
 private readonly List<Button> _writeButtons=[];
 private readonly List<Button> _allButtons=[];
 private CancellationTokenSource _reads=new();
 private long _generation;
 private int _localWrites;
 private bool _visible,_disposed,_loading,_setting,_showPaths,_loaded;
 private double _scale=1;
 private string _page="Organize",_mode="organize",_kind="organize",_rulesHash="";
 private string? _reviewId,_previewJobId,_scanId;
 private long[] _selection=[];
 private JsonElement[] _roots=[],_history=[],_groups=[],_members=[],_rules=[];
 private JsonElement _stateData,_header;
 private TextBox? _destination,_ruleName,_ruleMatch,_ruleDestination;
 private ComboBox? _scope,_root,_classification,_historyChoice,_ruleKind;
 private CheckBox? _ruleEnabled;
 private string? _ruleId;
 private long? _groupId;
 private string? _groupCursor,_memberCursor;
 private JsonElement? _selectedEntry;
 private Button? _execute,_generate,_discard;
 private WindowsShellActions _copy=new(false);
 public nint WindowHandle{get;set;}
 public event Action? Changed;
 public event Action? JobsRequested;
 public event Action? LibraryChanged;
 public object Snapshot=>new{page=_page,mode=_mode,loading=_loading,reviewId=_reviewId,previewInFlight=_previewJobId is not null,reviewState=_review.Review?.State??"none",reviewKind=_review.Review?.Kind,total=_review.Review?.Total??0,actionable=_review.Review?.Actionable??0,canExecute=_review.CanExecute,writing=_state?.Writing??false,uncertain=_state?.Uncertain??false,groupCount=_groups.Length,memberCount=_members.Length,selectionCount=_selection.Length,feedback=Status.Text};
 public FilesView()
 {
  InitializeComponent();
  _poll=DispatcherQueue.CreateTimer();_poll.Interval=TimeSpan.FromMilliseconds(750);
  _poll.Tick+=async(_,_)=>{if(_visible&&!_loading&&_previewJobId is not null)await RefreshReview();};
  SetupScroll.ViewChanged+=(_,e)=>{if(!e.IsIntermediate)Notify();};
  SizeChanged+=(_,_)=>LayoutColumns();
  Body.SizeChanged+=(_,_)=>LayoutColumns();
 }
 private void LayoutColumns()
 {
  bool compact=ActualWidth<520;
  SetupColumn.Width=compact?new(1,GridUnitType.Star):new(Math.Clamp(ActualWidth*.38,260,320));
  SetupRow.Height=compact?GridLength.Auto:new(1,GridUnitType.Star);ResultsRow.Height=compact?new(1,GridUnitType.Star):new(0);
  double available=Body.ActualHeight>0?Body.ActualHeight:ActualHeight;
  double setupLimit=Math.Min(300,Math.Min(available*.5,Math.Max(0,available-(_mode=="rules"?120:100)*_scale)));
  Grid.SetColumnSpan(SetupPane,compact?2:1);SetupPane.MaxHeight=compact?setupLimit:double.PositiveInfinity;
  Grid.SetColumn(PreviewPane,compact?0:1);Grid.SetRow(PreviewPane,compact?1:0);Grid.SetColumnSpan(PreviewPane,compact?2:1);
 }
 public void Configure(double scale,bool diagnostics)
 {
  _scale=scale;_copy=new(diagnostics);ReviewTitle.FontSize=Status.FontSize=Paths.FontSize=14*scale;Summary.FontSize=12*scale;
 }
 public void Bind(EngineClient client,ManagementState state)
 {
  if(ReferenceEquals(_client,client))return;
  Detach();_client=client;_api=new(client);_state=state;state.Changed+=StateChanged;
 }
 public void Detach()
 {
  Hide();if(_state is not null)_state.Changed-=StateChanged;
  _state=null;_api=null;_client=null;_review.Invalidate();_reviewId=_previewJobId=null;
  _roots=_history=_groups=_members=_rules=[];_stateData=_header=default;
 }
 public bool IsShowing(string page)=>_visible&&_page==page;
 public void ShowPage(string page,long[] selection)
 {
  _page=page;_visible=true;Visibility=Visibility.Visible;
  if(!_selection.SequenceEqual(selection)){_selection=selection.Take(120).ToArray();InvalidateDraft();}
  if(page=="Duplicates"&&_mode is not ("duplicates" or "quarantine"))_mode="duplicates";
  if(page=="Organize"&&(_mode is "duplicates" or "quarantine"))_mode="organize";
  _reads.Cancel();_reads.Dispose();_reads=new();_generation++;_loading=false;_review.Revoke();
  _=LoadPage();if(_previewJobId is not null)_poll.Start();
 }
 public void Hide()
 {
  _visible=false;Visibility=Visibility.Collapsed;_poll.Stop();_reads.Cancel();_generation++;_loading=false;_review.Revoke();
 }
 private static string S(JsonElement value,string key)=>value.ValueKind==JsonValueKind.Object&&value.TryGetProperty(key,out var item)&&item.ValueKind==JsonValueKind.String?item.GetString()??"":"";
 private static long N(JsonElement value,string key)=>value.ValueKind==JsonValueKind.Object&&value.TryGetProperty(key,out var item)&&item.ValueKind==JsonValueKind.Number&&item.TryGetInt64(out long n)?n:0;
 private static bool B(JsonElement value,string key)=>value.ValueKind==JsonValueKind.Object&&value.TryGetProperty(key,out var item)&&item.ValueKind==JsonValueKind.True;
 private static JsonElement[] Rows(JsonElement value)=>value.ValueKind==JsonValueKind.Array?value.EnumerateArray().Select(r=>r.Clone()).ToArray():[];
 private void Notify(){if(!_disposed)Changed?.Invoke();}
 private TextBlock Text(string text,bool heading=false)=>new(){Text=text,TextWrapping=TextWrapping.Wrap,FontSize=(heading?18:14)*_scale,FontWeight=heading?Microsoft.UI.Text.FontWeights.SemiBold:Microsoft.UI.Text.FontWeights.Normal};
 private Button Button(string title,string id,Func<Task> action,bool writes=false)
 {
  var button=new Button{Content=title,FontSize=14*_scale,Style=(Style)Application.Current.Resources["LumaButton"]};AutomationProperties.SetAutomationId(button,id);ToolTipService.SetToolTip(button,title);if(title.StartsWith("刷新",StringComparison.Ordinal))Luma.Preview.Services.ActionIcons.Refresh(button,title);
  button.Click+=async(_,_)=>{if(writes){_localWrites++;Update();}try{await action();}catch(OperationCanceledException){}catch(Exception){if(_visible)Status.Text="操作未完成，请核对预览或任务状态。";}finally{if(writes)_localWrites--;Update();}};
  _allButtons.Add(button);if(writes)_writeButtons.Add(button);return button;
 }
 private void Buttons(params Button[] buttons){var row=new TagChipPanel();foreach(var b in buttons)row.Children.Add(b);Setup.Children.Add(row);}
 private TextBox Input(string title,string id,string value="",bool edit=true)
 {
  Setup.Children.Add(Text(title));var input=new TextBox{Text=value,FontSize=14*_scale,IsReadOnly=!edit,TextWrapping=TextWrapping.NoWrap,MaxLength=32767};AutomationProperties.SetAutomationId(input,id);Setup.Children.Add(input);
  if(edit)input.TextChanging+=(_,_)=>{if(!_setting)InvalidateDraft();};return input;
 }
 private ComboBox Choice(string title,string id,IEnumerable<string> choices)
 {
  Setup.Children.Add(Text(title));var box=new ComboBox{FontSize=14*_scale,HorizontalAlignment=HorizontalAlignment.Stretch};AutomationProperties.SetAutomationId(box,id);
  foreach(string choice in choices)box.Items.Add(choice);Setup.Children.Add(box);return box;
 }
 private async Task<JsonElement> Read(string section,object? args=null)
 {
  var api=_api??throw new OperationCanceledException();return await api.ReadAsync(section,args??new{},_reads.Token);
 }
 private async Task<bool> Write(string action,object args)
 {
  var state=_state;if(state is null)return false;
  bool ok=await state.WriteAsync(action,args);if(_visible)Status.Text=state.Feedback;
  return ok;
 }
 private void InvalidateDraft()
 {
  if(_setting)return;
  if(_mode=="rules"){Update();return;}
  _review.Invalidate();_reviewId=_previewJobId=null;_poll.Stop();Entries.ItemsSource=null;Paths.Text="";Summary.Text="配置已更改";Update();
 }
 private async Task LoadPage()
 {
  if(!_visible||_api is null)return;
  long gen=_generation;_loading=true;_loaded=false;Status.Text="正在读取索引与记录…";Update();
  try
  {
   var state=await Read("files.state");if(gen!=_generation||!_visible)return;
   _loaded=true;_stateData=state;_roots=Rows(state.GetProperty("roots"));_rules=Rows(state.GetProperty("rules"));_rulesHash=S(state,"rulesHash");_rulesCursor=N(state,"rulesTotal")>25?25:null;_scanId=state.GetProperty("scan").ValueKind==JsonValueKind.Object?S(state.GetProperty("scan"),"id"):null;
   Build();if(_mode=="duplicates"&&_scanId is not null)await LoadGroups();
   if(_reviewId is not null)await RefreshReview();else Status.Text="";
  }
  catch(OperationCanceledException){}
  catch(Exception){if(gen==_generation){_loaded=false;InvalidateDraft();Status.Text="记录读取失败，请刷新；保存和执行已停用。";}}
  finally{if(gen==_generation){_loading=false;Update();}}
 }
 private Task SetMode(string mode)
 {
  InvalidateDraft();_mode=mode;_generation++;_reads.Cancel();_reads.Dispose();_reads=new();return LoadPage();
 }
 private void Build()
 {
  _setting=true;
  try
  {
   Modes.Children.Clear();Setup.Children.Clear();Actions.Children.Clear();PageActions.Children.Clear();RuleActions.Children.Clear();RulesPager.Children.Clear();RuleActions.Visibility=RulesPager.Visibility=_mode=="rules"?Visibility.Visible:Visibility.Collapsed;SetupPane.RowSpacing=_mode=="rules"?10:0;_writeButtons.Clear();_allButtons.Clear();Entries.ItemsSource=null;_selectedEntry=null;_showPaths=false;Paths.Visibility=RulesEmpty.Visibility=Visibility.Collapsed;Entries.Visibility=_mode=="rules"?Visibility.Collapsed:Visibility.Visible;RulesList.Visibility=_mode=="rules"?Visibility.Visible:Visibility.Collapsed;Actions.Visibility=PageActions.Visibility=_mode=="rules"?Visibility.Collapsed:Visibility.Visible;
   foreach(var (label,mode) in _page=="Duplicates"?new[]{("查找重复","duplicates"),("可恢复区","quarantine")}:new[]{("分类移动","organize"),("分类规则","rules"),("目录索引","roots"),("恢复检查","recovery")}){var button=Button(label,"FilesMode"+mode,()=>SetMode(mode));if(mode==_mode){button.BorderThickness=new Thickness(2);button.BorderBrush=(Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["LumaAccent"];AutomationProperties.SetItemStatus(button,"当前功能");}Modes.Children.Add(button);}
   if(_mode=="organize")BuildOrganize();else if(_mode=="duplicates")BuildDuplicates();else if(_mode=="quarantine")BuildQuarantine();else if(_mode=="roots")BuildRoots();else if(_mode=="rules")BuildRules();else BuildRecovery();
   _generate=null;if(_mode is "organize" or "duplicates" or "roots"){_generate=Button("生成预览","FilesGenerate",()=>Generate(DefaultKind()),true);Actions.Children.Add(_generate);}
   _execute=Button("确认并执行","FilesExecute",Execute,true);Actions.Children.Add(_execute);
   _discard=Button("作废预览","FilesDiscard",Discard,true);Actions.Children.Add(_discard);
   PageActions.Children.Add(Button("路径文本","FilesTogglePaths",()=>{_showPaths=!_showPaths;Paths.Visibility=_showPaths?Visibility.Visible:Visibility.Collapsed;Entries.Visibility=_showPaths?Visibility.Collapsed:Visibility.Visible;return Task.CompletedTask;}));
   PageActions.Children.Add(Button("复制来源","FilesCopySource",()=>CopyPath("source")));PageActions.Children.Add(Button("下一页","FilesNext",NextPage));
   var recent=Rows(_stateData.GetProperty("reviews")).Where(r=>S(r,"kind")==DefaultKind()||(_mode=="organize"&&S(r,"kind")=="organize_undo")).ToArray();
   if(_mode!="rules"&&recent.Length>0){var choice=Choice("已保存预览","FilesRecent",recent.Select(r=>KindName(S(r,"kind"))+" · "+S(r,"state")+" · "+N(r,"total")+"项"));choice.SelectedIndex=0;Buttons(Button("载入已保存预览","FilesLoadRecent",async()=>{if(choice.SelectedIndex<0)return;var selected=recent[choice.SelectedIndex];await LoadSaved(S(selected,"id"));}));}
   var refresh=Button("刷新记录","FilesRefresh",LoadPage);if(_mode=="rules")RulesPager.Children.Insert(0,refresh);else Buttons(refresh);
   RenderReviewTitle();LayoutColumns();
  }
  finally{_setting=false;}
  Update();
 }
 private void RootChoice()
 {
  var labels=_roots.Select(r=>S(r,"name")+" · "+N(r,"count")+"项");_rootAll=_mode=="duplicates";
  _root=Choice(_rootAll?"查找范围":"导入目录","FilesRoot",_rootAll?new[]{"整个图库"}.Concat(labels):labels);if(_root.Items.Count>0)_root.SelectedIndex=0;
  if(_rootAll&&_stateData.TryGetProperty("scan",out var scan)&&scan.ValueKind==JsonValueKind.Object&&N(scan,"root_id") is long saved&&saved>0){int index=Array.FindIndex(_roots,r=>N(r,"id")==saved);if(index>=0)_root.SelectedIndex=index+1;else _scanId=null;}
  var path=Input("目录完整路径","FilesRootPath","",false);if(RootId() is long initial)_=LoadRootPath(path,initial,_generation);else if(_rootAll)path.Text="全部已导入目录";
  _root.SelectionChanged+=async(_,_)=>{if(_setting)return;InvalidateDraft();if(_mode=="duplicates")ClearScanScope();long gen=_generation;if(RootId() is long id){try{var value=await Read("files.root",new{rootId=id});if(gen==_generation&&RootId()==id)path.Text=S(value,"path");}catch(OperationCanceledException){}catch(Exception){Status.Text="目录路径读取失败，请刷新。";}}else path.Text="全部已导入目录";};
 }
 private async Task LoadRootPath(TextBox path,long id,long gen){try{var value=await Read("files.root",new{rootId=id});if(gen==_generation&&RootId()==id)path.Text=S(value,"path");}catch(OperationCanceledException){}catch(Exception){if(gen==_generation)path.Text="读取失败，请刷新";}}
 private bool _rootAll;
 private long? RootId(){int index=(_root?.SelectedIndex??-1)-(_rootAll?1:0);return index>=0&&index<_roots.Length?N(_roots[index],"id"):null;}
 private void ClearScanScope(){_scanId=null;_groups=_members=[];_groupId=null;_selectedEntry=null;if(_groupList is not null)_groupList.ItemsSource=null;Entries.ItemsSource=null;Paths.Text="";ReviewTitle.Text="查找范围已变化";Summary.Text="请明确启动新的重复检查，再选择保留项与清理组。";Update();}
 private void BuildOrganize()
 {
  _classification=Choice("分类方式","FilesClassification",new[]{"生成模型家族","内容类别 TAG","提示词解析词规则","画师","文件格式"});_classification.SelectedIndex=1;
  _classification.SelectionChanged+=(_,_)=>{if(!_setting)InvalidateDraft();};
  _scope=Choice("整理范围","FilesScope",new[]{"请选择范围","图库所选 "+_selection.Length+" 张","指定导入目录","整个图库"});_scope.SelectedIndex=_selection.Length>0?1:0;_scope.SelectionChanged+=(_,_)=>{if(!_setting){InvalidateDraft();ShowRootWidgets();}};
  int rootStart=Setup.Children.Count;RootChoice();_rootWidgets=Setup.Children.Skip(rootStart).ToArray();ShowRootWidgets();_destination=Input("目标目录","FilesDestination");Buttons(Button("选择目录","FilesPickDestination",PickDestination));
  _history=Rows(_stateData.GetProperty("history"));_historyChoice=Choice("移动记录","FilesMoveHistory",_history.Select(r=>StatusName(S(r,"status"))+" · "+N(r,"processed")+"/"+N(r,"total")));_historyChoice.SelectionChanged+=(_,_)=>{if(!_setting)InvalidateDraft();};
  if(_history.Length>0)_historyChoice.SelectedIndex=0;Buttons(Button("生成撤销预览","FilesUndoPreview",()=>Generate("organize_undo"),true));
 }
 private UIElement[] _rootWidgets=[];
 private void ShowRootWidgets(){foreach(var widget in _rootWidgets)widget.Visibility=_scope?.SelectedIndex==2?Visibility.Visible:Visibility.Collapsed;}
 private void BuildDuplicates()
 {
  RootChoice();Setup.Children.Add(Text(""));
  Buttons(Button("查找完全重复","FilesScan",Scan,true),Button("全部组加入清理","FilesSelectAll",()=>SelectAll(true),true),Button("取消全部选择","FilesSelectNone",()=>SelectAll(false),true));
  _groupList=new ListView{Height=160,SelectionMode=ListViewSelectionMode.Single,ItemTemplate=Entries.ItemTemplate};AutomationProperties.SetAutomationId(_groupList,"FilesGroups");Setup.Children.Add(_groupList);
  _groupList.SelectionChanged+=async(_,_)=>{if(_setting||_groupList.SelectedItem is not FileRow row)return;_groupId=N(row.Value,"id");InvalidateDraft();_memberCursor=null;try{await LoadMembers();}catch(OperationCanceledException){}catch(Exception){Status.Text="副本读取失败，请刷新。";}};
  Buttons(Button("下一页重复组","FilesGroupNext",async()=>{if(_groupCursor is not null)await LoadGroups(_groupCursor);}),Button("本组加入/取消清理","FilesSelectGroup",SelectGroup,true),Button("保留所选副本","FilesKeep",Keep,true));
 }
 private ListView? _groupList;
 private void BuildQuarantine()
 {
  Setup.Children.Add(Text(""));
  _history=Rows(_stateData.GetProperty("quarantine"));_historyChoice=Choice("隔离任务","FilesQuarantineHistory",_history.Select(r=>S(r,"status")+" · "+N(r,"restorable")+"份可恢复"));if(_history.Length>0)_historyChoice.SelectedIndex=0;
  _historyChoice.SelectionChanged+=(_,_)=>{if(!_setting)InvalidateDraft();};
  Buttons(Button("生成还原预览","FilesRestorePreview",()=>Generate("duplicate_undo"),true),Button("生成永久清空预览","FilesPurgePreview",()=>Generate("duplicate_purge"),true));
 }
 private void BuildRoots()
 {
  RootChoice();Setup.Children.Add(Text(""));
  Buttons(Button("作废该目录旧整理预览","FilesRootDiscard",async()=>{if(RootId() is long id&&await Confirm("作废旧整理预览","")){InvalidateDraft();await Write("files.root.discard",new{rootId=id,confirmed=true});}},true));
 }
 private void BuildRecovery()
 {
  var recovery=_stateData.GetProperty("recovery");Setup.Children.Add(Text($"移动待复核 {N(recovery,"moves")} 项\n重复清理待复核 {N(recovery,"duplicates")} 项",true));
  Setup.Children.Add(Text(""));
  Buttons(Button("生成移动恢复预览","FilesRecoverMoves",()=>Generate("recover_moves"),true),Button("生成重复恢复预览","FilesRecoverDuplicates",()=>Generate("recover_duplicates"),true));
 }
 private void BuildRules()
 {
  _ruleId=null;RulesList.ItemsSource=_rules.Select(r=>new FileRow(r,S(r,"name")+"\n"+RuleKindName(S(r,"kind"))+" · "+S(r,"match"),14*_scale)).ToArray();RulesEmpty.Visibility=_rules.Length==0?Visibility.Visible:Visibility.Collapsed;
  _ruleKind=Choice("规则类型","FilesRuleKind",new[]{"模型","内容类别","提示词解析词","画师","格式"});_ruleKind.SelectedIndex=0;
  _ruleName=Input("分类名称","FilesRuleName");_ruleMatch=Input("匹配内容","FilesRuleMatch");_ruleDestination=Input("目标目录","FilesRuleDestination");_ruleEnabled=new CheckBox{Content="启用",IsChecked=true,FontSize=14*_scale};Setup.Children.Add(_ruleEnabled);
  RuleActions.Children.Add(Button("新增","FilesRuleNew",()=>{RulesList.SelectedItem=null;_ruleId=null;_ruleName.Text=_ruleMatch.Text=_ruleDestination.Text="";_ruleKind.SelectedIndex=0;_ruleEnabled.IsChecked=true;Update();return Task.CompletedTask;}));RuleActions.Children.Add(Button("保存","FilesRuleSave",SaveRule,true));RuleActions.Children.Add(Button("移除","FilesRuleRemove",RemoveRule,true));
  var next=Button("下一页规则","FilesRuleNext",NextRules);next.Style=(Style)Application.Current.Resources["LumaViewerIcon"];next.Content=new FontIcon{Glyph="\uE76C",FontSize=15};AutomationProperties.SetName(next,"下一页规则");RulesPager.Children.Add(next);
 }
 private static string RuleKindName(string kind)=>kind switch{"model"=>"模型","content_tag"=>"内容类别","tag"=>"提示词解析词","artist"=>"画师","format"=>"格式",_=>kind};
 private void RuleSelected(object sender,SelectionChangedEventArgs args)
 {
  if(_setting||RulesList.SelectedItem is not FileRow row||_ruleName is null||_ruleMatch is null||_ruleDestination is null||_ruleKind is null||_ruleEnabled is null)return;
  _setting=true;try{_ruleId=S(row.Value,"id");_ruleName.Text=S(row.Value,"name");_ruleMatch.Text=S(row.Value,"match");_ruleDestination.Text=S(row.Value,"destination");_ruleEnabled.IsChecked=B(row.Value,"enabled");_ruleKind.SelectedIndex=Array.IndexOf(new[]{"model","content_tag","tag","artist","format"},S(row.Value,"kind"));}finally{_setting=false;}Status.Text="";Update();
 }
 private int? _rulesCursor;
 private async Task NextRules()
 {
  if(_rulesCursor is null){Status.Text="已到规则末页，请刷新回到首页。";return;}var value=await Read("files.rules",new{cursor=_rulesCursor});_rules=Rows(value.GetProperty("items"));_rulesHash=S(value,"hash");_rulesCursor=value.GetProperty("next_cursor").ValueKind==JsonValueKind.Number?value.GetProperty("next_cursor").GetInt32():null;Build();
 }
 private async Task SaveRule()
 {
  if(string.IsNullOrWhiteSpace(_ruleName?.Text)||string.IsNullOrWhiteSpace(_ruleMatch?.Text)){Status.Text="请填写分类名称和匹配内容。";return;}
  var rule=new{id=_ruleId??Guid.NewGuid().ToString("N"),name=_ruleName.Text,match=_ruleMatch.Text,kind=new[]{"model","content_tag","tag","artist","format"}[Math.Max(0,_ruleKind!.SelectedIndex)],destination=_ruleDestination!.Text,enabled=_ruleEnabled!.IsChecked==true};
  if(await Write("files.rules.change",new{rule,expectedHash=_rulesHash})){InvalidateDraft();await LoadPage();}
 }
 private async Task RemoveRule(){if(_ruleId is not null&&await Write("files.rules.change",new{removeId=_ruleId,expectedHash=_rulesHash})){_ruleId=null;InvalidateDraft();await LoadPage();}}
 private string DefaultKind()=>_mode switch{"roots"=>"root_remove","duplicates"=>"duplicate_cleanup","quarantine"=>"duplicate_undo","recovery"=>"recover_moves",_=>"organize"};
 private Dictionary<string,object?> Options(string kind)
 {
  if(kind=="organize")
  {
   if(_scope?.SelectedIndex is null or <=0)throw new InvalidOperationException();
   var result=new Dictionary<string,object?>{{"mode",new[]{"model","content_tag","tag","artist","format"}[Math.Max(0,_classification!.SelectedIndex)]},{"destination",_destination!.Text},{"scope",_scope.SelectedIndex==1?"selected":_scope.SelectedIndex==2?"root":"all"}};
   if(_scope.SelectedIndex==1)result["ids"]=_selection;if(_scope.SelectedIndex==2)result["rootId"]=RootId();return result;
  }
  if(kind=="root_remove")return new(){{"rootId",RootId()}};
  if(kind=="duplicate_cleanup"){if(_scanId is null)throw new InvalidOperationException();return new(){{"scanId",_scanId}};}
  if(kind.EndsWith("undo")||kind=="duplicate_purge")
  {
   int index=_historyChoice?.SelectedIndex??-1;if(index<0||index>=_history.Length)throw new InvalidOperationException();return new(){{"jobId",(S(_history[index],"id")==""?S(_history[index],"job_id"):S(_history[index],"id"))}};
  }
  return new();
 }
 private async Task Generate(string kind)
 {
  if(_mode=="rules")return;
  Dictionary<string,object?> options;
  try{options=Options(kind);}catch(InvalidOperationException){Status.Text="请选择明确范围或已结束任务。";return;}
  _review.Invalidate();long version=_review.Version,gen=_generation;_kind=kind;
  if(await Write("files.preview",new{kind,options}))
  {
   var result=_state!.Result;if(gen!=_generation||version!=_review.Version||!_visible)return;
   _reviewId=S(result,"reviewId");_previewJobId=S(result,"id");_expectedVersion=version;_poll.Start();Status.Text="正在生成预览";await RefreshReview();
  }
 }
 private long _expectedVersion;
 private async Task RefreshReview()
 {
  if(!_visible||_reviewId is null||_api is null)return;
  long gen=_generation;string id=_reviewId;
  try
  {
   var value=await Read("files.review",new{reviewId=id});if(gen!=_generation||id!=_reviewId||!_visible)return;
   _header=value;string state=S(value,"state");if(state=="building"){Status.Text="具体预览正在准备，尚未允许执行。";return;}
   _previewJobId=null;_poll.Stop();_review.Accept(value,_expectedVersion);RenderReviewTitle();
   if(state=="ready"){RestoreOptions(value);await LoadItems(null);Status.Text="预览已生成";}else Status.Text="预览不可用";
  }
  catch(OperationCanceledException){}
  catch(Exception){if(gen==_generation)Status.Text="预览读取失败，可刷新或到任务中心核对。";}
  finally{Update();}
 }
 private int? _itemCursor;
 private async Task LoadItems(int? cursor)
 {
  if(_reviewId is null)return;string id=_reviewId;long gen=_generation;
  var page=await Read("files.items",new{reviewId=id,cursor});if(gen!=_generation||id!=_reviewId)return;
  Entries.ItemsSource=Rows(page.GetProperty("items")).Select(r=>new FileRow(r,EntryLabel(r),14*_scale)).ToArray();
  _itemCursor=page.GetProperty("next_cursor").ValueKind==JsonValueKind.Number?page.GetProperty("next_cursor").GetInt32():null;
  if(Entries.Items.Count>0)Entries.SelectedIndex=0;
 }
 private Task NextPage()=>_reviewId is not null&&_itemCursor is int cursor?LoadItems(cursor):_mode=="duplicates"&&_memberCursor is not null?LoadMembers(_memberCursor):Task.CompletedTask;
 private async Task LoadSaved(string id)
 {
  _review.Invalidate();_expectedVersion=_review.Version;_reviewId=id;_previewJobId=null;await RefreshReview();
 }
 private void RestoreOptions(JsonElement header)
 {
  if(S(header,"kind")!="organize"||_mode!="organize"||!header.TryGetProperty("options",out var options))return;
  _setting=true;try{if(_destination is not null)_destination.Text=S(options,"destination");if(_classification is not null)_classification.SelectedIndex=Array.IndexOf(new[]{"model","content_tag","tag","artist","format"},S(options,"mode"));if(_scope is not null)_scope.SelectedIndex=S(options,"scope") switch{"selected"=>1,"root"=>2,_=>3};if(S(options,"scope")=="selected"&&options.TryGetProperty("ids",out var ids)){_selection=ids.EnumerateArray().Select(x=>x.GetInt64()).Take(120).ToArray();if(_scope is not null){_scope.Items[1]="已保存选择 "+_selection.Length+" 张";_scope.SelectedIndex=1;}}ShowRootWidgets();if(_root is not null&&N(options,"rootId") is long root&&root>0)_root.SelectedIndex=Array.FindIndex(_roots,r=>N(r,"id")==root);}finally{_setting=false;}
 }
 private void RenderReviewTitle()
 {
  if(_mode=="rules"){ReviewTitle.Text="分类规则";Summary.Text=$"{_rules.Length} 项";return;}
  var review=_review.Review;ReviewTitle.Text=review is null?"文件预览":KindName(review.Kind)+" · "+StatusName(review.State);
  Summary.Text=review is null?"":$"{review.Total} 项 · 可执行 {review.Actionable} · 跳过 {review.Skipped} · 冲突 {review.Conflicts}";
  if(_header.ValueKind==JsonValueKind.Object&&_header.TryGetProperty("details",out var details)&&details.TryGetProperty("blockers",out var blockers)&&blockers.GetArrayLength()>0)Summary.Text+="\n"+string.Join("；",blockers.EnumerateArray().Select(x=>x.GetString()));
  if(review?.Kind=="organize"&&_header.TryGetProperty("options",out var options)){Summary.Text+="\n"+(S(options,"scope") switch{"selected"=>"所选图片","root"=>"指定目录",_=>"整个图库"})+" · "+(S(options,"mode") switch{"model"=>"按模型家族","content_tag"=>"按内容类别","tag"=>"按提示词规则","artist"=>"按画师",_=>"按格式"});}

  if(review?.Kind=="duplicate_cleanup")Summary.Text+="";
  if(review?.Kind=="duplicate_purge")Summary.Text+="";
 }
 private static string StatusName(string state)=>state switch{"ready"=>"待确认","completed"=>"已完成","completed_with_errors"=>"部分完成","building"=>"准备中","submitted"=>"已提交","claimed"=>"已受理","cancelled"=>"已取消","interrupted"=>"已中断","stale"=>"已失效","skipped"=>"跳过","shared"=>"保留重叠索引","reference"=>"核对项","conflict"=>"冲突",_=>state};
 private static string EntryLabel(JsonElement item)
 {
  string source=S(item,"source"),target=S(item,"target"),keep=S(item,"keep_path");return StatusName(S(item,"status"))+" · "+S(item,"detail")+"\n来源："+source+(target.Length>0?"\n目标："+target:"")+(keep.Length>0?"\n保留："+keep:"");
 }
 private string ScanSummary()
 {
  if(_stateData.ValueKind!=JsonValueKind.Object||!_stateData.TryGetProperty("scan",out var scan)||scan.ValueKind!=JsonValueKind.Object)return "";
  return $"已检查 {N(scan,"checked")} 份 · 确认 {N(scan,"groups")} 组 · 跳过 {N(scan,"skipped")} 份";
 }
 private static string KindName(string kind)=>kind switch{"organize"=>"分类移动预览","organize_undo"=>"撤销移动预览","root_remove"=>"移除目录索引预览","duplicate_cleanup"=>"重复隔离预览","duplicate_undo"=>"隔离还原预览","duplicate_purge"=>"永久清空预览","recover_moves"=>"中断移动恢复预览","recover_duplicates"=>"重复中断恢复预览",_=>"文件预览"};
 private void EntrySelected(object sender,SelectionChangedEventArgs args)
 {
  _selectedEntry=Entries.SelectedItem is FileRow row?row.Value:null;
  Paths.Text=_selectedEntry is{} item?"来源\r\n"+(S(item,"source")==""?S(item,"path"):S(item,"source"))+"\r\n目标\r\n"+S(item,"target")+"\r\n保留\r\n"+S(item,"keep_path"):"";Update();
 }
 private Task CopyPath(string key)
 {
  if(_selectedEntry is{} item){string value=S(item,key);if(value==""&&key=="source")value=S(item,"path");if(value.Length>0){_copy.Copy(value,"file_preview_path");Status.Text="已复制完整路径。";}}return Task.CompletedTask;
 }

 private void StateChanged(){if(_visible&&_state is not null)Status.Text=_state.Feedback;Update();}
 private void Update()
 {
  bool writable=_loaded&&!_loading&&_localWrites==0&&_state is{Writing:false,Uncertain:false};foreach(var b in _writeButtons){string id=AutomationProperties.GetAutomationId(b);b.IsEnabled=writable&&(!(id is "FilesSelectAll" or "FilesSelectNone" or "FilesSelectGroup" or "FilesKeep")||_scanId is not null);}
  foreach(var b in _allButtons){string id=AutomationProperties.GetAutomationId(b);if(id=="FilesGroupNext")b.IsEnabled=_groupCursor is not null;if(id=="FilesRuleNext")b.IsEnabled=_rulesCursor is not null;if(id=="FilesRuleRemove")b.IsEnabled=writable&&_ruleId is not null;if(id=="FilesRuleSave")b.IsEnabled=writable&&!string.IsNullOrWhiteSpace(_ruleName?.Text)&&!string.IsNullOrWhiteSpace(_ruleMatch?.Text);if(id is "FilesUndoPreview" or "FilesRestorePreview" or "FilesPurgePreview")b.IsEnabled=writable&&_history.Length>0;}
  if(_execute is not null){_execute.IsEnabled=writable&&_review.CanConfirm;_execute.Content=_review.Review?.Kind switch{"organize_undo" or "duplicate_undo"=>"确认还原","root_remove"=>"确认移除索引","duplicate_cleanup"=>"确认隔离","duplicate_purge"=>"永久清空","recover_moves" or "recover_duplicates"=>"确认恢复检查",_=>"确认移动"};}
  if(_generate is not null)_generate.IsEnabled=writable&&_previewJobId is null&&(_mode!="duplicates"||_scanId is not null)&&(_mode!="organize"||(_scope?.SelectedIndex>0&&!string.IsNullOrWhiteSpace(_destination?.Text)&&(_scope?.SelectedIndex!=2||RootId() is not null)));
  foreach(var b in PageActions.Children.OfType<Button>()){string id=AutomationProperties.GetAutomationId(b);b.IsEnabled=id switch{"FilesNext"=>_itemCursor is not null||(_reviewId is null&&_memberCursor is not null),"FilesNextItem"=>Entries.Items.Count>1&&Entries.SelectedIndex<Entries.Items.Count-1,"FilesTogglePaths" or "FilesCopySource"=>_selectedEntry is not null,_=>true};}
  if(_discard is not null)_discard.IsEnabled=writable&&_review.Review is{State:"ready"};
  Resolve.Visibility=_state?.Uncertain==true?Visibility.Visible:Visibility.Collapsed;Resolve.IsEnabled=_state?.OperationId.Length==32;Notify();
 }
 private async Task Execute()
 {
  if(!_review.CanConfirm)return;var snapshot=_review.Review!;long version=_review.Version;
  if(!await Confirm(KindName(snapshot.Kind),$"{snapshot.Actionable} 项\n{Summary.Text}"))return;
  if(version!=_review.Version||snapshot.Id!=_review.Review?.Id||!_review.CanConfirm){Status.Text="预览已更改";return;}
  _review.Approve(true);_review.BeginExecute();
  string confirmation=snapshot.Kind=="duplicate_purge"?"永久清空":"";
  if(await Write("files.execute",new{reviewId=snapshot.Id,fingerprint=snapshot.Fingerprint,confirmed=true,confirmation})){LibraryChanged?.Invoke();JobsRequested?.Invoke();}
 }
 private async Task<bool> Confirm(string title,string body)=>await new ContentDialog{Title=title,Content=body,PrimaryButtonText=title.Replace("预览",""),CloseButtonText="取消",XamlRoot=XamlRoot}.ShowAsync()==ContentDialogResult.Primary;
 private async Task Discard(){if(_reviewId is{} id&&await Write("files.discard",new{reviewId=id})){InvalidateDraft();await LoadPage();}}
 private async void ResolveClicked(object sender,RoutedEventArgs e){if(_state is null)return;try{await _state.ResolveAsync();_review.Revoke();await LoadPage();}catch(Exception){Status.Text="状态核对失败";}Update();}
 private async Task PickDestination()
 {
  var picker=new Microsoft.Windows.Storage.Pickers.FolderPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(WindowHandle));var result=await picker.PickSingleFolderAsync();if(result is not null&&_destination is not null)_destination.Text=result.Path;
 }
 private async Task Scan()
 {
  if(!await Confirm("查找完全重复","将读取“"+(_root?.SelectedItem?.ToString()??"未选择")+"”"))return;
  if(await Write("files.scan",new{rootId=RootId()})){LibraryChanged?.Invoke();JobsRequested?.Invoke();}
 }
 private async Task LoadGroups(string? cursor=null)
 {
  if(_scanId is null||_groupList is null)return;long gen=_generation,version=_review.Version;
  string scanId=_scanId;var value=await Read("files.duplicate.groups",new{scanId,cursor});if(gen!=_generation||version!=_review.Version||scanId!=_scanId||!_visible)return;
  _groups=Rows(value.GetProperty("items"));_groupCursor=S(value,"next_cursor");if(_groupCursor=="")_groupCursor=null;
  if(_groups.Length==0){ReviewTitle.Text="完全重复结果";Summary.Text=ScanSummary();}
  _groupList.ItemsSource=_groups.Select(r=>new FileRow(r,(B(r,"selected")?"已选 · ":"")+N(r,"member_count")+" 份完全重复\n保留："+S(r,"name"),14*_scale)).ToArray();if(_groups.Length>0)_groupList.SelectedIndex=0;Notify();
 }
 private async Task LoadMembers(string? cursor=null)
 {
  if(_groupId is not long id)return;long gen=_generation,version=_review.Version;var value=await Read("files.duplicate.members",new{groupId=id,cursor});if(gen!=_generation||version!=_review.Version||id!=_groupId||!_visible)return;
  _members=Rows(value.GetProperty("items"));_memberCursor=S(value,"next_cursor");if(_memberCursor=="")_memberCursor=null;
  Entries.ItemsSource=_members.Select(r=>new FileRow(r,(B(r,"is_keep")?"保留 · ":"副本 · ")+S(r,"name"),14*_scale)).ToArray();if(_members.Length>0)Entries.SelectedIndex=0;ReviewTitle.Text="本组副本 · 只读路径";Summary.Text=ScanSummary()+"\n选择保留项和清理组后，另生成具体隔离预览。";Update();
 }
 private async Task SelectGroup()
 {
  if(_groupId is not long id)return;var group=_groups.FirstOrDefault(g=>N(g,"id")==id);InvalidateDraft();if(await Write("files.duplicate.select",new{groupId=id,selected=!B(group,"selected")}))await LoadGroups();
 }
 private async Task SelectAll(bool selected){if(_scanId is not null){InvalidateDraft();if(await Write("files.duplicate.all",new{scanId=_scanId,selected}))await LoadGroups();}}
 private async Task Keep(){if(_groupId is long id&&_selectedEntry is{} member){long image=N(member,"id");InvalidateDraft();if(await Write("files.duplicate.keep",new{groupId=id,imageId=image}))await LoadGroups();}}
 public void Dispose(){if(_disposed)return;Detach();_disposed=true;_reads.Dispose();}
}
