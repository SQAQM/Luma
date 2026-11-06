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
  check(_jobStatus?.Text.Contains("当前文件哈希未匹配：1 个")==true,"task shows persistent not-found reason");
  check(_jobStatus!.Text.Contains("67 / 67"),"task total matches directory");
  var bounds=_jobStatus.TransformToVisual(FormScroll).TransformBounds(new(0,0,_jobStatus.ActualWidth,_jobStatus.ActualHeight));
  check(bounds.Right<=FormScroll.ActualWidth+1,"error details stay within content width");
 }
}
