using System.Text.Json;
using Luma.Core.Engine;
namespace Luma.Core.Management;

public interface IManagementApi
{
 Task<JsonElement> ReadAsync(string section,object args,CancellationToken token);
 Task<JsonElement> WriteAsync(string action,object args,string operationId,CancellationToken token);
 Task<JsonElement> StatusAsync(string id,CancellationToken token);
}
public sealed class ManagementApi(EngineClient client):IManagementApi
{
 public Task<JsonElement> ReadAsync(string section,object args,CancellationToken token)=>client.CallAsync("manage.read",new{section,args},cancellationToken:token);
 public Task<JsonElement> WriteAsync(string action,object args,string operationId,CancellationToken token)=>action.StartsWith("files.",StringComparison.Ordinal)&&!client.FileOperationsEnabled?Task.FromException<JsonElement>(new EngineRpcException("method_not_allowed")):client.CallAsync("manage.write",new{action,args,operationId},cancellationToken:token);
 public Task<JsonElement> StatusAsync(string operationId,CancellationToken token)=>client.CallAsync("manage.status",new{operationId},cancellationToken:token);
}
// UI-thread owned. Navigation invalidates reads, but cannot undo submitted persistent jobs.
public sealed class ManagementState:IDisposable
{
 private readonly IManagementApi api;private readonly PendingOperationStore? pending;
 public ManagementState(IManagementApi api,PendingOperationStore? pending=null)
 {this.api=api;this.pending=pending;try{if(pending?.Load() is{} operation){OperationId=operation.OperationId;LastAction=operation.Action;Uncertain=true;Feedback="上次操作需要核对；不会自动重试。";}}catch(EngineRpcException){Uncertain=true;Feedback="上次操作记录无法核对，请保留数据并查看任务中心。";}}
 private long _generation;private bool _disposed;private CancellationTokenSource _reads=new();
 public JsonElement Data{get;private set;}
 public JsonElement Result{get;private set;}
 public bool Writing{get;private set;}
 public bool Uncertain{get;private set;}
 public string OperationId{get;private set;}="";
 public string LastAction{get;private set;}="";
 public string LastResolvedAction{get;private set;}="";
 public JsonElement LastResolvedResult{get;private set;}
 public string Outcome{get;private set;}="none";
 public string Feedback{get;private set;}="";
 public event Action? Changed;
 public void ChangeScope(){_generation++;_reads.Cancel();_reads.Dispose();_reads=new();}
 public async Task LoadAsync(string section,object? args=null)
 {
  long generation=_generation;
  try{var data=await api.ReadAsync(section,args??new{},_reads.Token);if(!_disposed&&generation==_generation){Data=data;Changed?.Invoke();}}
  catch(Exception) when(_disposed||generation!=_generation){}
 }
 public async Task<bool> WriteAsync(string action,object args)
 {
  if(_disposed||Writing||Uncertain)return false;
  try{OperationId=Guid.NewGuid().ToString("N");LastAction=action;pending?.Save(OperationId,action);}catch(Exception){Feedback="无法记录操作，提交已暂停。";Changed?.Invoke();return false;}
  Writing=true;Outcome="pending";Result=default;LastResolvedAction="";LastResolvedResult=default;Feedback="正在提交…";Changed?.Invoke();
  try{Result=await api.WriteAsync(action,args,OperationId,CancellationToken.None);pending?.Clear();Outcome="completed";Feedback=CivitaiFeedback.Completed(action,Result);return true;}
  catch(Exception error){Uncertain=error is not EngineRpcException rpc||rpc.Code is not ("invalid_request" or "method_not_allowed" or "busy" or "stale_identity" or "not_found" or "unsupported" or "not_ready" or "invalid_token" or "model_hash_missing" or "stale_review" or "confirmation_required" or "recovery_required" or "duplicate_recovery");Feedback=Uncertain?"结果待核对":error is EngineRpcException known?known.Code switch{"busy"=>"正在处理其它操作","stale_identity"=>"选择已更新","not_ready"=>"识别模型未就绪","invalid_token"=>"密钥格式错误","stale_review"=>"预览已更新","confirmation_required"=>"未确认预览","recovery_required" or "duplicate_recovery"=>"有待恢复记录","model_hash_missing"=>"没有模型哈希",_=>"输入无效"}:"操作失败";return false;}
  finally{if(Outcome!="completed")Outcome=Uncertain?"unknown":"failed";if(!Uncertain){try{pending?.Clear();}catch(Exception error) when(error is IOException or EngineRpcException){Uncertain=true;Feedback="状态待核对";}}Writing=false;if(!_disposed)Changed?.Invoke();}
 }
 public async Task ResolveAsync()
 {
  if(OperationId.Length==0||Writing||_disposed)return;
  LastResolvedAction="";LastResolvedResult=default;
  var status=await api.StatusAsync(OperationId,CancellationToken.None);
  if(status.TryGetProperty("action",out var action)&&action.ValueKind==JsonValueKind.String)LastResolvedAction=action.GetString()??"";
  if(status.TryGetProperty("result",out var receiptResult))LastResolvedResult=receiptResult;
  string state=status.GetProperty("state").GetString()??"unknown";
  Outcome=state;
  Uncertain=state is "unknown" or "pending";
  if(state=="completed"&&status.TryGetProperty("result",out var result))Result=result;
  if(!Uncertain)pending?.Clear();
  Feedback=state=="completed"?CivitaiFeedback.Completed(LastResolvedAction.Length>0?LastResolvedAction:LastAction,Result):Uncertain?"结果待核对":"操作未完成";
  Changed?.Invoke();
 }
 public void Dispose(){if(_disposed)return;_disposed=true;ChangeScope();_reads.Dispose();}
}
