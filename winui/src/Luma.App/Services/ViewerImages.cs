using System.Diagnostics;using System.Runtime.InteropServices.WindowsRuntime;
using Luma.Core.Viewer;using Luma.Core.Gallery;using Luma.Core.Engine;using Luma.Core.Contracts;
using Microsoft.UI.Xaml.Media.Imaging;using Windows.Graphics.Imaging;
namespace Luma.Preview.Services;
internal sealed record ViewerFrame(GalleryItem Item,PreviewAsset Asset,BitmapImage Image);
internal sealed class ViewerImages(EngineClient client,string directory):IDisposable
{
 private readonly ByteLru<string,ViewerFrame> _cache=new(4,128L*1024*1024);
 private CancellationTokenSource _cancel=new();private (GalleryItem Item,string Mode,int Generation)? _wanted;
 private IReadOnlyList<GalleryItem> _neighbors=[];private bool _busy,_disposed;private int _generation;
 public ViewerFrame? Frame{get;private set;}public string Error{get;private set;}="";public bool Loading=>_wanted is not null&&Frame is null&&Error=="";
 public long Bytes=>_cache.Bytes;public int CacheCount=>_cache.Count;public int Completed{get;private set;}public int CacheHits{get;private set;}public int Discarded{get;private set;}
 public List<double> Latencies{get;}=[];public event Action? Changed;
 public void Show(GalleryItem item,string mode,IReadOnlyList<GalleryItem> neighbors){
  _neighbors=neighbors;if(_disposed)return;
  if(_wanted is { } old&&old.Item.Id==item.Id&&old.Item.Identity==item.Identity&&old.Mode==mode)return;
  _cancel.Cancel();_cancel.Dispose();_cancel=new();_wanted=(item,mode,++_generation);Frame=null;Error="";Changed?.Invoke();Pump();
 }
 public void Retry(){if(_wanted is not{} desired)return;_wanted=null;Show(desired.Item,desired.Mode,_neighbors);}
 private async Task<PreviewAsset> Asset(GalleryItem item,string mode,CancellationToken token){
  for(int attempt=0;;attempt++)try{return PreviewAsset.Parse(await client.CallAsync("catalog.preview.v2",new{imageId=item.Id,identity=item.Identity,mode},null,token),item.Identity);}
  catch(EngineRpcException e)when(e.Code=="busy"&&attempt<20){await Task.Delay(40,token);}
 }
 private async void Pump(){
  if(_disposed||_busy||_wanted is not{} wanted)return;_busy=true;var token=_cancel.Token;var clock=Stopwatch.StartNew();
  try{
   var asset=await Asset(wanted.Item,wanted.Mode,token);
   if(_disposed||wanted.Generation!=_generation){Discarded++;return;}
   if(_cache.TryGet(asset.ResourceId,out var cached)){Frame=cached with{Item=wanted.Item};CacheHits++;}
   else{
    byte[] bytes=await Task.Run(async()=>{
     string path=EngineAssets.Resolve(directory,new CachedAsset(asset.ResourceId,asset.RelativePath));using var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read,65536,FileOptions.Asynchronous|FileOptions.SequentialScan);EngineAssets.VerifyOpenedFile(file,directory);
     if(file.Length!=asset.Bytes||file.Length is <1 or >32*1024*1024)throw new EngineRpcException("asset_changed");byte[] data=new byte[(int)file.Length];await file.ReadExactlyAsync(data,token);asset.Verify(data);return data;
    },token);
    using var memory=new MemoryStream(bytes);using var random=memory.AsRandomAccessStream();var decoder=await BitmapDecoder.CreateAsync(random);
    if(decoder.PixelWidth!=asset.Width||decoder.PixelHeight!=asset.Height)throw new EngineRpcException("decode_budget");
    if(_disposed||wanted.Generation!=_generation){Discarded++;return;}
    random.Seek(0);var bitmap=new BitmapImage();await bitmap.SetSourceAsync(random);
    if(_disposed||wanted.Generation!=_generation){Discarded++;return;}
    if(bitmap.PixelWidth!=asset.Width||bitmap.PixelHeight!=asset.Height)throw new EngineRpcException("decode_budget");
    var frame=new ViewerFrame(wanted.Item,asset,bitmap);if(!_cache.Put(asset.ResourceId,frame,(long)asset.Width*asset.Height*4))throw new EngineRpcException("decode_budget");Frame=frame;
   }
   Completed++;if(Latencies.Count>=512)Latencies.RemoveAt(0);Latencies.Add(clock.Elapsed.TotalMilliseconds);Changed?.Invoke();
   // Warm at most two adjacent PNG resources after current pixels; no neighbor pixel allocation.
   var around=_neighbors.ToList();int index=around.FindIndex(x=>x.Id==wanted.Item.Id);
   foreach(int offset in new[]{1,-1}){int position=index+offset;if(index<0||position<0||position>=around.Count)continue;token.ThrowIfCancellationRequested();await Asset(around[position],wanted.Mode,token);}
  }
  catch(Exception e){if(!_disposed&&wanted.Generation==_generation&&e is not OperationCanceledException){if(Frame is null){Error=GalleryModel.Code(e);Changed?.Invoke();}}else Discarded++;}
  finally{_busy=false;if(!_disposed&&wanted.Generation!=_generation)Pump();}
 }
 public void Clear(){_generation++;_cancel.Cancel();_wanted=null;_neighbors=[];Frame=null;Error="";_cache.Clear();Changed?.Invoke();}
 public void Dispose(){if(_disposed)return;_disposed=true;Clear();_cancel.Dispose();}
}
