using System.Text.Json;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;

// Intrinsic Button.Invoke probes, restricted to a newly created diagnostic profile.
// They exercise the production Click handlers and RPC channel without OS input automation.
public sealed partial class ShellView
{
 internal async Task VerifyModelPagingUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_MODEL_PAGING_UI_TEST")!="1")return;
  var checks=new List<object>();
  try{
   string fixture=directory+"-fixtures";using var pointer=JsonDocument.Parse(File.ReadAllText(Path.Combine(fixture,"winui-library-v1.json")));
   string name=pointer.RootElement.GetProperty("directory").GetString()??"";
   if(!System.Text.RegularExpressions.Regex.IsMatch(name,"^engine-probe-[a-f0-9]{32}$"))throw new InvalidOperationException("Own fixture identity missing");
   string own=Path.Combine(directory,name);Directory.CreateDirectory(own);
   foreach(string file in Directory.EnumerateFiles(Path.Combine(fixture,name)))File.Copy(file,Path.Combine(own,Path.GetFileName(file)),false);
   File.Copy(Path.Combine(fixture,"winui-library-v1.json"),Path.Combine(directory,"winui-library-v1.json"),false);
   Navigate(WorkspacePage.Models);
   async Task Capture(string label){await capture();string file=Directory.EnumerateFiles(directory,"capture-*.png").MaxBy(File.GetLastWriteTimeUtc)!;File.Copy(file,Path.Combine(directory,label+".png"),false);}
   await Management.VerifyModelPagingUi(checks,Capture);
   await capture();
   File.WriteAllText(Path.Combine(directory,"model-paging-ui-report.json"),JsonSerializer.Serialize(new{result=checks.All(c=>JsonSerializer.SerializeToElement(c).GetProperty("passed").GetBoolean())?"passed_model_paging_ui":"failed_model_paging_ui",checks,privateDataRead=false,networkUsed=false}));
  }catch(Exception error){File.WriteAllText(Path.Combine(directory,"model-paging-ui-report.json"),JsonSerializer.Serialize(new{result="failed_model_paging_ui",checks,error=error.ToString()}));}
 }
}
public sealed partial class ManagementView
{
 internal async Task VerifyModelPagingUi(List<object> checks,Func<string,Task> capture)
 {
  async Task Until(Func<bool> predicate){for(int i=0;i<200;i++){if(predicate())return;await Task.Delay(50);}throw new TimeoutException("Own paging fixture timed out");}
  void Check(bool condition,string message){if(!condition)throw new InvalidOperationException(message);}
  Button Pager(string id)=>ModelPager.Children.OfType<Button>().Single(b=>AutomationProperties.GetAutomationId(b)==id);
  async Task Click(string id){var button=Pager(id);Check(button.IsEnabled,id+" must be enabled");long generation=_generation;((IInvokeProvider)new ButtonAutomationPeer(button).GetPattern(PatternInterface.Invoke)).Invoke();await Until(()=>_generation>generation&&!_loading&&!_formLoading);}
  async Task Case(string name,Func<Task> action){try{await action();checks.Add(new{name,passed=true,count=_models.Length,page=_history.Count+1,total=_modelTotal});}catch(Exception error){checks.Add(new{name,passed=false,count=_models.Length,page=_history.Count+1,total=_modelTotal,feedback=Status.Text,error=error.Message});}}
  await Until(()=>_visible&&_page=="Models"&&_api is not null&&!_loading&&!_formLoading&&_modelTotal==67);
  long[] first=[];
  await Case("next button reaches final seven models",async()=>{
   Check(_models.Length==60&&_history.Count==0,"first page is 60 of 67");first=_models.Select(m=>N(m,"id")).ToArray();
   Check(!Pager("ManageModelPrevious").IsEnabled,"previous disabled on first page");
   await Click("ManageModelNext");Check(_models.Length==7&&_history.Count==1,"next must display page 2 with seven models");Check(!_models.Any(m=>first.Contains(N(m,"id"))),"page 2 must not repeat page 1");Check(!Pager("ManageModelNext").IsEnabled,"next disabled on last page");await capture("page-2");
  });
  await Case("previous button restores first sixty models",async()=>{await Click("ManageModelPrevious");Check(_models.Length==60&&_history.Count==0&&_models.Select(m=>N(m,"id")).SequenceEqual(first),"previous must restore page 1");await capture("page-1");});
  await RefreshFromStart();
  await Case("same page presentation preserves current cursor",async()=>{await Click("ManageModelNext");ShowPage("Models");await Until(()=>!_loading&&!_formLoading);Check(_models.Length==7&&_history.Count==1,"presenting the same Models page must retain page 2");});
  await RefreshFromStart();
  await Case("unrelated image tag revision does not block next",async()=>{
   // Updating only synthetic tag definitions changes the global image query revision.
   var tags=Rows(await Read("tags")).Select(t=>JsonSerializer.Deserialize<Dictionary<string,object?>>(t.GetRawText())!).ToArray();
   tags[0]["threshold"]=.37;await _api!.WriteAsync("tags.save",new{definitions=tags},Guid.NewGuid().ToString("N"),CancellationToken.None);
   await Click("ManageModelNext");Check(_models.Length==7&&_history.Count==1,"unrelated tag edits must not silently return to page 1");
  });
  await RefreshFromStart();
  await Case("failed page read retains current page number",async()=>{
   string? next=_next;try{_next="invalid-cursor";await Click("ManageModelNext");Check(_models.Length==60&&_history.Count==0&&_cursor is null,"failed page read must keep page 1 and its navigation state");Check(Status.Text.Length>0,"failed page read must show feedback");}finally{_next=next;Notify();}
  });
  await RefreshFromStart();
  await Case("model change recovers stale cursor with feedback",async()=>{
   long root=N(_roots[0],"id");var job=await _api!.WriteAsync("models.scan",new{rootId=root},Guid.NewGuid().ToString("N"),CancellationToken.None);string id=S(job,"id");
   for(int i=0;i<200;i++){var jobs=Rows(await Read("jobs"));if(jobs.Any(j=>S(j,"id")==id&&!ActiveJob(j)))break;await Task.Delay(50);}
   await Click("ManageModelNext");Check(_models.Length==60&&_history.Count==0&&_cursor is null,"expired model snapshot must visibly return to page 1");Check(Status.Text.Contains("已更新")&&Status.Text.Contains("1"),"stale recovery must explain the first page reset");await capture("cursor-recovered");
  });
  await RefreshFromStart();
  await Case("refresh resets cursor and history",async()=>{await Click("ManageModelNext");await RefreshFromStart();Check(_models.Length==60&&_history.Count==0&&_cursor is null,"refresh must reset to first page");});
 }
}
