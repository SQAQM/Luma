using System.Text.Json;
using Luma.Core.Contracts;
using Luma.Core.Gallery;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace Luma.Preview.Views;

// Native focus/layout regression, restricted to an isolated diagnostic profile.
public sealed partial class ShellView
{
 internal async Task VerifyViewerUsabilityUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_VIEWER_USABILITY_UI_TEST")!="1")return;
  var checks=new List<object>();var captureErrors=new List<string>();
  void Check(bool passed,string name)=>checks.Add(new{passed,name});
  async Task Capture(){try{await capture();}catch(Exception error){captureErrors.Add(error.Message);}}
  async Task Until(Func<Task<bool>> condition){for(int i=0;i<200;i++){if(await condition())return;await Task.Delay(50);}throw new TimeoutException("Own viewer usability fixture timed out");}
  try{
   Navigate(WorkspacePage.Models);
   await Until(()=>Task.FromResult(_engine?.Connected==true&&_managementState is not null));
   string images=Path.Combine(directory+"-fixtures","images");Directory.CreateDirectory(images);
   string seed=Path.Combine(images,"fixture-000.png");await SyntheticPng(seed,0);
   for(int i=1;i<123;i++)File.Copy(seed,Path.Combine(images,$"fixture-{i:000}.png"));
   if(!await _managementState!.WriteAsync("catalog.import",new{path=images,recursive=true}))throw new InvalidOperationException("Own image import failed");
   var client=_engine!.Client!;
   await Until(async()=>{var page=await client.QueryAsync(new QueryRequest(new Dictionary<string,object?>{{"sort","name"}},null,120));return page.Total==123;});
   Navigate(WorkspacePage.Gallery);
   await LiveGallery.VerifyViewerReturnUi(Check,async target=>{
    LiveGallery.OpenForViewerUsability();
    await _viewer.VerifyViewerReturnUi(target);
   },Capture);
   await Capture();
   // Leave a real viewer with its sort field focused for optional native key/pointer checks.
   LiveGallery.OpenForViewerUsability();await _viewer.PrepareViewerKeyboardUi();
   await Capture();
  }catch(Exception error){checks.Add(new{passed=false,name="probe completed",error=error.ToString()});}
  bool passed=checks.Count>0&&checks.All(c=>JsonSerializer.SerializeToElement(c).GetProperty("passed").GetBoolean());
  await File.WriteAllTextAsync(Path.Combine(directory,"viewer-usability-ui-report.json"),JsonSerializer.Serialize(new{result=passed?"passed_viewer_usability_ui":"failed_viewer_usability_ui",checks,captureErrors,privateDataRead=false,scope="synthetic PNGs, native WinUI focus, virtualized containers and actual viewer close events; keyboard/pointer routing requires separate native input check"}));
 }
}

public sealed partial class GalleryView
{
 internal void OpenForViewerUsability()=>OpenViewer();
 internal async Task VerifyViewerReturnUi(Action<bool,string> check,Func<GalleryItem,Task> openAndClose,Func<Task> capture)
 {
  async Task Until(Func<bool> condition){for(int i=0;i<200;i++){if(condition())return;await Task.Delay(50);}throw new TimeoutException("Own gallery focus fixture timed out");}
  await Until(()=>_active&&_model is{Loading:false,Total:123}&&_tiles.Count==120);
  foreach(bool rows in new[]{false,true}){
   if(rows!=_listMode)ViewClicked(ViewButton,new RoutedEventArgs());
   await Task.Delay(100);var first=_tiles[0];ActiveImages.SelectedItem=first;await _model!.SelectAsync(first.Item);
   ActiveImages.ScrollIntoView(first,ScrollIntoViewAlignment.Leading);UpdateLayout();await Task.Delay(100);
   SortBox.Focus(FocusState.Keyboard);
   await openAndClose(first.Item);await Task.Delay(150);
   check(_model.Current?.Id==first.Item.Id&&ReferenceEquals(ActiveImages.SelectedItem,first),(rows?"list":"grid")+" return keeps selected image");
   check(ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot),ActiveImages.ContainerFromItem(first)),(rows?"list":"grid")+" close restores native focus to current gallery item");
   var last=_tiles[^1];await openAndClose(last.Item);await Task.Delay(200);UpdateLayout();
   var container=ActiveImages.ContainerFromItem(last) as FrameworkElement;
   check(_model.Current?.Id==last.Item.Id&&ReferenceEquals(ActiveImages.SelectedItem,last),(rows?"list":"grid")+" current viewer image selects within same page");
   check(container is not null&&ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot),container),(rows?"list":"grid")+" virtualized return realizes and focuses current item");
   bool visible=false;
   if(container is not null){Rect item=container.TransformToVisual(ActiveImages).TransformBounds(new(0,0,container.ActualWidth,container.ActualHeight));visible=item.Top>=-1&&item.Bottom<=ActiveImages.ActualHeight+1;}
   check(visible,(rows?"list":"grid")+" returning current item is fully inside gallery viewport");
   await capture();
   var before=_model.Current;var selection=ActiveImages.SelectedItem;
   ReturnFromViewer(last.Item with{Identity=new string('f',64)});await Task.Delay(100);
   check(_model.Current==before&&ReferenceEquals(ActiveImages.SelectedItem,selection),(rows?"list":"grid")+" stale identity cannot select replacement image");
   ReturnFromViewer(last.Item with{Id=long.MaxValue});await Task.Delay(100);
   check(_model.Current==before&&ReferenceEquals(ActiveImages.SelectedItem,selection)&&_model.Items.Count==120,(rows?"list":"grid")+" offpage image does not change selection or fetch a new page");
   ReturnFromViewer(last.Item);SortBox.Focus(FocusState.Keyboard);await Task.Delay(150);
   check(ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot),SortBox),(rows?"list":"grid")+" deferred image focus respects a newer control focus");
  }
  if(_listMode)ViewClicked(ViewButton,new RoutedEventArgs());
  ActiveImages.SelectedItem=_tiles[0];await _model!.SelectAsync(_tiles[0].Item);ActiveImages.ScrollIntoView(_tiles[0],ScrollIntoViewAlignment.Leading);UpdateLayout();
 }
}

public sealed partial class ViewerView
{
 private async Task ViewerUsabilityReady()
 {
  for(int i=0;i<200;i++){if(IsOpen&&_model is{Loading:false,Current:not null}&&_images?.Frame?.Item.Id==_model.Current.Id)return;await Task.Delay(50);}
  throw new TimeoutException("Own viewer image not ready");
 }
 internal async Task VerifyViewerReturnUi(GalleryItem target)
 {
  await ViewerUsabilityReady();
  if(_model!.Current!.Id!=target.Id){await _model.OpenAsync(target,_model.Filters);await ViewerUsabilityReady();}
  Sort.Focus(FocusState.Keyboard);CloseViewer();
 }
 internal async Task PrepareViewerKeyboardUi(){await ViewerUsabilityReady();Sort.Focus(FocusState.Keyboard);}
}
