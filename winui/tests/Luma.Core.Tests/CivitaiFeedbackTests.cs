using System.Text.Json;
using Luma.Core.Management;

static class CivitaiFeedbackTests
{
 public static async Task<int> Run()
 {
  int count=0;
  void Check(bool value,string name){if(!value)throw new Exception("CIVITAI_FEEDBACK_FAIL "+name);count++;}
  foreach(string action in new[]{"credential.test","credential.connect"})
  {
   var api=new ResultApi(JsonSerializer.SerializeToElement(new{ok=false,authenticated=false,code="unauthorized",message="PRIVATE_BODY_DO_NOT_DISPLAY"}));
   using var state=new ManagementState(api);
   Check(await state.WriteAsync(action,new{}),"request receipt completed");
   Check(state.Feedback.Contains("密钥")&&state.Feedback.Contains("无效"),"failed authentication must be visible instead of operation completed");
   Check(!state.Feedback.Contains("PRIVATE_BODY"),"unknown upstream message stays private");
   await state.ResolveAsync();
   Check(state.Feedback.Contains("无效"),"receipt resolution preserves failed authentication feedback");
  }
  foreach(var sample in new[]{(JsonSerializer.SerializeToElement(new{ok=true,authenticated=true,code="ok"}),"已验证"),
                              (JsonSerializer.SerializeToElement(new{ok=true,authenticated=false,code="ok"}),"匿名"),
                              (JsonSerializer.SerializeToElement(new{ok=false,authenticated=false,code="network"}),"连接")})
  {
   using var state=new ManagementState(new ResultApi(sample.Item1));
   await state.WriteAsync("credential.test",new{});
   Check(state.Feedback.Contains(sample.Item2),"connection result differs by actual outcome");
  }
  Check(CivitaiFeedback.CoverStatus(JsonSerializer.SerializeToElement(new{status="indexed",cover_available=false}))=="待获取封面","unrequested is distinct from unavailable");
  Check(CivitaiFeedback.CoverStatus(JsonSerializer.SerializeToElement(new{status="synced",cover_available=false}))=="未获取到封面","no upstream preview has its own state");
  Check(CivitaiFeedback.CoverStatus(JsonSerializer.SerializeToElement(new{status="sync_error_unauthorized",cover_available=false})).Contains("验证失败"),"authorization failure remains actionable on cards");
  Check(CivitaiFeedback.SyncResult(JsonSerializer.SerializeToElement(new{status="completed",errors=0,feedback="模型资料同步完成；封面已保存 3 个"}))=="封面 3 个","completed sync displays cover count");
  Check(CivitaiFeedback.SyncResult(JsonSerializer.SerializeToElement(new{status="completed_with_errors",errors=1,feedback="模型资料同步完成；封面已保存 0 个；Civitai 密钥无效或已过期，请重新设置。"}))=="密钥无效","key failure has priority over cover count");
  Check(CivitaiFeedback.SyncResult(JsonSerializer.SerializeToElement(new{status="completed_with_errors",errors=1,feedback="PRIVATE_UNKNOWN_BODY"}))=="部分未完成","arbitrary task bodies are not display text");
  return count;
 }
 private sealed class ResultApi(JsonElement value):IManagementApi
 {
  public Task<JsonElement> ReadAsync(string section,object args,CancellationToken token)=>Task.FromResult(value);
  public Task<JsonElement> WriteAsync(string action,object args,string operationId,CancellationToken token)=>Task.FromResult(value);
  public Task<JsonElement> StatusAsync(string id,CancellationToken token)=>Task.FromResult(JsonSerializer.SerializeToElement(new{state="completed",result=value}));
 }
}
