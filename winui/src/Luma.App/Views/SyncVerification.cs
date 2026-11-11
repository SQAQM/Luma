using System.Text.Json;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;
public sealed partial class ShellView
{
 internal async Task VerifySyncUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_SYNC_UI_TEST")!="1")return;
  var checks=new List<string>();void Check(bool value,string name){if(!value)throw new InvalidOperationException(name);checks.Add(name);}
  async Task Until(Func<bool> test){for(int i=0;i<150;i++){if(test())return;await Task.Delay(100);}throw new TimeoutException("Own sync fixture timed out");}
  async Task Capture(string name){await capture();string file=Directory.EnumerateFiles(directory,"capture-*.png").MaxBy(File.GetLastWriteTimeUtc)!;File.Copy(file,Path.Combine(directory,name+".png"),true);}
  try{
   // Seed only this newly created diagnostic profile, before connecting its engine.
   string fixture=directory+"-fixtures";using var pointer=JsonDocument.Parse(File.ReadAllText(Path.Combine(fixture,"winui-library-v1.json")));
   string name=pointer.RootElement.GetProperty("directory").GetString()??"";
   if(!System.Text.RegularExpressions.Regex.IsMatch(name,"^engine-probe-[a-f0-9]{32}$"))throw new InvalidOperationException("Own fixture identity missing");
   string own=Path.Combine(directory,name);Directory.CreateDirectory(own);
   foreach(string file in Directory.EnumerateFiles(Path.Combine(fixture,name)))File.Copy(file,Path.Combine(own,Path.GetFileName(file)),false);
   File.Copy(Path.Combine(fixture,"winui-library-v1.json"),Path.Combine(directory,"winui-library-v1.json"),false);
   Navigate(WorkspacePage.Models);await Until(()=>_engine?.Connected==true&&Management.SyncModelsReady);
   await Management.VerifySyncCount(Check,()=>Capture("scope-count"));
   Navigate(WorkspacePage.Jobs);await Until(()=>Management.SyncIssueReady);
   Management.VerifySyncIssues(Check);await Capture("sync-reason");
   if(Environment.GetEnvironmentVariable("LUMA_SYNC_RETRY_UI_TEST")=="1"){
    await Management.VerifyTaskStates(Check,Capture);
    await Management.VerifyFailedRetry(Check,Capture);
    await _engine!.StopAsync();
    await Until(()=>!_engine.IsAlive&&StatePanel.Visibility==Visibility.Visible);
    Check(StateAction.Visibility==Visibility.Visible&&StateAction.Content?.ToString()=="重试连接"&&StateAction.IsEnabled,"disconnected task page exposes recovery action");
    var recovery=new Microsoft.UI.Xaml.Automation.Peers.ButtonAutomationPeer(StateAction);
    ((Microsoft.UI.Xaml.Automation.Provider.IInvokeProvider)recovery.GetPattern(Microsoft.UI.Xaml.Automation.Peers.PatternInterface.Invoke)).Invoke();
    await Until(()=>_engine.Connected&&Management.SyncIssueReady);
    Check(Management.IsShowing("Jobs")&&StatePanel.Visibility==Visibility.Collapsed,"fault-state retry reconnects directly to task records");await Capture("task-reconnected");
   }
   File.WriteAllText(Path.Combine(directory,"sync-ui-report.json"),JsonSerializer.Serialize(new{result="passed_sync_ui",checks,privateDataRead=false,networkUsed=false}));
  }catch(Exception e){File.WriteAllText(Path.Combine(directory,"sync-ui-report.json"),JsonSerializer.Serialize(new{result="failed_sync_ui",checks,error=e.ToString()}));}
 }
}
public sealed partial class ManagementView
{
 internal bool SyncModelsReady=>_page=="Models"&&!_formLoading&&_modelTotal==67&&_models.Length==60;
 internal bool SyncIssueReady=>_page=="Jobs"&&!_formLoading&&_jobSelection.Value is{} job&&S(job,"kind")=="model_sync";
 internal async Task VerifySyncCount(Action<bool,string> check,Func<Task> capture)
 {
  Records.SelectedItems.Clear();var request=CurrentModelSyncRequest();
  check(request.Count==67,"native update includes all 67 models");
  check(JsonSerializer.SerializeToElement(request.Args).TryGetProperty("scope",out _),"native update sends filter scope");
  var first=Records.Items[0];Records.SelectedItems.Add(first);check(CurrentModelSyncRequest().Count==1,"explicit single selection stays single");Records.SelectedItems.Clear();
  var dialog=Confirmation("更新资料",$"{request.Range} {request.Count} 个模型");var shown=dialog.ShowAsync();
  try{await Task.Delay(300);check(dialog.Content?.ToString()?.Contains("67")==true,"confirmation displays 67");await capture();}finally{dialog.Hide();await shown;}
 }
 internal void VerifySyncIssues(Action<bool,string> check)
 {
  check(_jobIssues?.Text.Contains("当前文件哈希未匹配：1 个")==true,"task shows persistent not-found reason");
  check(_jobStatus!.Text.Contains("67 / 67"),"task total matches directory");
  check(Math.Abs(_jobStatus.FontSize-14*_scale)<.1,"task progress uses body size instead of heading size");
  IEnumerable<FrameworkElement> Descendants(DependencyObject root){for(int i=0;i<Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChildrenCount(root);i++){var child=Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChild(root,i);if(child is FrameworkElement element)yield return element;foreach(var nested in Descendants(child))yield return nested;}}
  var controls=Descendants(Form).ToArray();
  check(!controls.Any(e=>Microsoft.UI.Xaml.Automation.AutomationProperties.GetAutomationId(e) is "EngineState" or "EngineDisconnect" or "EngineFeedback"),"tasks contain no service controls or connection explanation");
  check(!controls.OfType<Button>().Any(b=>Microsoft.UI.Xaml.Automation.AutomationProperties.GetAutomationId(b)=="ManageCancelJob"&&b.Visibility==Visibility.Visible),"finished task has no cancel action");
  check(_jobHeading?.Text=="模型资料同步"&&!_jobHeading.Text.Contains("67"),"selected task title is separate from progress and errors");
  check(_jobErrors?.Visibility==Visibility.Visible&&_jobIssues!.FontSize==14*_scale,"persistent errors form a body-size group");
  var bounds=_jobIssues!.TransformToVisual(FormScroll).TransformBounds(new(0,0,_jobIssues.ActualWidth,_jobIssues.ActualHeight));
  check(bounds.Right<=FormScroll.ActualWidth+1,"error details stay within content width");
 }
 internal async Task VerifyTaskStates(Action<bool,string> check,Func<string,Task> capture)
 {
  var saved=_jobs;var selected=_jobSelection.Value??throw new InvalidOperationException("Own sync task missing");_poll.Stop();
  void Show(JsonElement sample){_jobs=[sample];_setting=true;try{Records.ItemsSource=new[]{new ManagementRow(sample,JobText(sample),14*_scale)};Records.SelectedIndex=0;}finally{_setting=false;}JobsForm(sample);UpdateLayout();}
  JsonElement Sample(string status,long processed,long errors,long retryable,object[] issues)=>JsonSerializer.SerializeToElement(new{id=S(selected,"id"),kind="model_sync",status,total=67,processed,errors,retryable_count=retryable,issues});
  try{
   Show(Sample("running",2,0,0,[]));
   check(_cancelJob?.Visibility==Visibility.Visible&&_cancelJob.IsEnabled,"running task exposes cancel action");
   check(_retryJob?.Visibility==Visibility.Collapsed&&_jobErrors?.Visibility==Visibility.Collapsed,"running clean task hides retry and empty errors");
   check(_jobProgress?.Value==2&&_jobProgress.Maximum==67,"native progress bar follows selected task counts");await capture("task-running");
   Show(Sample("completed",67,0,0,[]));
   check(_cancelJob?.Visibility==Visibility.Collapsed&&_retryJob?.Visibility==Visibility.Collapsed,"successful task exposes no obsolete actions");
   check(_jobErrors?.Visibility==Visibility.Collapsed,"successful task has no empty error group");await capture("task-completed");
   string[] codes=["sidecar_busy","sidecar_permission","sidecar_format","sidecar_size","sidecar_conflict","sidecar_space","sidecar_write"];
   Show(Sample("completed_with_errors",67,7,7,codes.Select(code=>(object)new{code,count=1}).ToArray()));
   check(_retryJob?.Visibility==Visibility.Visible&&_retryJob.IsEnabled&&_cancelJob?.Visibility==Visibility.Collapsed,"failed sync has one retry action and no cancel");
   check(_jobIssues?.Text.Split('\n').Length==7&&!_jobIssues.Text.Contains("sidecar_"),"seven sidecar failures render fixed labels in native error group");
   var buttons=Form.Children.OfType<Controls.TagChipPanel>().SelectMany(p=>p.Children.OfType<Button>()).Where(b=>b.Visibility==Visibility.Visible).ToArray();
   check(buttons.Length==1&&ReferenceEquals(buttons[0],_retryJob),"failed sync exposes exactly one task action");await capture("task-errors");
   var actionBounds=_retryJob!.TransformToVisual(FormScroll).TransformBounds(new(0,0,_retryJob.ActualWidth,_retryJob.ActualHeight));
   check(actionBounds.Top>=0&&actionBounds.Bottom<=FormScroll.ActualHeight+1,"retry action stays fully visible before scrolling error details");
   Show(JsonSerializer.SerializeToElement(new{id=S(selected,"id"),kind="model_sync",status="completed_with_errors",total=67,processed=67,errors=1,issues=new[]{new{code="sidecar_write",count=1}}}));
   check(_retryJob?.Visibility==Visibility.Collapsed,"legacy task without persisted failure ids hides retry");
  }finally{
   _jobs=saved;_setting=true;try{Records.ItemsSource=saved.Select(j=>new ManagementRow(j,JobText(j),14*_scale)).ToArray();Records.SelectedIndex=Array.FindIndex(saved,j=>S(j,"id")==S(selected,"id"));}finally{_setting=false;}JobsForm(selected);_poll.Start();UpdateLayout();
  }
 }
 internal async Task VerifyFailedRetry(Action<bool,string> check,Func<string,Task> capture)
 {
  string previous=_jobSelection.Value is{} old?S(old,"id"):"";
  check(_jobSelection.CanRetryFailed&&_retryJob?.Visibility==Visibility.Visible&&_retryJob.IsEnabled,"persisted failed item enables real native retry button");
  var peer=new Microsoft.UI.Xaml.Automation.Peers.ButtonAutomationPeer(_retryJob!);
  ((Microsoft.UI.Xaml.Automation.Provider.IInvokeProvider)peer.GetPattern(Microsoft.UI.Xaml.Automation.Peers.PatternInterface.Invoke)).Invoke();
  bool settled=false;for(int i=0;i<150;i++){await Task.Delay(100);if(_jobSelection.Value is{} current&&S(current,"id")!=previous&&S(current,"status") is not ("queued" or "running")){settled=true;break;}}
  check(settled,"native retry submits RPC and selects the new finished task");
  var job=_jobSelection.Value!.Value;
  check(N(job,"total")==1&&N(job,"processed")==1&&N(job,"errors")==1,"real retry processes one failed item instead of all 67 models");
  check(_jobIssues?.Text=="模型文件不存在：1 个","retry fails locally before a network request");
  check(_cancelJob?.Visibility==Visibility.Collapsed,"finished retry hides cancel action");await capture("task-retry");
 }
}
