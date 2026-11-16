using System.Diagnostics;
using System.Text.Json;
using Luma.Core.Contracts;
using Luma.Core.Gallery;
using Luma.Preview.Services;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;

namespace Luma.Preview.Views;

// Opt-in native regression: only the probe's own generated gradients and profile.
public sealed partial class ShellView
{
 internal async Task VerifyFilmstripPerformanceUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_FILMSTRIP_PERF_UI_TEST")!="1")return;
  var checks=new List<object>();var samples=new List<object>();var captureErrors=new List<string>();
  void Check(bool passed,string name)=>checks.Add(new{passed,name});
  async Task Until(Func<Task<bool>> condition){for(int i=0;i<300;i++){if(await condition())return;await Task.Delay(50);}throw new TimeoutException("Own filmstrip fixture timed out");}
  try
  {
   Navigate(WorkspacePage.Models);
   await Until(()=>Task.FromResult(_engine?.Connected==true&&_managementState is not null));
   string images=Path.Combine(directory+"-fixtures","filmstrip");Directory.CreateDirectory(images);
   for(int i=0;i<12;i++)await SyntheticPng(Path.Combine(images,$"filmstrip-{i:00}.png"),i);
   Check(await _managementState!.WriteAsync("catalog.import",new{path=images,recursive=true}),"own twelve gradients imported");
   var client=_engine!.Client!;QueryPage page=null!;
   await Until(async()=>{page=await client.QueryAsync(new QueryRequest(new Dictionary<string,object?>{{"sort","name"}},null,120));return page.Total==12&&page.Items.Length==12;});
   Management.SuspendForViewer();LiveGallery.SetActive(false);Workspace.Visibility=Rail.Visibility=Visibility.Collapsed;
   _viewer.Open(client,_engine.DataDirectory!,GalleryApi.ParseItem(page.Items[5]),new(){{"sort","name"}},true);
   await _viewer.VerifyFilmstripPerformanceUi(Check,samples);
   try{await capture();}catch(Exception error){captureErrors.Add(error.Message);}
  }
  catch(Exception error){checks.Add(new{passed=false,name="probe completed",error=error.ToString()});}
  bool passed=checks.Count>0&&checks.All(c=>JsonSerializer.SerializeToElement(c).GetProperty("passed").GetBoolean());
  await File.WriteAllTextAsync(Path.Combine(directory,"filmstrip-perf-ui-report.json"),JsonSerializer.Serialize(new{
   result=passed?"passed_filmstrip_perf_ui":"failed_filmstrip_perf_ui",checks,samples,captureErrors,privateDataRead=false,
   scope="native WinUI visible nine-item filmstrip, twelve unique inert PNG gradients; next/back after settled thumbnails; successful thumbnail RPC/decode completions, not all RPC attempts; warmed OS/engine caches, diagnostic capture enabled; no million-image throughput claim"}));
 }
}

