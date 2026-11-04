using System.Text.Json;
using Luma.Core.Management;

static class ModelDirectoryAddTests
{
 public static async Task<int> Run()
 {
  int n=0;
  void Check(bool value,string name){if(!value)throw new Exception("model directory: "+name);n++;}
  var api=new Fake();using var state=new ManagementState(api);var flow=new ModelDirectoryAdd(state);
  Check(await flow.AddAsync("synthetic-models"),"acknowledged add requests index");
  Check(flow.RootId==2&&flow.IndexRequested&&!flow.Busy,"adopt exact added root");
  Check(api.Writes.Count==2&&api.Writes[0].Action=="models.add"&&api.Writes[0].Args.GetProperty("path").GetString()=="synthetic-models"&&api.Writes[1].Action=="models.scan"&&api.Writes[1].Args.GetProperty("rootId").GetInt64()==2,"add then scan correct root once");
  api.Receipt=Json(new{state="completed",action="models.scan",result=new{jobId="synthetic"}});
  Check(!await flow.ResolveAsync()&&api.Writes.Count==2,"completed scan resolve cannot rescan");
  api.AddResult=Json(new{id=9});Check(await flow.AddAsync("second"),"new add allowed");
  Check(flow.RootId==9&&api.Writes.Count==4&&api.Writes[3].Args.GetProperty("rootId").GetInt64()==9,"new add resets root and continuation");

  var lost=new Fake{LoseAdd=true};using var lostState=new ManagementState(lost);var recovery=new ModelDirectoryAdd(lostState);
  Check(!await recovery.AddAsync("lost")&&lostState.Uncertain&&recovery.RootId is null&&!recovery.IndexRequested&&lost.Writes.Count==1,"lost add never scans before explicit resolve");
  string addId=lost.Writes[0].Id;
  Check(!await recovery.AddAsync("blocked")&&lost.Writes.Count==1,"unresolved add blocks replacement");
  lost.Receipt=Json(new{state="pending",action="models.add"});
  Check(!await recovery.ResolveAsync()&&lost.Writes.Count==1,"pending add does not scan");
  lost.Receipt=Json(new{state="completed",action="models.add",result=new{id=7}});
  Check(await recovery.ResolveAsync()&&recovery.RootId==7&&recovery.IndexRequested&&lost.Writes.Count==2,"completed add continues exactly once");
  Check(lost.StatusIds.All(id=>id==addId)&&lost.Writes[1].Args.GetProperty("rootId").GetInt64()==7,"resolve and scan bind exact operation and root");
  Check(!await recovery.ResolveAsync()&&lost.Writes.Count==2,"repeat resolve cannot duplicate scan");

  var uncertain=new Fake{LoseScan=true};using var uncertainState=new ManagementState(uncertain);var scanFlow=new ModelDirectoryAdd(uncertainState);
  Check(!await scanFlow.AddAsync("scan-lost")&&scanFlow.IndexRequested&&uncertainState.Uncertain,"uncertain scan is recorded as requested");
  uncertain.Receipt=Json(new{state="completed",action="models.scan",result=new{jobId="synthetic"}});
  Check(!await scanFlow.ResolveAsync()&&!await scanFlow.ResolveAsync()&&uncertain.Writes.Count==2&&!uncertainState.Uncertain,"scan can resolve but never replay");

  var historical=new Fake{LoseAdd=true};using var historicalState=new ManagementState(historical);
  Check(!await historicalState.WriteAsync("models.add",new{path="historical"}),"seed historical unknown add");
  var fresh=new ModelDirectoryAdd(historicalState);historical.Receipt=Json(new{state="completed",action="models.add",result=new{id=12}});
  Check(await fresh.ResolveAsync()&&fresh.RootId==12&&!fresh.IndexRequested&&historical.Writes.Count==1,"new coordinator resolves historical add without scan authorization");

  foreach(string outcome in new[]{"pending","failed","expired","unknown"})
  {
   var rejected=new Fake{LoseAdd=true};using var rejectedState=new ManagementState(rejected);var candidate=new ModelDirectoryAdd(rejectedState);
   await candidate.AddAsync("synthetic");rejected.Receipt=Json(new{state=outcome,action="models.add",result=new{id=20}});
   Check(!await candidate.ResolveAsync()&&candidate.RootId is null&&!candidate.IndexRequested&&rejected.Writes.Count==1,outcome+" receipt cannot scan");
  }
  foreach(var bad in new[]{Json(new{}),Json(new{id=0}),Json(new{id=-1}),Json(new{id="2"}),Json(new{id=2.5}),Json(new{id=(long?)null}),Json(new[]{2}),Json(new{id=1e30})})
  {
   var malformed=new Fake{AddResult=bad};using var malformedState=new ManagementState(malformed);var candidate=new ModelDirectoryAdd(malformedState);
   Check(!await candidate.AddAsync("synthetic")&&candidate.RootId is null&&!candidate.IndexRequested&&malformed.Writes.Count==1,"malformed acknowledged root cannot scan");
   var malformedLost=new Fake{LoseAdd=true};using var malformedLostState=new ManagementState(malformedLost);var recovered=new ModelDirectoryAdd(malformedLostState);
   await recovered.AddAsync("synthetic");malformedLost.Receipt=Json(new{state="completed",action="models.add",result=bad});
   Check(!await recovered.ResolveAsync()&&recovered.RootId is null&&!recovered.IndexRequested&&malformedLost.Writes.Count==1,"malformed resolved root cannot scan");
  }
  var wrong=new Fake{LoseAdd=true};using var wrongState=new ManagementState(wrong);
  await wrongState.WriteAsync("models.remove",new{rootId=3});var wrongFlow=new ModelDirectoryAdd(wrongState);
  wrong.Receipt=Json(new{state="completed",action="models.remove",result=new{id=3}});
  Check(!await wrongFlow.ResolveAsync()&&wrongFlow.RootId is null&&wrong.Writes.Count==1,"different operation action cannot continue");

  foreach(var receipt in new[]{Json(new{state="completed",action="models.remove",result=new{id=3}}),Json(new{state="completed",result=new{id=3}})})
  {
   var mismatch=new Fake{LoseAdd=true};using var mismatchState=new ManagementState(mismatch);var candidate=new ModelDirectoryAdd(mismatchState);
   await candidate.AddAsync("synthetic");mismatch.Receipt=receipt;
   Check(!await candidate.ResolveAsync()&&candidate.RootId is null&&!candidate.IndexRequested&&mismatch.Writes.Count==1,"wrong or missing receipt action cannot authorize scan");
  }
  var stale=new Fake{LoseAdd=true};using var staleState=new ManagementState(stale);var staleFlow=new ModelDirectoryAdd(staleState);
  await staleFlow.AddAsync("synthetic");stale.Receipt=Json(new{state="completed",action="models.remove",result=new{id=99}});
  await staleFlow.ResolveAsync();stale.Receipt=Json(new{state="completed",action="models.add"});
  Check(!await staleFlow.ResolveAsync()&&staleFlow.RootId is null&&!staleFlow.IndexRequested&&stale.Writes.Count==1,"missing current result cannot reuse prior wrong-action root");

  var concurrent=new Fake{LoseAdd=true};using var concurrentState=new ManagementState(concurrent);var guarded=new ModelDirectoryAdd(concurrentState);
  await guarded.AddAsync("synthetic");concurrent.StatusGate=new(TaskCreationOptions.RunContinuationsAsynchronously);
  var resolving=guarded.ResolveAsync();Check(guarded.Busy,"busy spans receipt wait");
  Check(!await guarded.ResolveAsync()&&!await guarded.AddAsync("racing")&&concurrent.StatusIds.Count==1,"concurrent resolve and add blocked");
  concurrent.StatusGate.SetResult(Json(new{state="completed",action="models.add",result=new{id=45}}));
  Check(await resolving&&!guarded.Busy&&concurrent.Writes.Count==2&&concurrent.Writes[1].Args.GetProperty("rootId").GetInt64()==45,"guarded resolve submits one correct scan");

  var delayed=new Fake{AddGate=new(TaskCreationOptions.RunContinuationsAsynchronously)};using var delayedState=new ManagementState(delayed);var adding=new ModelDirectoryAdd(delayedState);
  var operation=adding.AddAsync("delayed");Check(adding.Busy&&!await adding.AddAsync("duplicate")&&!await adding.ResolveAsync()&&delayed.Writes.Count==1,"busy spans add chain");
  delayed.AddGate.SetResult(Json(new{id=2}));Check(await operation&&!adding.Busy&&delayed.Writes.Count==2,"add guard clears after completion");

  var throwing=new Fake{LoseAdd=true};using var throwingState=new ManagementState(throwing);var resilient=new ModelDirectoryAdd(throwingState);
  await resilient.AddAsync("synthetic");throwing.StatusGate=new(TaskCreationOptions.RunContinuationsAsynchronously);var failedResolve=resilient.ResolveAsync();throwing.StatusGate.SetException(new IOException("synthetic status failure"));
  try{await failedResolve;}catch(IOException){}
  Check(!resilient.Busy&&throwing.Writes.Count==1,"finally releases busy on status exception");
  return n;
 }
 static JsonElement Json(object value)=>JsonSerializer.SerializeToElement(value);
 sealed class Fake:IManagementApi
 {
  public readonly List<(string Action,JsonElement Args,string Id)> Writes=[];
  public readonly List<string> StatusIds=[];
  public bool LoseAdd,LoseScan;
  public JsonElement AddResult=Json(new{id=2});
  public JsonElement Receipt=Json(new{state="pending",action="models.add"});
  public TaskCompletionSource<JsonElement>? AddGate,StatusGate;
  public Task<JsonElement> ReadAsync(string section,object args,CancellationToken token)=>throw new InvalidOperationException("unexpected read");
  public Task<JsonElement> WriteAsync(string action,object args,string operationId,CancellationToken token)
  {
   Writes.Add((action,Json(args),operationId));
   if(action=="models.add"&&AddGate is not null)return AddGate.Task;
   if((action=="models.scan"?LoseScan:LoseAdd))return Task.FromException<JsonElement>(new IOException("synthetic reply lost"));
   return Task.FromResult(action=="models.add"?AddResult:Json(new{jobId="synthetic"}));
  }
  public Task<JsonElement> StatusAsync(string id,CancellationToken token){StatusIds.Add(id);return StatusGate?.Task??Task.FromResult(Receipt);}
 }
}
