using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace Luma.Preview.Views;

public sealed partial class FilesView
{
 private Grid? _organizeRootRow;
 private Button? _scanButton,_groupNext,_groupChoices,_keep,_next;
 private string? _groupPage,_scanBeforeSubmission;
 private bool _scanPending;
 private JsonElement _scan;

 private bool CanWrite=>_loaded&&!_loading&&_localWrites==0&&_state is{Writing:false,Uncertain:false};
 private bool ShowingReview=>_mode=="duplicates"&&_reviewId is not null;
 private IEnumerable<string> RootLabels()=>_roots.Select(r=>$"{S(r,"name")} · {N(r,"count")}项");

 private async Task RunAction(Func<Task> action,bool writes)
 {
  if(writes){_localWrites++;Update();}
  try{await action();}
  catch(OperationCanceledException){}
  catch(Exception){if(_visible)Status.Text="操作未完成，请核对预览或任务状态。";}
  finally{if(writes)_localWrites--;Update();}
 }

 private static Grid FieldRow(FrameworkElement field,FrameworkElement trailing,bool equal=false)
 {
  var row=new Grid{ColumnSpacing=8};
  row.ColumnDefinitions.Add(new(){Width=new(1,GridUnitType.Star)});
  row.ColumnDefinitions.Add(new(){Width=equal?new(1,GridUnitType.Star):GridLength.Auto});
  row.Children.Add(field);Grid.SetColumn(trailing,1);row.Children.Add(trailing);
  return row;
 }

 private void BuildOrganize()
 {
  _scope=CreateChoice("整理范围","FilesScope",["请选择范围",$"图库所选 {_selection.Length} 张","指定导入目录","整个图库"]);
  _scope.SelectedIndex=_selection.Length>0?1:0;
  _scope.SelectionChanged+=(_,_)=>{if(!_setting){InvalidateDraft();ShowRootWidgets();}};
  _classification=CreateChoice("分类方式","FilesClassification",["生成模型家族","内容类别 TAG","提示词解析词规则","画师","文件格式"]);
  _classification.SelectedIndex=1;
  _classification.SelectionChanged+=(_,_)=>{if(!_setting)InvalidateDraft();};
  var scopeLabel=Text("整理范围");var classLabel=Text("分类方式");
  WorkbenchControls.Children.Add(FieldRow(scopeLabel,classLabel,true));
  WorkbenchControls.Children.Add(FieldRow(_scope,_classification,true));

  _rootAll=false;
  _root=CreateChoice("导入目录","FilesRoot",RootLabels());
  if(_root.Items.Count>0)_root.SelectedIndex=0;
  _root.SelectionChanged+=(_,_)=>{if(!_setting)InvalidateDraft();};
  AttachRootPathMenu(_root);
  var rootLabel=Text("导入目录");rootLabel.VerticalAlignment=VerticalAlignment.Center;
  _organizeRootRow=FieldRow(rootLabel,_root,true);
  WorkbenchControls.Children.Add(_organizeRootRow);ShowRootWidgets();

  _destination=CreateInput("目标目录","FilesDestination");_destination.PlaceholderText="目标目录";
  WorkbenchControls.Children.Add(FieldRow(_destination,Button("选择目录","FilesPickDestination",PickDestination)));
 }

 private void ShowRootWidgets()
 {
  if(_organizeRootRow is not null)_organizeRootRow.Visibility=_scope?.SelectedIndex==2?Visibility.Visible:Visibility.Collapsed;
 }

 private void BuildDuplicates()
 {
  _scanId=null;_scan=default;_scanPending=false;_scanBeforeSubmission=null;
  _groups=_members=[];_groupId=null;_groupPage=_groupCursor=_memberCursor=null;
  _rootAll=true;
  _root=CreateChoice("查找范围","FilesRoot",new[]{"整个图库"}.Concat(RootLabels()));
  long savedRoot=N(_stateData.GetProperty("scan"),"root_id");
  _root.SelectedIndex=savedRoot>0?Array.FindIndex(_roots,r=>N(r,"id")==savedRoot)+1:0;
  _root.SelectionChanged+=(_,_)=>{if(!_setting){_scan=default;ClearScanResults();}};
  AttachRootPathMenu(_root);
  _scanButton=Button("查找完全重复","FilesScan",Scan,true);
  WorkbenchControls.Children.Add(FieldRow(_root,_scanButton));

  _groupChoices=Button("选择…","FilesGroupActions",()=>Task.CompletedTask);
  var groupMenu=new MenuFlyout();
  var current=MenuItem("本组加入清理","FilesSelectGroup",SelectGroup,true);
  groupMenu.Items.Add(current);
  groupMenu.Items.Add(MenuItem("全部组加入清理","FilesSelectAll",()=>SelectAll(true),true));
  groupMenu.Items.Add(MenuItem("取消全部选择","FilesSelectNone",()=>SelectAll(false),true));
  groupMenu.Opening+=(_,_)=>{current.Text=B(CurrentGroup(),"selected")?"本组退出清理":"本组加入清理";current.IsEnabled=_groupId is not null;};
  _groupChoices.Flyout=groupMenu;
  _groupNext=Button("下一页","FilesGroupNext",async()=>{if(_groupCursor is not null)await LoadGroups(_groupCursor);});
  GroupActions.Children.Add(Text("重复组"));GroupActions.Children.Add(_groupChoices);GroupActions.Children.Add(_groupNext);
  _keep=Button("设为保留","FilesKeep",Keep,true);
  PageActions.Children.Add(_keep);
 }