public sealed partial class ViewerView
{
 internal async Task VerifyFilmstripPerformanceUi(Action<bool,string> check,List<object> samples)
 {
  async Task Until(Func<bool> condition){for(int i=0;i<300;i++){if(condition())return;await Task.Delay(50);}throw new TimeoutException("Own filmstrip image/thumbnail not ready");}
  async Task Ready(){await Until(()=>IsOpen&&_model is{Loading:false,Current:not null}&&_images?.Frame?.Item.Id==_model.Current.Id&&_images.Frame.Item.Identity==_model.Current.Identity&&_thumbs is{Active:0,Queued:0}&&_tiles.Count==9&&_tiles.All(t=>t.Image is not null));}
  await Until(()=>IsOpen&&_model is{Loading:false,Current:not null}&&_tiles.Count==9);
  FilmToggleClicked(FilmToggle,new RoutedEventArgs());UpdateLayout();await Ready();
  check(Filmstrip.Visibility==Visibility.Visible&&Filmstrip.ActualWidth>=9*88,"all nine filmstrip tiles fit the native viewport");
  check(_thumbs!.Failed==0,"initial nine thumbnails decode without failure");
  samples.Add(new{operation="initial",tileCount=_tiles.Count,completedThumbnailRpcDecodeCycles=_thumbs.Completed,cacheCount=_thumbs.CacheCount,decodedBytes=_thumbs.DecodedBytes});
  var provider=_thumbs;int observed=provider.Completed,total=0;
  // Reset clears Completed. Account for that in the old behavior as well as the fixed path.
  void Observe(){int value=provider.Completed;total+=value<observed?value:value-observed;observed=value;}
  provider.Changed+=Observe;
  try
  {
   long start=_model!.Current!.Id;
   foreach(int direction in new[]{1,-1})
   {
    var before=_tiles.ToArray();var decoded=before.ToDictionary(t=>t.Item.Id,t=>t.Image);int previousTotal=total;
    var elapsed=Stopwatch.StartNew();await _model.MoveAsync(direction);await Ready();elapsed.Stop();
    int retained=_tiles.Count(tile=>before.Any(old=>ReferenceEquals(old,tile)));
    int retainedDecoded=_tiles.Count(tile=>before.Any(old=>ReferenceEquals(old,tile))&&ReferenceEquals(decoded[tile.Item.Id],tile.Image));
    int completed=total-previousTotal;
    string operation=direction>0?"next":"back";
    samples.Add(new{operation,elapsedMs=elapsed.Elapsed.TotalMilliseconds,retainedTiles=retained,retainedDecodedImages=retainedDecoded,completedThumbnailRpcDecodeCycles=completed,tileCount=_tiles.Count,cacheCount=provider.CacheCount,decodedBytes=provider.DecodedBytes,failed=provider.Failed,busyRetries=provider.BusyRetries});
    check(retained==8,operation+" retains the eight overlapping native tile instances");
    check(retainedDecoded==8,operation+" retains eight decoded image instances");
    check(completed<=1,operation+" completes at most one new thumbnail RPC/decode cycle");
    check(ReferenceEquals(Filmstrip.SelectedItem,_tiles.FirstOrDefault(t=>t.Item.Id==_model.Current?.Id)),operation+" selects the current native filmstrip item");
    check(_tiles.Count<=9&&provider.CacheCount<=160&&provider.DecodedBytes<=64L*1024*1024,operation+" keeps bounded neighbors and existing decoded cache limits");
    check(provider.Failed==0,operation+" thumbnail pipeline has no failure");
   }
   check(_model.Current?.Id==start,"next/back returns to original image");
   await _model.SetSortAsync("name_desc");await Ready();
   check(_tiles.Select(t=>t.Item.Id).SequenceEqual(_model.Window.Select(i=>i.Id)),"sort updates filmstrip order");
   check(_tiles.Select(t=>t.Item.Id).Distinct().Count()==9,"sort keeps unique bounded neighbors");
   var move1=_model.MoveAsync(1);var move2=_model.MoveAsync(1);var move3=_model.MoveAsync(-1);
   await Task.WhenAll(move1,move2,move3);await Ready();
   check(_tiles.Select(t=>t.Item.Id).SequenceEqual(_model.Window.Select(i=>i.Id)),"rapid navigation settles on the latest model window");
   check(ReferenceEquals(Filmstrip.SelectedItem,_tiles.FirstOrDefault(t=>t.Item.Id==_model.Current?.Id)),"rapid navigation preserves current selection");
   provider.Pause();
   for(int i=0;i<64;i++){var departed=new GalleryTile(_model.Current!);provider.Visible(departed,true);provider.Visible(departed,false);}
   samples.Add(new{operation="departed_while_lane_unavailable",departedTiles=64,pendingRequests=provider.Queued});
   check(provider.Queued==0,"departed tiles do not accumulate pending requests while decoding is unavailable");
   provider.Resume();
  }
  finally{provider.Changed-=Observe;}
 }
}
