using System.Text.Json;
using System.Runtime.InteropServices.WindowsRuntime;
using Luma.Core.Engine;
using Luma.Core.Contracts;
using Luma.Core.Gallery;
using Luma.Core.Management;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;
using Windows.Graphics.Imaging;

namespace Luma.Preview.Views;

public sealed partial class ShellView
{
 internal async Task VerifyFeatureUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_FEATURE_UI_TEST")!="1")return;
  var checks=new List<object>();
  void Check(bool value,string name){checks.Add(new{passed=value,name});if(!value)throw new InvalidOperationException(name);}
  async Task Until(Func<Task<bool>> test){for(int i=0;i<150;i++){if(await test())return;await Task.Delay(100);}throw new TimeoutException("Own feature fixture timed out");}
  async Task Capture(string name){await capture();File.Copy(Path.Combine(directory,"screenshot.png"),Path.Combine(directory,name+".png"),true);}
  try
  {
   Navigate(WorkspacePage.Models);
   await Until(()=>Task.FromResult(_engine?.Connected==true&&_managementState is not null));
   var client=_engine!.Client!;var api=new ManagementApi(client);var state=_managementState!;
   string fixtures=directory+"-fixtures",images=Path.Combine(fixtures,"images"),models=Path.Combine(fixtures,"models");
   Directory.CreateDirectory(images);Directory.CreateDirectory(models);
   for(int i=0;i<3;i++)await SyntheticPng(Path.Combine(images,$"studio-{i}.png"),i);
   await File.WriteAllTextAsync(Path.Combine(models,"synthetic.safetensors"),"inert fixture");
   await File.WriteAllTextAsync(Path.Combine(models,"synthetic.cm-info.json"),JsonSerializer.Serialize(new{ModelName="本地模型资料",VersionName="Version 2",ModelType="LORA",BaseModel="Krea 2",ModelDescription="本地介绍",TrainedWords=new[]{"fixture"}}));
   File.Copy(Path.Combine(images,"studio-0.png"),Path.Combine(models,"synthetic.preview.png"));
   Check(await state.WriteAsync("models.add",new{path=models}),"model directory added");
   long root=state.Result.GetProperty("id").GetInt64();
   Check(await state.WriteAsync("models.scan",new{rootId=root}),"model indexing started");
   JsonElement local=default;
   await Until(async()=>{var page=await api.ReadAsync("models",new{},default);var items=page.GetProperty("items");if(items.GetArrayLength()!=1)return false;local=items[0].Clone();return true;});
   Check(local.GetProperty("metadata_source").GetString()=="local"&&local.GetProperty("type").GetString()=="LORA"&&local.GetProperty("family").GetString()=="Krea 2","local metadata reaches native model card");
   await Management.RefreshForReleaseVerification();await Task.Delay(400);await Capture("models-local-info");
   Check(await state.WriteAsync("catalog.import",new{path=images,recursive=true}),"own pictures imported");
   QueryPage gallery=null!;
   await Until(async()=>{gallery=await client.QueryAsync(new(new Dictionary<string,object?>{{"sort","name"}},null,120));return gallery.Items.Length==3;});
   var first=GalleryApi.ParseItem(gallery.Items[0]);
   LiveGallery.SetActive(false);Workspace.Visibility=Rail.Visibility=Visibility.Collapsed;
   _viewer.Open(client,_engine.DataDirectory!,first,new(){{"sort","name"}},true);
   await _viewer.VerifyPlayback(Check,()=>Capture("viewer-playback"));
   Check(!_viewer.IsOpen,"viewer closes after playback test");
   File.WriteAllText(Path.Combine(directory,"feature-ui-report.json"),JsonSerializer.Serialize(new{result="passed_feature_ui",checks,privateDataRead=false}));
  }
  catch(Exception error){File.WriteAllText(Path.Combine(directory,"feature-ui-report.json"),JsonSerializer.Serialize(new{result="failed_feature_ui",checks,error=error.ToString()}));}
 }
 private static async Task SyntheticPng(string path,int variant)
 {
  const int width=960,height=640;byte[] pixels=new byte[width*height*4];
  for(int y=0;y<height;y++)for(int x=0;x<width;x++){int p=(y*width+x)*4;pixels[p]=(byte)(90+x*100/width);pixels[p+1]=(byte)(65+y*90/height);pixels[p+2]=(byte)(45+variant*25);pixels[p+3]=255;}
  using var memory=new MemoryStream();using var stream=memory.AsRandomAccessStream();var encoder=await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId,stream);
  encoder.SetPixelData(BitmapPixelFormat.Bgra8,BitmapAlphaMode.Ignore,width,height,96,96,pixels);await encoder.FlushAsync();await File.WriteAllBytesAsync(path,memory.ToArray());
 }
}

public sealed partial class ViewerView
{
 internal async Task VerifyPlayback(Action<bool,string> check,Func<Task> capture)
 {
  async Task Ready(){for(int i=0;i<150;i++){if(_model?.Current is{} item&&_images?.Frame is{} frame&&item.Id==frame.Item.Id&&item.Identity==frame.Item.Identity&&!_model.Loading)return;await Task.Delay(100);}throw new TimeoutException("Own viewer image not ready");}
  await Ready();check(PlaybackButton.ActualWidth>=32&&PlaybackButton.ActualHeight>=32,"play icon has usable target");
  long first=_model!.Current!.Id;TogglePlayback();check(_playback.Playing,"play control starts playback");
  await capture();
  for(int i=0;i<120&&_model.Current?.Id==first;i++)await Task.Delay(100);
  check(_model.Current?.Id!=first,"playback advances in current sorted range");
  await Ready();PausePlayback();long paused=_model.Current!.Id;await Task.Delay(400);
  check(_model.Current?.Id==paused&&!_playback.Playing,"pause keeps current image");
  TogglePlayback();Fit();check(!_playback.Playing,"manual zoom mode pauses playback");
  var playBounds=PlaybackButton.TransformToVisual(Header).TransformBounds(new(0,0,PlaybackButton.ActualWidth,PlaybackButton.ActualHeight));
  check(playBounds.Left>=0&&playBounds.Right<=Header.ActualWidth,"play icon stays within header");
  TogglePlayback();CloseViewer();check(!_playback.Playing&&!_playbackTimer.IsRunning,"closing stops timer");
 }
}
