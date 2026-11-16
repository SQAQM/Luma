using System.Text.Json;
using Luma.Core.Contracts;
using Luma.Core.Gallery;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Luma.Preview.Views;

public sealed partial class ShellView
{
 internal void SetWindowPerformanceTheme(bool dark)=>ThemeClicked(dark?DarkButton:LightButton,new RoutedEventArgs());
 internal async Task VerifyWindowIdlePerformance(string directory,Action<bool,string> check,Action<object> sample)
 {
  async Task Until(Func<Task<bool>> condition){for(int i=0;i<200;i++){if(await condition())return;await Task.Delay(50);}throw new TimeoutException("Own idle fixture timed out");}
  Navigate(WorkspacePage.Models);await Until(()=>Task.FromResult(_engine?.Connected==true&&_managementState is not null));
  string images=Path.Combine(directory+"-fixtures","images");Directory.CreateDirectory(images);await SyntheticPng(Path.Combine(images,"idle.png"),0);
  if(!await _managementState!.WriteAsync("catalog.import",new{path=images,recursive=true}))throw new InvalidOperationException("Own import failed");
  var client=_engine!.Client!;QueryPage page=null!;
  await Until(async()=>{page=await client.QueryAsync(new(new Dictionary<string,object?>{{"sort","name"}},null,120));return page.Total==1;});
  var item=GalleryApi.ParseItem(page.Items[0]);LiveGallery.SetActive(false);Workspace.Visibility=Rail.Visibility=Visibility.Collapsed;
  foreach(bool diagnostic in new[]{false,true}){
   _viewer.Open(client,_engine.DataDirectory!,item,new(){{"sort","name"}},diagnostic);
   await _viewer.MeasureIdleTimer(diagnostic,check,sample);_viewer.CloseViewer();Workspace.Visibility=Rail.Visibility=Visibility.Collapsed;
  }
 }
}

public sealed partial class ViewerView
{
 internal async Task MeasureIdleTimer(bool diagnostic,Action<bool,string> check,Action<object> sample)
 {
  for(int i=0;i<200&&(_images?.Frame is null||_model?.Loading==true);i++)await Task.Delay(50);
  if(_images?.Frame is null)throw new TimeoutException("Own idle image missing");
  long before=_heartbeatCount;bool running=_heartbeat.IsRunning;await Task.Delay(1000);long ticks=_heartbeatCount-before;
  sample(new{scenario=diagnostic?"diagnostic_viewer":"normal_viewer",sampleMs=1000,heartbeatRunning=running,heartbeatTicks=ticks});
  check(diagnostic?running&&ticks>0:!running&&ticks==0,diagnostic?"explicit diagnostics retain UI heartbeat sampling":"normal viewer has no diagnostic heartbeat wakeups");
 }
}
