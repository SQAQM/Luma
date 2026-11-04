using Luma.Core.Viewer;using Luma.Core.Gallery;
internal static class ViewerReviewTests
{
 public static async Task<int> Run(){
  var api=new Late();using var model=new ViewerModel(api);await model.OpenAsync(Late.Item(1),new());var moving=model.MoveAsync(1);api.PreviousDetail.SetResult(Late.Detail(1));await Task.Delay(20);
  if(model.Current?.Id!=2||model.Detail is not null)throw new Exception("VIEWER_REVIEW previous detail accepted after current changed");
  api.NextWindow.SetResult(new([Late.Item(1),Late.Item(2)],2,1,null,2));await moving;await Task.Delay(10);if(model.Detail?.Id!=2)throw new Exception("VIEWER_REVIEW current detail missing");
  using var retry=new ViewerModel(new Retry());await retry.OpenAsync(Late.Item(1),new());if(retry.DetailError!="busy")throw new Exception("VIEWER_REVIEW failure state missing");await retry.RetryDetailAsync();if(retry.Detail?.Id!=1||retry.DetailError!="")throw new Exception("VIEWER_REVIEW explicit metadata retry failed");return 4;
 }
 private sealed class Late:IViewerApi
 {
  public readonly TaskCompletionSource<GalleryDetail> PreviousDetail=new(TaskCreationOptions.RunContinuationsAsynchronously);
  public readonly TaskCompletionSource<ViewerWindow> NextWindow=new(TaskCreationOptions.RunContinuationsAsynchronously);
  public static GalleryItem Item(long id)=>new(id,new string((char)('a'+id),64),id.ToString(),"C:/synthetic/never-opened.png",10,10,100,"PNG","","",[]);
  public static GalleryDetail Detail(long id)=>new(id,Item(id).Identity,Item(id).Path,Item(id).Name,"synthetic "+id,"",[],"","",10,10);
  public Task<ViewerWindow> NeighborsAsync(GalleryItem i,Dictionary<string,object?> f,CancellationToken t)=>i.Id==1?Task.FromResult(new ViewerWindow([Item(1),Item(2)],1,null,2,2)):NextWindow.Task;
  public Task<GalleryDetail> DetailAsync(GalleryItem i,CancellationToken t)=>i.Id==1?PreviousDetail.Task:Task.FromResult(Detail(i.Id));
 }
 private sealed class Retry:IViewerApi
 {
  private int _attempts;
  public Task<ViewerWindow> NeighborsAsync(GalleryItem i,Dictionary<string,object?> f,CancellationToken t)=>Task.FromResult(new ViewerWindow([i],i.Id,null,null,1));
  public Task<GalleryDetail> DetailAsync(GalleryItem i,CancellationToken t)=>++_attempts==1?Task.FromException<GalleryDetail>(new Luma.Core.Engine.EngineRpcException("busy")):Task.FromResult(Late.Detail(i.Id));
 }
}