 private MenuFlyoutItem MenuItem(string title,string id,Func<Task> action,bool writes=false)
 {
  var item=new MenuFlyoutItem{Text=title,FontSize=14*_scale};
  AutomationProperties.SetAutomationId(item,id);
  item.Click+=async(_,_)=>{if(!writes||CanWrite)await RunAction(action,writes);};
  return item;
 }

 private void BuildReviewActions()
 {
  _generate=_execute=_next=null;
  if(_mode=="rules"){RulesPager.Children.Insert(0,Button("刷新记录","FilesRefresh",LoadPage));return;}
  if(_mode is "organize" or "duplicates" or "roots")
  {
   _generate=Button(_mode=="duplicates"?"生成隔离预览":"生成预览","FilesGenerate",()=>Generate(DefaultKind()),true);
   Actions.Children.Add(_generate);
  }
  _execute=Button("确认并执行","FilesExecute",Execute,true);Actions.Children.Add(_execute);
  _next=Button("下一页","FilesNext",NextPage);PageActions.Children.Add(_next);

  var options=Button("记录","FilesReviewOptions",()=>Task.CompletedTask);
  var menu=new MenuFlyout();
  var recent=Rows(_stateData.GetProperty("reviews")).Where(r=>MatchesMode(S(r,"kind"))).ToArray();
  var back=MenuItem("返回重复组","FilesReturnResults",ReturnToResults);
  var discard=MenuItem("作废预览","FilesDiscard",Discard,true);
  menu.Items.Add(back);menu.Items.Add(discard);
  var refresh=MenuItem("刷新记录","FilesRefresh",()=>CanRefreshRecords?LoadPage():Task.CompletedTask);
  menu.Items.Add(refresh);
  if(recent.Length>0)menu.Items.Add(new MenuFlyoutSeparator());
  for(int i=0;i<recent.Length;i++)
  {
   var row=recent[i];
   string caption=$"载入{KindName(S(row,"kind"))} · {StatusName(S(row,"state"))} · {N(row,"total")}项";
   menu.Items.Add(MenuItem(caption,$"FilesLoadRecent{i}",()=>LoadSaved(S(row,"id"))));
  }
  menu.Opening+=(_,_)=>
  {
   back.Visibility=ShowingReview?Visibility.Visible:Visibility.Collapsed;
   discard.Visibility=_review.Review is{State:"ready"}?Visibility.Visible:Visibility.Collapsed;
   discard.IsEnabled=CanWrite;
   refresh.IsEnabled=CanRefreshRecords;
  };
  options.Flyout=menu;Actions.Children.Add(options);
 }

 private bool MatchesMode(string kind)=>_mode switch
 {
  "rules"=>false,
  "recovery"=>kind is "organize_undo" or "recover_moves" or "recover_duplicates",
  "quarantine"=>kind is "duplicate_undo" or "duplicate_purge",
  _=>kind==DefaultKind()
 };
 private bool CanRefreshRecords=>!_loading&&!_scanPending&&_state?.Writing!=true;
 private bool CanConfirmCurrentReview=>_reviewItemsReady&&Entries.Items.Count>0&&_review.CanConfirm&&_review.Review is{} review&&MatchesMode(review.Kind);

