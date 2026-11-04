using System.Text.Json;
using Luma.Core.Gallery;
using Luma.Core.Management;
using Luma.Preview.Services;
using Luma.Preview.Views.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;
public sealed partial class ManagementView
{
 private readonly HashSet<Button> _modelButtons=[];
 private ModelDirectoryAdd? _modelAdd;
 private TextBox? _modelSearch;
 private long _modelTotal,_formRevision;private TextBlock? _modelProgress;private FrameworkElement? _modelProgressRow;private Button? _modelStop;private Microsoft.UI.Dispatching.DispatcherQueueTimer? _modelSearchTimer;
 private static string ModelSize(long bytes)=>bytes>=1073741824?$"{bytes/1073741824d:0.##} GiB":bytes>=1048576?$"{bytes/1048576d:0.#} MiB":$"{bytes:N0} B";
 private static bool ModelJob(JsonElement j)=>S(j,"kind") is "model_scan" or "model_sync" or "model_links" or "reclassify_models" or "civitai_image_lookup";
 private static bool ActiveJob(JsonElement j)=>S(j,"status") is "queued" or "running";
 private string ModelOperationFeedback()
 {
  if(_page=="Models"&&_state is{Writing:false,Uncertain:false,LastAction:"models.sync"} state){
   string id=S(state.Result,"id");var job=_jobs.FirstOrDefault(j=>S(j,"id")==id&&S(j,"kind")=="model_sync");
   if(job.ValueKind==JsonValueKind.Object&&!ActiveJob(job)&&S(job,"feedback").Length>0)return CivitaiFeedback.SyncResult(job);
  }
  return _state?.Feedback??"";
 }
 private async Task FetchModelInformation()
 {
  var selected=Records.SelectedItems.OfType<ManagementRow>().Select(r=>r.Value).ToArray();
  var scope=selected.Length>0?selected:_models.Where(m=>!B(m,"cover_available")||S(m,"status")!="synced").ToArray();
  long[] ids=scope.Select(m=>N(m,"id")).Where(id=>id>0).Distinct().Take(60).ToArray();
  if(ids.Length==0){Status.Text="已更新";Notify();return;}
  string range=selected.Length>0?"所选":"本页待获取资料的";
  await ConfirmTask("更新资料",$"{range} {ids.Length} 个模型","models.sync",new{ids,downloadCovers=true});
 }
 private async Task<JsonElement> ReadModelPage()
 {
  long gen=_generation;var root=_rootId;string query=_query;string? cursor=_cursor;string[] types=_modelTypes.ToArray(),families=_modelFamilies.ToArray();
  try{return await Read("models",new{rootId=root,query,cursor,types,families});}
  catch(Luma.Core.Engine.EngineRpcException error) when(gen==_generation&&cursor is not null&&error.Code=="invalid_request")
  { _cursor=null;_history.Clear();return await Read("models",new{rootId=root,query,cursor=(string?)null,types,families}); }
 }
 private void UpdateModelPolling(){if(_visible&&_page=="Models"&&_jobs.Any(j=>ModelJob(j)&&ActiveJob(j)))_poll.Start();else if(_page=="Models")_poll.Stop();UpdateModelProgress();}
 private void UpdateModelProgress(){if(_modelProgress is null)return;var job=_jobs.FirstOrDefault(j=>ModelJob(j)&&ActiveJob(j));_modelProgress.Text=job.ValueKind==JsonValueKind.Object?JobText(job).Replace('\n',' '):"";if(_modelProgressRow is not null)_modelProgressRow.Visibility=Visibility.Visible;if(_modelStop is not null)_modelStop.Visibility=job.ValueKind==JsonValueKind.Object?Visibility.Visible:Visibility.Collapsed;}
 private async Task PollModelJobs()
 {
  if(_api is null||!_loads.TryPoll())return;long gen=_generation;bool refresh=false;
  try{var rows=Rows(await Read("jobs"));if(gen!=_generation||!_visible||_page!="Models")return;
   refresh=_jobs.Where(j=>ModelJob(j)&&ActiveJob(j)).Any(old=>!rows.Any(j=>S(j,"id")==S(old,"id")&&ActiveJob(j)));
   var finished=rows.FirstOrDefault(j=>S(j,"kind")=="model_sync"&&!ActiveJob(j)&&_jobs.Any(old=>S(old,"id")==S(j,"id")&&ActiveJob(old)));
   _jobs=rows;UpdateModelPolling();if(refresh){_cursor=null;_history.Clear();if(finished.ValueKind==JsonValueKind.Object)Status.Text=CivitaiFeedback.SyncResult(finished);if(_sub.Length>0){refresh=false;if(finished.ValueKind!=JsonValueKind.Object)Status.Text="已完成";}}Notify();
  }catch(OperationCanceledException){}catch(Exception){if(gen==_generation){Status.Text="状态读取失败";Notify();}}
  finally{_loads.Complete();if(refresh){_cursor=null;_history.Clear();}if((_loads.RefreshPending||refresh)&&_visible&&!_disposed)_=RefreshPage();}
 }
 private void BuildModelControls()
 {
  Actions.Children.Clear();Actions.Visibility=Visibility.Collapsed;ModelControlScroll.Visibility=Visibility.Visible;ModelControls.Children.Clear();ModelPager.Children.Clear();ModelPager.Visibility=Visibility.Visible;_modelButtons.Clear();
  Button Tool(string title,string id,Func<Task> action,bool write=false,Func<bool>? when=null){var b=Button(title,id,action,write,when);_modelButtons.Add(b);return b;}
  Button Icon(string title,string id,string glyph,Func<Task> action,bool write=false,Func<bool>? when=null){var b=Tool(title,id,action,write,when);b.Style=(Style)Application.Current.Resources["LumaViewerIcon"];b.Content=new FontIcon{Glyph=glyph,FontSize=15};AutomationProperties.SetName(b,title);return b;}
  var folders=new TagChipPanel();bool compact=ActualWidth<680;_rootChoice=new ComboBox{MinWidth=compact?112:140,Width=compact?112:160,FontSize=14*_scale};foreach(var root in _roots)_rootChoice.Items.Add(S(root,"name")+$" · {N(root,"count")} 个");_rootChoice.Items.Add("全部目录");AutomationProperties.SetAutomationId(_rootChoice,"ManageModelRoots");AutomationProperties.SetName(_rootChoice,"模型目录");_rootChoice.SelectedIndex=_rootId is null?_roots.Length:Array.FindIndex(_roots,r=>N(r,"id")==_rootId);ToolTipService.SetToolTip(_rootChoice,_rootId is long chosen?S(_roots.FirstOrDefault(r=>N(r,"id")==chosen),"path"):"全部模型目录");folders.Children.Add(_rootChoice);
  _rootChoice.SelectionChanged+=(_,_)=>{if(_rootChoice.SelectedIndex<0)return;long? root=_rootChoice.SelectedIndex<_roots.Length?N(_roots[_rootChoice.SelectedIndex],"id"):null;if(root==_rootId)return;_modelInspector=false;Invalidate();_rootId=root;_cursor=null;_history.Clear();Records.IsEnabled=false;long gen=_generation;DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low,()=>{if(gen==_generation&&_visible&&_page=="Models")_=RefreshPage();});};
  var search=new TextBox{Text=_query,PlaceholderText="搜索模型",FontSize=14*_scale,MaxLength=1024,Width=compact?148:160};_modelSearch=search;AutomationProperties.SetAutomationId(search,"ManageModelSearch");AutomationProperties.SetName(search,"搜索文件名或模型名称");ToolTipService.SetToolTip(search,_query.Length>0?_query:"搜索文件名或模型名称");folders.Children.Add(search);
  if(_modelSearchTimer is null){_modelSearchTimer=DispatcherQueue.CreateTimer();_modelSearchTimer.Interval=TimeSpan.FromMilliseconds(250);_modelSearchTimer.IsRepeating=false;_modelSearchTimer.Tick+=async(_,_)=>await SearchModels();}
  search.KeyDown+=async(_,e)=>{if(e.Key==Windows.System.VirtualKey.Enter){e.Handled=true;await SearchModels();}};
  search.TextChanged+=(_,_)=>{if(!_setting&&search.Text.Trim()!=_query){_modelSearchTimer.Stop();_modelSearchTimer.Start();}};
  folders.Children.Add(Tool("添加目录","ManagePickModels",PickAndAddModelRoot,true,()=>!_jobs.Any(j=>ModelJob(j)&&ActiveJob(j))));
  folders.Children.Add(Tool("更新资料","ManageFetchModelInfo",FetchModelInformation,true,()=>_models.Length>0&&!_jobs.Any(j=>ModelJob(j)&&ActiveJob(j))));
  folders.Children.Add(Icon("移除目录","ManageRemoveModelRoot","\uE74D",RemoveModelRoot,true,()=>_rootId is not null&&!_jobs.Any(j=>ModelJob(j)&&ActiveJob(j))));
  ModelPager.Children.Add(Icon("刷新","ManageRefresh","\uE72C",async()=>{if(_rootId is long id&&!_jobs.Any(j=>ModelJob(j)&&ActiveJob(j)))await Write("models.scan",new{rootId=id},false);await RefreshFromStart();},true));
  ModelControls.Children.Add(folders);
  var previous=Icon("上一页","ManageModelPrevious","\uE76B",async()=>{if(_history.Count>0){_modelInspector=false;Invalidate();_cursor=_history.Pop();await RefreshPage();}});previous.IsEnabled=_history.Count>0;ModelPager.Children.Add(previous);
  var next=Icon("下一页","ManageModelNext","\uE76C",async()=>{if(_next is not null){_modelInspector=false;Invalidate();_history.Push(_cursor);_cursor=_next;await RefreshPage();}});next.IsEnabled=_next is not null;ModelPager.Children.Add(next);BuildModelFilters();
  var progressRow=new Grid{Height=28,ColumnSpacing=8};progressRow.ColumnDefinitions.Add(new(){Width=new(1,GridUnitType.Star)});progressRow.ColumnDefinitions.Add(new(){Width=new(64)});
  _modelProgress=Text("");_modelProgress.FontSize=12*_scale;_modelProgress.MaxLines=1;_modelProgress.TextTrimming=TextTrimming.CharacterEllipsis;AutomationProperties.SetAutomationId(_modelProgress,"ManageModelProgress");progressRow.Children.Add(_modelProgress);
  _modelStop=Tool("停止","ManageCancelModelTask",()=>{var task=_jobs.FirstOrDefault(j=>ModelJob(j)&&ActiveJob(j));return task.ValueKind==JsonValueKind.Object?Write("job.cancel",new{jobId=S(task,"id")},false):Task.CompletedTask;},true,()=>_jobs.Any(j=>ModelJob(j)&&ActiveJob(j)));Grid.SetColumn(_modelStop,1);progressRow.Children.Add(_modelStop);_modelProgressRow=progressRow;ModelControls.Children.Add(progressRow);UpdateModelProgress();
 }
 private async Task SearchModels(){_modelSearchTimer?.Stop();if(!_visible||_page!="Models"||_modelSearch is null)return;string query=_modelSearch.Text.Trim();if(query==_query)return;_modelInspector=false;Invalidate();_query=query;_cursor=null;_history.Clear();await RefreshPage();}
 private async Task PickAndAddModelRoot()
 {
  long gen=_generation;try{var picker=new Microsoft.Windows.Storage.Pickers.FolderPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(WindowHandle)){CommitButtonText="添加"};var result=await picker.PickSingleFolderAsync();if(result is null){Status.Text="已取消添加。";Notify();return;}if(gen!=_generation||!_visible||_page!="Models")return;await AddModelRoot(result.Path);}catch(Exception){Status.Text="目录选择器打开失败。";Notify();}
 }
 private async Task AddModelRoot(string path)
 {
  var flow=_modelAdd;var state=_state;if(flow is null||state is null)return;long gen=_generation;
  bool indexed=await flow.AddAsync(path);if(!ReferenceEquals(flow,_modelAdd)||!ReferenceEquals(state,_state))return;
  if(flow.RootId is not null){AdoptAddedModelRoot(flow);LibraryChanged?.Invoke();if(gen==_generation&&_visible&&_page=="Models"){Invalidate();await RefreshPage();}}
  Status.Text=state.Uncertain?state.Feedback:flow.RootId is null?(state.Outcome=="completed"?"添加回执缺少有效目录，请刷新核对。":state.Feedback):indexed?"正在添加":"已添加"+state.Feedback;Notify();
 }
 private void AdoptAddedModelRoot(ModelDirectoryAdd flow){if(flow.RootId is not long id)return;_rootId=id;_cursor=null;_history.Clear();_query="";_modelTypes.Clear();_modelFamilies.Clear();_modelInspector=false;}
 private async Task RemoveModelRoot()
 {
  long gen=_generation;if(_rootId is not long id)return;var root=_roots.First(r=>N(r,"id")==id);
  if(await Confirm("移除目录",$"{S(root,"path")}\n取消 {N(root,"count")} 个模型")&&gen==_generation&&await Write("models.remove",new{rootId=id},false)&&gen==_generation){_rootId=null;_modelInspector=false;_cursor=null;_history.Clear();Invalidate();await RefreshPage();}
 }
 private void ModelsForm(JsonElement? model)
 {
  ClearForm();FormScroll.ChangeView(null,0,null,true);_sub="";
  if(model is{} m){
   Buttons(Button("返回模型库","ManageModelCloseDetails",CloseModelInspector));
   var heading=Text(S(m,"name"),true);AutomationProperties.SetAutomationId(heading,"ManageSelectedModel");Fields.Children.Add(heading);
   Fields.Children.Add(Text(ModelSize(N(m,"size"))+" · "+(S(m,"family")==""?"未知家族":S(m,"family"))+" · "+ModelStatus(m)));
   var file=Text(S(m,"path"));file.FontSize=12*_scale;file.TextWrapping=TextWrapping.NoWrap;file.TextTrimming=TextTrimming.CharacterEllipsis;ToolTipService.SetToolTip(file,S(m,"path"));AutomationProperties.SetName(file,"路径："+S(m,"path"));Fields.Children.Add(file);
   long id=N(m,"id");
   var information=new StackPanel{Spacing=8};Fields.Children.Add(information);_=ModelSummary(id,information);
  }else{
   string message=_roots.Length==0?"添加你的模型目录":_query.Length>0?"没有匹配的模型":_modelTotal>0?"选择一个模型查看资料":"正在准备模型";
   var empty=Text(message,true);AutomationProperties.SetAutomationId(empty,"ManageModelEmpty");Fields.Children.Add(empty);
   Fields.Children.Add(Text(_roots.Length==0?"添加目录后自动索引属性。":_query.Length>0?"清除搜索或切换目录，查看其它模型。":""));
  }
Layout();
 }
 private async Task ModelSummary(long id,StackPanel information)
 {
  long gen=_generation,form=_formRevision;try{var detail=await Read("modelDetail",new{modelId=id});if(gen!=_generation||form!=_formRevision||_sub.Length>0||!_visible||Selected is not{} selected||N(selected,"id")!=id)return;
   string baseModel=S(detail,"base_model"),type=S(detail,"type"),version=S(detail,"version_name"),name=S(detail,"model_name");
   if(name.Length>0)information.Children.Add(Text(name,true));
   information.Children.Add(Text(baseModel.Length==0?"":$"{type} · {baseModel}\n{version}"));ModelDescription(detail,information);Notify();
   if(B(detail,"cover_available")){var asset=await Read("modelCover",new{modelId=id});if(gen!=_generation||form!=_formRevision||!_visible)return;if(asset.ValueKind==JsonValueKind.Object){var bitmap=await ModelAsset.Load(_data,asset,_reads.Token);if(gen!=_generation||form!=_formRevision||!_visible)return;information.Children.Insert(0,new Image{Source=bitmap,Height=164,HorizontalAlignment=HorizontalAlignment.Left});Notify();}}
   var linked=await Read("modelImages",new{modelId=id,match="verified"});if(gen!=_generation||form!=_formRevision||!_visible)return;_linkedHeading=Text("该模型生成的图片",true);information.Children.Add(_linkedHeading);await LinkedImages(id,"verified",linked,null,gen);Notify();
  }catch(OperationCanceledException){}catch(Exception){if(gen==_generation&&form==_formRevision&&_sub.Length==0){Status.Text="模型资料读取失败，可刷新重试。";Notify();}}
 }
 private long? _editingRule;private int _registryOffset;private CheckBox? _ruleEnabled;
 private WindowsShellActions _fileActions=new(false);
 internal void SetDiagnostics(bool diagnostics){_fileActions=new(diagnostics);_verifyModelUi=diagnostics&&Environment.GetEnvironmentVariable("LUMA_MODEL_UI_TEST")=="1";}
 private static string ModelStatus(JsonElement model)=>S(model,"status") switch{"synced"=>"资料已获取","indexed"=>"尚未获取资料","changed"=>"文件已更改","sync_error_not_found"=>"未找到公开记录","sync_error_unauthorized"=>"密钥无效或已过期","sync_error_rate_limited"=>"请求频率受限","sync_error_unavailable"=>"获取失败","missing"=>"文件不存在",var status when status.StartsWith("sync_error")=>"获取失败",_=>"等待索引或获取资料"};
 private void ModelDescription(JsonElement detail,StackPanel? target=null)
 {
  target??=Fields;
  void CopyButton(string label,string id,string content,string action){var row=new TagChipPanel();row.Children.Add(Button(label,id,()=>{_fileActions.Copy(content,action);Status.Text="已"+label+"。";Notify();return Task.CompletedTask;}));target.Children.Add(row);}
  string creator=S(detail,"creator");if(creator.Length>0)target.Children.Add(Text("作者："+creator));
  foreach(var section in new[]{("模型介绍","description","ManageCopyDescription"),("版本说明","version_description","ManageCopyVersionDescription")})
  {
   string value=S(detail,section.Item2);if(value.Length==0)continue;
   target.Children.Add(Text(section.Item1,true));target.Children.Add(new ListView{Height=140,ItemsSource=TextChunkRow.Create(value,_scale),ItemTemplate=(Microsoft.UI.Xaml.DataTemplate)Resources["DescriptionTemplate"],SelectionMode=ListViewSelectionMode.None});
   CopyButton("复制"+section.Item1,section.Item3,value,"model_information");
  }
  if(detail.TryGetProperty("trigger_words",out var words)&&words.ValueKind==JsonValueKind.Array&&words.GetArrayLength()>0)
  {
   target.Children.Add(Text("模型触发词",true));string content=string.Join("、",words.EnumerateArray().Where(w=>w.ValueKind==JsonValueKind.String).Select(w=>w.GetString()));
   var field=new TextBox{Text=content,IsReadOnly=true,TextWrapping=Microsoft.UI.Xaml.TextWrapping.Wrap,MaxHeight=100,FontSize=14*_scale};AutomationProperties.SetAutomationId(field,"ManageTriggerWords");target.Children.Add(field);
   CopyButton("复制触发词","ManageCopyTriggerWords",content,"model_trigger_words");
  }
  string source=S(detail,"source_url");if(source.Length>0){var field=new TextBox{Header="Civitai来源",Text=source,IsReadOnly=true,FontSize=14*_scale};AutomationProperties.SetAutomationId(field,"ManageModelSource");target.Children.Add(field);}
 }
 private async Task ModelFileAction(string action,JsonElement model)
 {
  try{await _fileActions.ExecuteModelAsync(action,S(model,"path"));Status.Text=action=="copy"?"已复制模型路径。":_fileActions.LastAction=="folder"?"已打开模型所在目录。":"已请求资源管理器定位模型文件。";}
  catch(Luma.Core.Engine.EngineRpcException error){Status.Text=error.Code=="not_found"?"文件不存在":error.Code=="unsafe_file_action"?"路径不支持定位":"文件夹打开失败";}
  catch(Exception error){_actionError=error.GetType().Name+":"+error.HResult;Status.Text="文件夹打开失败";}Notify();
 }
 private async Task RegistryForm()
 {
  long gen=_generation;var value=await Read("registry");if(gen!=_generation)return;_registry=Rows(value);ClearForm();_modelInspector=true;_sub="registry";Fields.Children.Add(Text("模型规则",true));

  _kind=Choice("匹配方式","ManageRegistryKind",new[]{"精确名称","模型哈希"});_match=Input("名称或10至64位模型哈希","ManageRegistryMatch");_family=Choice("模型家族","ManageRegistryFamily",new[]{"Anima","Stable Diffusion","Krea 2","FLUX","NovelAI"});_note=Input("备注","ManageRegistryNote","",500);_ruleEnabled=new CheckBox{Content="启用规则",IsChecked=true,FontSize=14*_scale};Fields.Children.Add(_ruleEnabled);
  Buttons(Button("新增规则","ManageRegistryNew",()=>{_editingRule=null;_match.Text=_note.Text="";_ruleEnabled.IsChecked=true;return Task.CompletedTask;}),Button("保存规则","ManageRegistrySave",SaveRegistry,true),Button("移除选中规则","ManageRegistryRemove",RemoveRegistry,true),Button("完成","ManageRegistryReturn",RefreshPage));
  _registryOffset=Math.Clamp(_registryOffset,0,Math.Max(0,_registry.Length-1));Fields.Children.Add(Text($"{_registry.Length} 条规则 · 当前 {_registryOffset+1}–{Math.Min(_registryOffset+25,_registry.Length)}"));
  foreach(var row in _registry.Skip(_registryOffset).Take(25)){var entry=row;Fields.Children.Add(Button(S(entry,"match")+" · "+S(entry,"family"),"ManageRegistryEntry"+N(entry,"id"),()=>{_editingRule=N(entry,"id");_kind.SelectedIndex=S(entry,"kind")=="hash"?1:0;_match.Text=S(entry,"match");_note.Text=S(entry,"note");_family.SelectedItem=S(entry,"family");_ruleEnabled.IsChecked=B(entry,"enabled");return Task.CompletedTask;}));}
  Buttons(Button("上一页规则","ManageRegistryPrevious",async()=>{_registryOffset=Math.Max(0,_registryOffset-25);await RegistryForm();}),Button("下一页规则","ManageRegistryNext",async()=>{if(_registryOffset+25<_registry.Length)_registryOffset+=25;await RegistryForm();}));Layout();StateChanged();Notify();
 }
 private async Task SaveRegistry()
 {
  long gen=_generation;string match=_match?.Text.Trim()??"";if(match.Length==0){await Missing("请输入精确模型名称或模型哈希。");return;}
  string kind=_kind!.SelectedIndex==1?"hash":"name";if(kind=="hash"&&(match.Length<10||match.Length>64||!match.All(Uri.IsHexDigit))){await Missing("模型哈希应为10至64位十六进制。");return;}
  var entries=_registry.Where(e=>N(e,"id")!=_editingRule).Select(e=>JsonSerializer.Deserialize<Dictionary<string,object?>>(e.GetRawText())!).ToList();entries.Add(new(){["kind"]=kind,["match"]=match,["family"]=_family!.SelectedItem?.ToString(),["note"]=_note!.Text,["enabled"]=_ruleEnabled!.IsChecked==true});
  if(await Write("registry.save",new{entries},false)&&gen==_generation&&_visible&&_page is "Models" or "Settings"){_editingRule=null;await RegistryForm();}
 }
 private async Task RemoveRegistry(){long gen=_generation;if(_editingRule is null){await Missing("请先选择一条规则。");return;}var entries=_registry.Where(e=>N(e,"id")!=_editingRule).ToArray();if(await Write("registry.save",new{entries},false)&&gen==_generation&&_visible&&_page is "Models" or "Settings"){_editingRule=null;await RegistryForm();}}
}
