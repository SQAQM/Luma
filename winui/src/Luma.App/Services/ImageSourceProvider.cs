using System.ComponentModel;
using System.Runtime.InteropServices.WindowsRuntime;
using Luma.Core.Contracts;
using Luma.Core.Engine;
using Luma.Core.Gallery;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
namespace Luma.Preview.Services;

[Microsoft.UI.Xaml.Data.Bindable]
public sealed class GalleryTile(GalleryItem item):INotifyPropertyChanged
{
    public GalleryItem Item{get;}=item;
    public bool Favorite=>Item.Favorite;
    public int Rating=>Item.Rating;
    public string ReactionId=>"Gallery"+Item.Id;
    private bool _canEdit;
    public bool CanEdit{get=>_canEdit;set{if(value==_canEdit)return;_canEdit=value;PropertyChanged?.Invoke(this,new(nameof(CanEdit)));}}
    public string AutomationId=>"GalleryImage"+Item.Id;
    public string Name=>Item.Name;
    public double LabelSize{get;set;}=12;
    public string Caption=>$"{Item.Width} × {Item.Height} · {Item.Format}";
    public string Metadata=>Caption+" · "+Item.Family+" · "+Item.Model;
    private double _height=110;
    private double _rowHeight=92;
    public double RowHeight{get=>_rowHeight;set{if(_rowHeight==value)return;_rowHeight=value;PropertyChanged?.Invoke(this,new(nameof(RowHeight)));}}
    public double ImageHeight{get=>_height;set{if(_height==value)return;_height=value;PropertyChanged?.Invoke(this,new(nameof(ImageHeight)));}}
    private BitmapImage? _image;
    public BitmapImage? Image{get=>_image;set{_image=value;PropertyChanged?.Invoke(this,new(nameof(Image)));}}
    private string _status="等待预览";
    public string Status{get=>_status;set{_status=value;PropertyChanged?.Invoke(this,new(nameof(Status)));}}
    public event PropertyChangedEventHandler? PropertyChanged;
}