 private void UpdateReviewActions(bool writable)
 {
  bool canConfirm=CanConfirmCurrentReview;
  if(_execute is not null)
  {
   _execute.Visibility=canConfirm?Visibility.Visible:Visibility.Collapsed;
   _execute.IsEnabled=writable&&canConfirm;
   _execute.Content=_review.Review?.Kind switch
   {
    "organize_undo" or "duplicate_undo"=>"确认还原","root_remove"=>"确认移除索引",
    "duplicate_cleanup"=>"确认隔离","duplicate_purge"=>"永久清空",
    "recover_moves" or "recover_duplicates"=>"确认恢复检查",_=>"确认移动"
   };
  }
  if(_generate is not null)
  {
   _generate.Visibility=canConfirm?Visibility.Collapsed:Visibility.Visible;
   _generate.IsEnabled=writable&&_previewJobId is null&&(_mode switch
   {
    "duplicates"=>_scanId is not null&&_groups.Length>0&&!_scanPending,
    "organize"=>_scope?.SelectedIndex>0&&!string.IsNullOrWhiteSpace(_destination?.Text)&&(_scope?.SelectedIndex!=2||RootId() is not null),
    _=>true
   });
  }
  if(_next is not null)
  {
   bool hasNext=_reviewId is not null?_itemCursor is not null:_mode=="duplicates"&&_memberCursor is not null;
   _next.Visibility=hasNext?Visibility.Visible:Visibility.Collapsed;
   _next.Content=_reviewId is null?"下一页副本":"下一页";
  }
  bool showGroups=_mode=="duplicates"&&_scanId is not null&&!ShowingReview;
  var groupVisibility=showGroups?Visibility.Visible:Visibility.Collapsed;
  if(GroupsPane.Visibility!=groupVisibility){GroupsPane.Visibility=groupVisibility;LayoutColumns();}
  if(_mode!="duplicates")return;
  WorkbenchControls.Visibility=ShowingReview?Visibility.Collapsed:Visibility.Visible;
  if(_root is not null)_root.IsEnabled=!_scanPending;
  if(_scanButton is not null){_scanButton.IsEnabled=writable&&!_scanPending;_scanButton.Content=_scanPending?"正在查找…":"查找完全重复";}
  if(_groupChoices is not null)_groupChoices.IsEnabled=writable&&showGroups&&_groups.Length>0;
  if(_groupNext is not null)_groupNext.Visibility=_groupCursor is not null?Visibility.Visible:Visibility.Collapsed;
  if(_keep is not null)
  {
   _keep.Visibility=showGroups&&_groupId is not null?Visibility.Visible:Visibility.Collapsed;
   _keep.IsEnabled=writable&&_selectedEntry is{} member&&!B(member,"is_keep");
  }
 }

 private void AttachRootPathMenu(ComboBox box)
 {
  var menu=new MenuFlyout();
  var paths=MenuItem("查看目录完整路径","FilesRootPathMenu",async()=>
  {
   if(RootId() is not long id)return;
   long gen=_generation;var value=await Read("files.root",new{rootId=id});
   if(gen==_generation&&RootId()==id)await ShowPaths("目录完整路径",S(value,"path"),"FilesRootPath");
  });
  menu.Items.Add(paths);
  menu.Opening+=(_,_)=>paths.IsEnabled=RootId() is not null;
  box.ContextFlyout=menu;
 }

 private void EntryContainerChanging(ListViewBase sender,ContainerContentChangingEventArgs args)
 {
  var container=args.ItemContainer;
  if(args.InRecycleQueue||container.ContextFlyout is not null)return;
  JsonElement? Item()=>container.Content is FileRow row?row.Value:null;
  var menu=new MenuFlyout();
  menu.Items.Add(MenuItem("查看完整路径","FilesTogglePaths",()=>ShowEntryPaths(Item())));
  var source=MenuItem("复制路径","FilesCopySource",()=>CopyEntryPath(Item(),"source"));
  var target=MenuItem("复制目标路径","FilesCopyTarget",()=>CopyEntryPath(Item(),"target"));
  var keep=MenuItem("复制保留路径","FilesCopyKeep",()=>CopyEntryPath(Item(),"keep_path"));
  menu.Items.Add(source);menu.Items.Add(target);menu.Items.Add(keep);
  menu.Opening+=(_,_)=>
  {
   var item=Item();
   source.Text=item is{} value&&S(value,"source").Length>0?"复制来源路径":"复制路径";
   target.Visibility=EntryPath(item,"target").Length>0?Visibility.Visible:Visibility.Collapsed;
   keep.Visibility=EntryPath(item,"keep_path").Length>0?Visibility.Visible:Visibility.Collapsed;
  };
  container.ContextFlyout=menu;
 }

 private static string EntryPath(JsonElement? item,string key)
 {
  if(item is not{} value)return "";
  string path=S(value,key);
  return key=="source"&&path.Length==0?S(value,"path"):path;
 }

