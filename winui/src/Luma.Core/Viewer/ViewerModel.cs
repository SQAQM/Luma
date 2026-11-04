using Luma.Core.Gallery;using Luma.Core.Engine;
namespace Luma.Core.Viewer;
public sealed record ViewerWindow(GalleryItem[] Items,long? CurrentId,long? PreviousId,long? NextId,long Total);
public interface IViewerApi
{
 Task<ViewerWindow> NeighborsAsync(GalleryItem item,Dictionary<string,object?> filters,CancellationToken token);
 Task<GalleryDetail> DetailAsync(GalleryItem item,CancellationToken token);
}
public sealed class ViewerApi(EngineClient client):IViewerApi
{
 private readonly GalleryApi _gallery=new(client);
 public async Task<ViewerWindow> NeighborsAsync(GalleryItem item,Dictionary<string,object?> filters,CancellationToken token){
  var e=await client.NeighborsAsync(new(item.Id,filters),null,token);var items=e.GetProperty("items").EnumerateArray().Select(GalleryApi.ParseItem).ToArray();
  long? Id(string key)=>e.GetProperty(key).ValueKind==System.Text.Json.JsonValueKind.Null?null:e.GetProperty(key).GetInt64();
  var value=new ViewerWindow(items,Id("current_id"),Id("previous_id"),Id("next_id"),e.GetProperty("total").GetInt64());
  if(items.Length>9||items.Select(x=>x.Id).Distinct().Count()!=items.Length||value.CurrentId is long id&&!items.Any(i=>i.Id==id))throw new EngineProtocolException();return value;
 }
 public Task<GalleryDetail> DetailAsync(GalleryItem item,CancellationToken token)=>_gallery.DetailAsync(item,new EngineContext(),token);
}
// UI-dispatcher owned. Repeated arrows queue bounded steps, decode never blocks navigation.
public sealed class ViewerModel(IViewerApi api):IDisposable
{
 private CancellationTokenSource _scope=new(),_detail=new();private int _generation,_detailGeneration,_movement;private Task? _moveTask;private bool _disposed;
 public bool Active{get;private set;}public bool Loading{get;private set;}public GalleryItem? Current{get;private set;}
 public GalleryDetail? Detail{get;private set;}public IReadOnlyList<GalleryItem> Window{get;private set;}=[];
 public Dictionary<string,object?> Filters{get;private set;}=[];public long? PreviousId{get;private set;}public long? NextId{get;private set;}public long Total{get;private set;}
 public string Error{get;private set;}="";public string DetailError{get;private set;}="";public event Action? Changed;
 private void SetCurrent(GalleryItem? item){_detailGeneration++;_detail.Cancel();Current=item;Detail=null;DetailError="";}
 public async Task OpenAsync(GalleryItem item,Dictionary<string,object?> filters){
  if(_disposed)return;_scope.Cancel();_scope.Dispose();_scope=new();_detail.Cancel();_movement=0;int generation=++_generation;
  _moveTask=null;Active=true;SetCurrent(item);Window=[];PreviousId=NextId=null;Filters=new(filters);Error="";Changed?.Invoke();await LoadAsync(item,generation);
 }
 private async Task LoadAsync(GalleryItem item,int generation){
  var token=_scope.Token;Loading=true;Error="";Changed?.Invoke();
  try{var window=await api.NeighborsAsync(item,new(Filters),token);if(!Active||_disposed||generation!=_generation)return;
   Window=window.Items;Total=window.Total;SetCurrent(Window.FirstOrDefault(i=>i.Id==window.CurrentId));PreviousId=window.PreviousId;NextId=window.NextId;if(Current is not null)_=LoadDetailAsync(Current,generation);
  }catch(Exception e){if(Active&&generation==_generation){Error=GalleryModel.Code(e);_movement=0;}}
  finally{if(Active&&generation==_generation){Loading=false;Changed?.Invoke();}}
 }
 private async Task LoadDetailAsync(GalleryItem item,int generation){
  int request=++_detailGeneration;_detail.Cancel();_detail.Dispose();_detail=new();var token=_detail.Token;DetailError="";
  bool Matches()=>Active&&generation==_generation&&request==_detailGeneration&&Current?.Id==item.Id&&Current.Identity==item.Identity;
  try{var detail=await api.DetailAsync(item,token);if(Matches()){Detail=detail;Changed?.Invoke();}}
  catch(Exception e){if(Matches()){DetailError=GalleryModel.Code(e);Changed?.Invoke();}}
 }
 public Task RetryDetailAsync(){if(!Active||Current is null||_disposed)return Task.CompletedTask;Detail=null;DetailError="";Changed?.Invoke();return LoadDetailAsync(Current,_generation);}
 public Task MoveAsync(int direction){if(!Active||_disposed||direction==0)return Task.CompletedTask;_movement=Math.Clamp(_movement+Math.Sign(direction),-256,256);if(_moveTask is null||_moveTask.IsCompleted)_moveTask=PumpAsync(_generation);return _moveTask;}
 private async Task PumpAsync(int generation){
  while(Active&&generation==_generation&&_movement!=0){int step=Math.Sign(_movement);_movement-=step;long? id=step>0?NextId:PreviousId;if(id is null){_movement=0;break;}var target=Window.FirstOrDefault(i=>i.Id==id);if(target is null){_movement=0;break;}SetCurrent(target);Changed?.Invoke();await LoadAsync(target,generation);}
 }
 public Task JumpAsync(GalleryItem item)=>!Active||(Current?.Id==item.Id&&Current.Identity==item.Identity)||!Window.Any(i=>i.Id==item.Id&&i.Identity==item.Identity)?Task.CompletedTask:OpenAsync(item,Filters);
 public Task SetSortAsync(string sort){if(Current is null)return Task.CompletedTask;var filters=new Dictionary<string,object?>(Filters){["sort"]=sort};return OpenAsync(Current,filters);}
 public void Close(){Active=false;_generation++;_detailGeneration++;_scope.Cancel();_detail.Cancel();_movement=0;Current=null;Detail=null;Window=[];PreviousId=NextId=null;Loading=false;Changed?.Invoke();}
 public void Dispose(){if(_disposed)return;Close();_disposed=true;_scope.Dispose();_detail.Dispose();}
}