// Only realized tiles request work. One thumbnail RPC reserves the other engine lane for input.
internal sealed class ImageSourceProvider:IDisposable
{
    private readonly EngineClient _client;private readonly string _data;
    private readonly DispatcherQueue _dispatcher=DispatcherQueue.GetForCurrentThread();
    private readonly VisibleByteCache<GalleryTile,BitmapImage> _cache=new(160,64L*1024*1024);
    private int _size=320;
    public int Evicted{get;private set;}
    public void SetSize(int size){size=Math.Clamp(size,64,1200);if(size==_size)return;_size=size;Reset();}
    private readonly HashSet<GalleryTile> _visible=[],_spaceWaiters=[];
    private readonly Queue<GalleryTile> _queue=[];private readonly HashSet<GalleryTile> _queued=[];
    private readonly DispatcherQueueTimer _retry;
    private CancellationTokenSource _cancel=new();private bool _active=true,_busy,_disposed;private int _generation;
    public long DecodedBytes{get;private set;}
    public int Completed{get;private set;}
    public int Failed{get;private set;}
    public int BusyRetries{get;private set;}
    public int CacheCount=>_cache.Count;
    public int Queued=>_queued.Count;
    public int Active=>_busy?1:0;
    public event Action? Changed;
    public ImageSourceProvider(EngineClient client,string data)
    {
        _client=client;_data=data;_client.CapacityAvailable+=Capacity;
        _retry=_dispatcher.CreateTimer();_retry.Interval=TimeSpan.FromMilliseconds(100);_retry.IsRepeating=false;
        _retry.Tick+=(_,_)=>Pump();
    }
    public void Visible(GalleryTile tile,bool visible)
    {
        if(_disposed)return;
        if(!visible){_visible.Remove(tile);_spaceWaiters.Remove(tile);foreach(var waiting in _spaceWaiters.ToArray())if(_visible.Contains(waiting)&&_queued.Add(waiting)){_queue.Enqueue(waiting);_spaceWaiters.Remove(waiting);}Pump();return;}
        _visible.Add(tile);
        if(_cache.TryGetValue(tile,out var image)){tile.Image=image;return;}
        if(_queued.Add(tile)){_queue.Enqueue(tile);tile.Status="正在载入预览";}
        Pump();
    }
    private void Capacity()=>_dispatcher.TryEnqueue(()=>{if(!_disposed)Pump();});
    private async void Pump()
    {
        if(_disposed||!_active||_busy||!_client.Connected)return;
        GalleryTile? tile=null;
        while(_queue.TryDequeue(out var candidate)){_queued.Remove(candidate);if(_visible.Contains(candidate)&&!_cache.ContainsKey(candidate)){tile=candidate;break;}}
        if(tile is null)return;
        _busy=true;int generation=_generation,requested=_size;var token=_cancel.Token;bool retry=false;
        try
        {
            var element=await _client.CallAsync("catalog.thumbnail",new{imageId=tile.Item.Id,identity=tile.Item.Identity,size=requested},null,token);
            if(element.GetProperty("identity").GetString()!=tile.Item.Identity)throw new EngineRpcException("stale_identity");
            var asset=new CachedAsset(element.GetProperty("resourceId").GetString()!,element.GetProperty("relativePath").GetString()!);
            byte[] bytes=await Task.Run(async()=>
            {
                string path=EngineAssets.Resolve(_data,asset);
                using var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read,65536,FileOptions.Asynchronous|FileOptions.SequentialScan);
                EngineAssets.VerifyOpenedFile(file,_data);
                if(file.Length is <1 or >4*1024*1024)throw new EngineRpcException("asset_boundary");
                byte[] data=new byte[(int)file.Length];await file.ReadExactlyAsync(data,token);
                CachePayload.Verify(data,element.GetProperty("bytes").GetInt64(),element.GetProperty("sha256").GetString()!);return data;
            },token);
            if(_disposed||generation!=_generation||!_active)return;
            using var memory=new MemoryStream(bytes);using var random=memory.AsRandomAccessStream();
            var decoder=await BitmapDecoder.CreateAsync(random);
            if(!ThumbnailBudget.Accepts(decoder.PixelWidth,decoder.PixelHeight,requested))throw new EngineRpcException("decode_budget");
            if(_disposed||generation!=_generation||!_active)return;
            random.Seek(0);var bitmap=new BitmapImage();await bitmap.SetSourceAsync(random);
            if(_disposed||generation!=_generation||!_active)return;
            long weight=(long)bitmap.PixelWidth*bitmap.PixelHeight*4;
            if(weight<=0||bitmap.PixelWidth!=decoder.PixelWidth||bitmap.PixelHeight!=decoder.PixelHeight)throw new EngineRpcException("decode_budget");
            if(!_cache.Put(tile,bitmap,weight,_visible.Contains,out var evicted)){tile.Status="等待缓存空间";_spaceWaiters.Add(tile);return;}
            foreach(var previous in evicted){previous.Key.Image=null;previous.Key.Status="等待预览";Evicted++;}
            DecodedBytes=_cache.Bytes;tile.Image=bitmap;tile.Status="";Completed++;
        }
        catch(Exception e)
        {
            if(!_disposed&&generation==_generation)
            {
                retry=e is EngineRpcException r&&r.Code=="busy";
                if(retry)BusyRetries++;
                if(!retry&&e is not OperationCanceledException){tile.Status=e is EngineRpcException error&&error.Code=="not_found"?"文件离线或已移走":"预览不可用";Failed++;}
            }
        }
        finally
        {
            _busy=false;
            if(!_disposed&&generation==_generation&&_active)
            {
                if(retry&&_visible.Contains(tile)&&_queued.Add(tile)){_queue.Enqueue(tile);_retry.Start();}
                else Pump();
                Changed?.Invoke();
            }
            else if(!_disposed&&_active)Pump();
        }
    }
    public void Pause(){_active=false;_cancel.Cancel();_retry.Stop();_visible.Clear();_spaceWaiters.Clear();_queue.Clear();_queued.Clear();}
    public void Resume(){if(!_disposed){_active=true;if(_cancel.IsCancellationRequested){_cancel.Dispose();_cancel=new();}Pump();}}
    public void Reset(){Pause();_generation++;foreach(var tile in _cache.Keys)tile.Image=null;_cache.Clear();DecodedBytes=0;Completed=Failed=0;Resume();}
    public void Dispose(){if(_disposed)return;_disposed=true;Pause();_client.CapacityAvailable-=Capacity;_cancel.Dispose();_cache.Clear();}
}