 private Task CopyEntryPath(JsonElement? item,string key)
 {
  string path=EntryPath(item,key);
  if(path.Length>0){_copy.Copy(path,"file_preview_path");Status.Text="已复制完整路径。";}
  return Task.CompletedTask;
 }

 private Task ShowEntryPaths(JsonElement? item)
 {
  if(item is null)return Task.CompletedTask;
  string source=EntryPath(item,"source"),target=EntryPath(item,"target"),keep=EntryPath(item,"keep_path");
  string path=S(item.Value,"source").Length>0?"来源\r\n"+source:source;
  if(target.Length>0)path+="\r\n目标\r\n"+target;
  if(keep.Length>0)path+="\r\n保留\r\n"+keep;
  return ShowPaths("完整路径",path,"FilesFullPaths");
 }

 private async Task ShowPaths(string title,string value,string id)
 {
  var text=CreateInput(title,id,value,false);text.AcceptsReturn=true;text.MinHeight=64;text.MaxHeight=240;
  ScrollViewer.SetHorizontalScrollBarVisibility(text,ScrollBarVisibility.Auto);
  ScrollViewer.SetVerticalScrollBarVisibility(text,ScrollBarVisibility.Auto);
  await new ContentDialog{Title=title,Content=text,CloseButtonText="关闭",XamlRoot=XamlRoot}.ShowAsync();
 }

 private void SetPoll()
 {
  bool enabled=_visible&&(_previewJobId is not null||(_mode=="duplicates"&&_scanPending));
  if(enabled&&!_poll.IsRunning)_poll.Start();
  else if(!enabled&&_poll.IsRunning)_poll.Stop();
 }

 private async Task PollActivity()
 {
  if(!_visible||_loading||_polling)return;
  _polling=true;
  try
  {
   if(_previewJobId is not null)await RefreshReview();
   if(_mode=="duplicates"&&_scanPending&&_state is{Writing:false})await ReadScan();
  }
  finally{_polling=false;}
 }

 private void ClearScanResults()
 {
  _scanId=null;_groups=_members=[];_groupId=null;_groupPage=_groupCursor=_memberCursor=null;
  GroupsList.ItemsSource=null;
  InvalidateDraft();
 }

 private async Task Scan()
 {
  var rootId=RootId();long gen=_generation;
  _scanBeforeSubmission=S(_stateData.GetProperty("scan"),"id");
  if(S(_scan,"id").Length>0)_scanBeforeSubmission=S(_scan,"id");
  _scanPending=true;_scan=default;ClearScanResults();
  if(await Write("files.scan",new{rootId}))
  {
   if(gen==_generation&&_visible){Status.Text="正在查找完全重复图片…";SetPoll();}
  }
  else if(gen==_generation){_scanBeforeSubmission=null;_scanPending=false;SetPoll();}
 }

 private async Task ReadScan()
 {
  long gen=_generation;
  try
  {
   var state=await Read("files.state");
   if(gen!=_generation||!_visible||_mode!="duplicates")return;
   var scan=state.GetProperty("scan");
   if(_scanBeforeSubmission is not null&&S(scan,"id")==_scanBeforeSubmission)return;
   _stateData=state;
   await ApplyScan(scan);
  }
  catch(OperationCanceledException){}
  catch(Exception){if(gen==_generation&&_visible){Status.Text="暂无法读取检查结果。";Update();}}
 }

 private async Task ApplyScan(JsonElement scan)
 {
  if(scan.ValueKind!=JsonValueKind.Object||(N(scan,"root_id")>0?N(scan,"root_id"):(long?)null)!=RootId())
  {
   _scanPending=false;EndScanUpdate("");return;
  }
  string status=S(scan,"status");
  _scan=scan;
  if(status is "queued" or "running"){_scanPending=true;EndScanUpdate("");return;}
  if(status=="completed")
  {
   long gen=_generation;
   _scanId=S(scan,"id");await LoadGroups();
   if(gen!=_generation||_scanId!=S(scan,"id"))return;
   _scanPending=false;EndScanUpdate(_groups.Length==0?"没有发现完全重复的图片。":"");return;
  }
  _scanPending=false;
  EndScanUpdate(status switch{"cancelled"=>"检查已取消。","interrupted"=>"检查已中断，请重新查找。",_=>"检查未完成，请重新查找。"});
 }

 private void EndScanUpdate(string feedback)
 {
  if(!_scanPending)_scanBeforeSubmission=null;
  Status.Text=feedback;SetPoll();RenderReviewTitle();Update();
 }

