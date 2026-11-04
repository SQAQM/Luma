using Luma.Core.Viewer;using Luma.Core.Gallery;using Luma.Core.Engine;
internal static class ViewerNavigationTests
{
 public static async Task<int> Run(){int n=0;void Check(bool v,string s){if(!v)throw new Exception("VIEWER_NAV "+s);n++;}
  var api=new Fake();using var model=new ViewerModel(api);
  await model.OpenAsync(Fake.Item(120),new(){["sort"]="name"});Check(model.Current?.Id==120&&model.Window.Count==9,"open bounded neighbors");
  var same=model.JumpAsync(model.Current!);Check(model.Window.Count==9,"programmatic current film selection does not reset window");await same;
  await model.MoveAsync(1);Check(model.Current?.Id==121,"cross gallery page boundary");
  await Task.WhenAll(Enumerable.Range(0,8).Select(_=>model.MoveAsync(1)));Check(model.Current?.Id==129,"rapid repeated input keeps requested steps");
  await model.OpenAsync(Fake.Item(1),new(){["sort"]="name"});await model.MoveAsync(-1);Check(model.Current?.Id==1,"scope boundary never wraps");
  var old=model.OpenAsync(Fake.Item(50),new(){["sort"]="slow"});await Task.Delay(5);await model.OpenAsync(Fake.Item(9),new(){["sort"]="name"});await old;Check(model.Current?.Id==9,"late neighbor scope discarded");
  await model.SetSortAsync("name_desc");Check(model.Filters["sort"]?.ToString()=="name_desc"&&model.Current?.Id==9,"sort retains scope and current");
  await model.OpenAsync(Fake.Item(50),new(){["sort"]="slow"});var previousMove=model.MoveAsync(1);await Task.Delay(5);await model.OpenAsync(Fake.Item(9),new(){["sort"]="name"});await model.MoveAsync(1);await previousMove;Check(model.Current?.Id==10,"new scope can navigate while old scope is still settling");
  var closing=model.OpenAsync(Fake.Item(50),new(){["sort"]="slow"});model.Close();await closing;Check(!model.Active&&model.Current is null&&model.Detail is null&&model.Window.Count==0,"close rejects delayed result");return n;
 }
 private sealed class Fake:IViewerApi
 {
  internal static GalleryItem Item(long id)=>new(id,new string('a',64),id.ToString(),"C:\\synthetic\\"+id+".png",100,50,10,"PNG","","",[]);
  public async Task<ViewerWindow> NeighborsAsync(GalleryItem item,Dictionary<string,object?> filters,CancellationToken token){await Task.Delay(filters["sort"]?.ToString()=="slow"?70:1);long id=item.Id;return new(Enumerable.Range((int)Math.Max(1,id-4),(int)(Math.Min(140,id+4)-Math.Max(1,id-4)+1)).Select(x=>Item(x)).ToArray(),id,id>1?id-1:null,id<140?id+1:null,140);}
  public async Task<GalleryDetail> DetailAsync(GalleryItem item,CancellationToken token){await Task.Delay(1);return new(item.Id,item.Identity,item.Path,item.Name,"synthetic","",[],"","",100,50);}
 }
}
