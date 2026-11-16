using System.Diagnostics;
using System.Text.Json;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview;

public sealed partial class MainWindow
{
 private async Task VerifyWindowPerformanceUi()
 {
  if(!_options.Diagnostics||Environment.GetEnvironmentVariable("LUMA_WINDOW_PERF_UI_TEST")!="1")return;
  var checks=new List<object>();var samples=new List<object>();
  void Check(bool passed,string name)=>checks.Add(new{passed,name});
  try{
   await Task.Delay(300);_chrome.Refresh();
   long start=_chrome.CaptionColorWrites;var clock=Stopwatch.StartNew();for(int i=0;i<100;i++)_chrome.Refresh();clock.Stop();long writes=_chrome.CaptionColorWrites-start;
   samples.Add(new{scenario="unchanged_caption",refreshes=100,colorPropertyWrites=writes,elapsedMs=clock.Elapsed.TotalMilliseconds});Check(writes==0,"unchanged refresh avoids native caption property writes");
   foreach(bool dark in new[]{true,false}){
    Shell.SetWindowPerformanceTheme(dark);await Task.Delay(100);_chrome.Refresh();
    Check(AppWindow.TitleBar.ButtonForegroundColor==((SolidColorBrush)Shell.ResolveThemeBrush("LumaText")).Color,dark?"dark caption colors update":"light caption colors update");
   }
   AppWindow.SetPresenter(AppWindowPresenterKind.FullScreen);_chrome.Refresh();
   Check(!JsonSerializer.SerializeToElement(_chrome.Snapshot).GetProperty("visible").GetBoolean(),"cached colors do not block fullscreen caption hiding");
   AppWindow.SetPresenter(AppWindowPresenterKind.Default);_chrome.Refresh();Check(JsonSerializer.SerializeToElement(_chrome.Snapshot).GetProperty("visible").GetBoolean(),"caption returns after fullscreen");
   Check(AppWindow.TitleBar.ButtonForegroundColor==((SolidColorBrush)Shell.ResolveThemeBrush("LumaText")).Color,"caption colors persist after fullscreen");
   await Shell.VerifyWindowIdlePerformance(_options.DataDirectory,Check,sample=>samples.Add(sample));
   await CaptureVerificationFrame();
  }catch(Exception error){checks.Add(new{passed=false,name="probe completed",error=error.ToString()});}
  bool passed=checks.Count>0&&checks.All(c=>JsonSerializer.SerializeToElement(c).GetProperty("passed").GetBoolean());
  await File.WriteAllTextAsync(Path.Combine(_options.DataDirectory,"window-perf-ui-report.json"),JsonSerializer.Serialize(new{result=passed?"passed_window_perf_ui":"failed_window_perf_ui",checks,samples,privateDataRead=false,networkUsed=false,scope="Own WinUI caption refreshes and one-second synthetic viewer sample; counter reductions are not a general CPU percentage benchmark."}));
 }
}