 private string ScanSummary()
 {
  if(_scan.ValueKind!=JsonValueKind.Object)return "";
  long checkedCount=N(_scan,"checked");
  return _scanPending?$"已检查 {checkedCount} / {N(_scan,"total")} 份":$"已检查 {checkedCount} 份 · {N(_scan,"groups")} 组 · 跳过 {N(_scan,"skipped")} 份";
 }

 private string DuplicateReviewScope(string scanId)
 {
  if(scanId.Length==0)return "已保存的重复组";
  var scan=_scan;
  if(S(scan,"id")!=scanId&&_stateData.ValueKind==JsonValueKind.Object)
   scan=_stateData.GetProperty("scan");
  if(S(scan,"id")!=scanId)return "已保存的重复组";
  long root=N(scan,"root_id");
  if(root==0)return "整个图库";
  string name=S(_roots.FirstOrDefault(r=>N(r,"id")==root),"name");
  return name.Length>0?name:"已保存的重复组";
 }

 private JsonElement CurrentGroup()=>_groups.FirstOrDefault(g=>N(g,"id")==_groupId);

 private async Task LoadGroups(string? cursor=null,long? selected=null)
 {
  if(_scanId is not{} scanId)return;
  long gen=_generation,version=_review.Version;
  var value=await Read("files.duplicate.groups",new{scanId,cursor});
  if(gen!=_generation||version!=_review.Version||scanId!=_scanId||!_visible)return;
  _groups=Rows(value.GetProperty("items"));_groupPage=cursor;_groupCursor=S(value,"next_cursor");
  if(string.IsNullOrEmpty(_groupCursor))_groupCursor=null;
  _setting=true;
  try
  {
   GroupsList.ItemsSource=_groups.Select(r=>new FileRow(r,(B(r,"selected")?"已加入 · ":"")+N(r,"member_count")+" 份完全重复\n保留："+S(r,"name"),14*_scale)).ToArray();
   int index=Array.FindIndex(_groups,r=>N(r,"id")==selected);
   GroupsList.SelectedIndex=_groups.Length==0?-1:Math.Max(0,index);
  }
  finally{_setting=false;}
  if(GroupsList.SelectedItem is FileRow row)await ChooseGroup(N(row.Value,"id"));
  else{_groupId=null;_members=[];_memberCursor=null;Entries.ItemsSource=null;RenderReviewTitle();Update();}
 }

 private async void GroupSelected(object sender,SelectionChangedEventArgs args)
 {
  if(_setting||ShowingReview||GroupsList.SelectedItem is not FileRow row)return;
  await RunAction(()=>ChooseGroup(N(row.Value,"id")),false);
 }

 private Task ChooseGroup(long id)
 {
  _groupId=id;_members=[];_memberCursor=null;InvalidateDraft();
  return LoadMembers();
 }

 private async Task LoadMembers(string? cursor=null)
 {
  if(_groupId is not long id)return;
  long gen=_generation,version=_review.Version;
  var value=await Read("files.duplicate.members",new{groupId=id,cursor});
  if(gen!=_generation||version!=_review.Version||id!=_groupId||!_visible)return;
  _members=Rows(value.GetProperty("items"));_memberCursor=S(value,"next_cursor");
  if(string.IsNullOrEmpty(_memberCursor))_memberCursor=null;
  Entries.ItemsSource=_members.Select(r=>new FileRow(r,(B(r,"is_keep")?"保留 · ":"副本 · ")+S(r,"name"),14*_scale)).ToArray();
  if(_members.Length>0)Entries.SelectedIndex=0;
  RenderReviewTitle();Update();
 }

 private async Task ReturnToResults()
 {
  InvalidateDraft();
  if(_scanId is not null)await LoadGroups(_groupPage,_groupId);
  else if(_stateData.ValueKind==JsonValueKind.Object)await ApplyScan(_stateData.GetProperty("scan"));
 }

 private Task SelectGroup()=>_groupId is long id?EditGroups("files.duplicate.select",new{groupId=id,selected=!B(CurrentGroup(),"selected")}):Task.CompletedTask;
 private Task SelectAll(bool selected)=>_scanId is{} scanId?EditGroups("files.duplicate.all",new{scanId,selected}):Task.CompletedTask;
 private Task Keep()=>_groupId is long id&&_selectedEntry is{} member&&!B(member,"is_keep")?EditGroups("files.duplicate.keep",new{groupId=id,imageId=N(member,"id")}):Task.CompletedTask;

 private async Task EditGroups(string action,object args)
 {
  long gen=_generation;InvalidateDraft();
  if(await Write(action,args))
  {
   if(gen==_generation&&_visible)await LoadGroups(_groupPage,_groupId);
  }
  else if(gen==_generation&&_visible)await LoadMembers();
 }
}
