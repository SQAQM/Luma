using System.Text.Json;
using Luma.Core.Management;
using Luma.Core.Engine;
using Luma.Core.Gallery;

static class ManagementTests
{
 public static async Task<int> Run()
 {
  int n=0;void Check(bool v){if(!v)throw new Exception("management assertion");n++;}
  var loads=new ManagementReadScope();Check(loads.TryPoll());long old=loads.Generation;loads.Invalidate();Check(!loads.TryRefresh());loads.Complete();Check(loads.RefreshPending&&loads.Generation!=old);Check(loads.TryRefresh());Check(!loads.RefreshPending);loads.Invalidate();Check(!loads.TryRefresh());loads.Complete();Check(loads.TryRefresh());loads.Complete();Check(!loads.RefreshPending&&!loads.Busy);
  Check(ThumbnailBudget.Accepts(1200,800,1200));Check(!ThumbnailBudget.Accepts(1201,800,1200));Check(!ThumbnailBudget.Accepts(640,480,320));Check(!ThumbnailBudget.Accepts(0,480,1200));
  var jobs=new ManagementJobSelection();jobs.Select("synthetic-job");foreach(string status in new[]{"running","completed","failed","cancelled"}){jobs.Update(new[]{JsonSerializer.SerializeToElement(new{id="synthetic-job",status,processed=5,total=5})});Check(jobs.CanCancel==(status=="running"));Check(jobs.Value?.GetProperty("status").GetString()==status);}jobs.Update([]);Check(!jobs.CanCancel&&jobs.Value is null);
  var fake=new DelayedManagement();var state=new ManagementState(fake);
  var first=state.LoadAsync("tags");state.ChangeScope();var second=state.LoadAsync("models");
  fake.Reads[1].SetResult(JsonSerializer.SerializeToElement(new{marker=2}));await second;
  fake.Reads[0].SetResult(JsonSerializer.SerializeToElement(new{marker=1}));await first;
  Check(state.Data.GetProperty("marker").GetInt32()==2);
  var write=state.WriteAsync("settings.save",new{values=new{sort="name"}});
  Check(state.Writing);Check(!await state.WriteAsync("settings.save",new{}));
  fake.Write.SetException(new IOException());Check(!await write);Check(state.Uncertain);Check(fake.Writes==1);
  fake.StatusValue=JsonSerializer.SerializeToElement(new{state="pending"});await state.ResolveAsync();Check(state.Uncertain);
  fake.StatusValue=JsonSerializer.SerializeToElement(new{state="completed",result=new{saved=true}});await state.ResolveAsync();Check(!state.Uncertain);Check(fake.Writes==1);
  Check(state.Feedback.Contains("完成"));state.Dispose();
  var cache=new VisibleByteCache<string,string>(2,100);Check(cache.Put("A","a",40,_=>false,out _));Check(!cache.Put("B","b",80,k=>k=="A",out _));Check(cache.Bytes==40&&cache.TryGetValue("A",out _));Check(cache.Put("B","b",50,_=>false,out _));Check(cache.Put("C","c",50,k=>k=="A",out var evicted));Check(evicted.Single().Key=="B"&&cache.Bytes==90);
  var failedTransport=new DelayedManagement();var failed=new ManagementState(failedTransport);var failedWrite=failed.WriteAsync("catalog.import",new{});failedTransport.Write.SetException(new EngineRpcException("operation_failed"));await failedWrite;Check(failed.Uncertain);failed.Dispose();
  var image=Luma.Core.Gallery.GalleryApi.ParseItem(JsonSerializer.SerializeToElement(new{id=1,identity=new string('a',64),name="synthetic",path="C:/synthetic.png",width=1,height=1,size=16,format="PNG",model="",family="未知",content_tags=Array.Empty<object>(),favorite=true,rating=4}));Check(image.Favorite&&image.Rating==4);
  string dir=Path.Combine(Path.GetTempPath(),"luma-ui-settings-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(dir);
  try{var prefs=new UiPreferences(dir);prefs.Save(new(1100,720,false,"Dark",true));var saved=prefs.Load();Check(saved.Width==1100&&saved.Theme=="Dark");Check(!File.Exists(Path.Combine(dir,"settings.json")));File.WriteAllText(prefs.Path,"{invalid");Check(prefs.Load().Width==1000);
   var pendingStore=new PendingOperationStore(dir);pendingStore.Save(new string('b',32),"catalog.import");var recoveredApi=new DelayedManagement();var recovered=new ManagementState(recoveredApi,pendingStore);Check(recovered.Uncertain&&recovered.OperationId==new string('b',32));Check(!await recovered.WriteAsync("catalog.import",new{}));recoveredApi.StatusValue=JsonSerializer.SerializeToElement(new{state="failed"});await recovered.ResolveAsync();Check(recovered.Outcome=="failed"&&recovered.Result.ValueKind==JsonValueKind.Undefined);recovered.Dispose();pendingStore.Clear();Check(pendingStore.Load() is null);
   var library=PreviewLibrary.Select(dir,false);var reopened=PreviewLibrary.Select(dir,false);Check(library.Created&&!reopened.Created&&library.Directory==reopened.Directory);Check(PreviewLibrary.Select(dir,true).Directory!=library.Directory);
   File.WriteAllText(Path.Combine(dir,"winui-library-v1.json"),"{\"directory\":\"../outside\"}");try{PreviewLibrary.Select(dir,false);throw new Exception("unsafe pointer accepted");}catch(EngineRpcException){n++;}
  }
  finally{if(Path.GetDirectoryName(Path.GetFullPath(dir))!=Path.TrimEndingDirectorySeparator(Path.GetTempPath())||!Path.GetFileName(dir).StartsWith("luma-ui-settings-"))throw new IOException("unsafe cleanup");Directory.Delete(dir,true);}
  string profile=Path.Combine(Path.GetTempPath(),"luma-ui-profile-"+Guid.NewGuid().ToString("N"));
  try{PreviewProfile.Open(profile,false);PreviewProfile.Open(profile,false);Check(File.Exists(Path.Combine(profile,".luma-winui-preview.json")));try{PreviewProfile.Open(profile,true);throw new Exception("diagnostic profile reused");}catch(EngineRpcException){n++;}File.Delete(Path.Combine(profile,".luma-winui-preview.json"));File.WriteAllText(Path.Combine(profile,"foreign.txt"),"untouched");try{PreviewProfile.Open(profile,false);throw new Exception("foreign profile accepted");}catch(EngineRpcException){n++;}Check(File.ReadAllText(Path.Combine(profile,"foreign.txt"))=="untouched");}
  finally{if(Path.GetDirectoryName(Path.GetFullPath(profile))!=Path.TrimEndingDirectorySeparator(Path.GetTempPath())||!Path.GetFileName(profile).StartsWith("luma-ui-profile-"))throw new IOException("unsafe cleanup");if(Directory.Exists(profile))Directory.Delete(profile,true);}
  return n;
 }
 sealed class DelayedManagement:IManagementApi
 {
  public List<TaskCompletionSource<JsonElement>> Reads=[];public TaskCompletionSource<JsonElement> Write=new();public int Writes;
  public JsonElement StatusValue;
  public Task<JsonElement> ReadAsync(string section,object args,CancellationToken token){var t=new TaskCompletionSource<JsonElement>();Reads.Add(t);return t.Task;}
  public Task<JsonElement> WriteAsync(string action,object args,string operationId,CancellationToken token){Writes++;return Write.Task;}
  public Task<JsonElement> StatusAsync(string id,CancellationToken token)=>Task.FromResult(StatusValue);
 }
}
