using System.Text.Json;
using Luma.Core.Management;
internal static class ModelSyncTests
{
 public static int Run(){int n=0;void Check(bool v,string label){if(!v)throw new Exception("MODEL_SYNC_FAIL "+label);n++;}
  var all=ModelSyncRequest.Create([],7,67,"",[],[]);var value=JsonSerializer.SerializeToElement(all.Args);
  Check(all.Count==67&&value.GetProperty("scope").GetProperty("rootId").GetInt64()==7&&!value.TryGetProperty("ids",out _),"whole directory is not a page of 60 ids");
  Check(!value.GetProperty("forceRefresh").GetBoolean()&&!value.GetProperty("compatibility").GetBoolean()&&value.GetProperty("downloadCovers").GetBoolean(),"completion reuses data and exports minimal metadata by default");
  var selected=ModelSyncRequest.Create([9,9,10],7,67,"",[],[]);value=JsonSerializer.SerializeToElement(selected.Args);
  Check(selected.Count==2&&value.GetProperty("ids").GetArrayLength()==2&&!value.TryGetProperty("scope",out _),"selection stays explicit");
  value=JsonSerializer.SerializeToElement(ModelSyncRequest.Create([9,10],7,67,"",[],[],forceRefresh:true,compatibility:true).Args);
  Check(value.GetProperty("forceRefresh").GetBoolean()&&value.GetProperty("compatibility").GetBoolean()&&value.GetProperty("ids").GetArrayLength()==2,"refresh and compatibility are explicit independent options");
  value=JsonSerializer.SerializeToElement(ModelSyncRequest.Create([],null,8,"search",["LORA"],["Anima"]).Args);
  Check(value.GetProperty("scope").GetProperty("types")[0].GetString()=="LORA"&&value.GetProperty("scope").GetProperty("families")[0].GetString()=="Anima"&&value.GetProperty("scope").GetProperty("query").GetString()=="search","scope keeps combined filters");
  var job=JsonSerializer.SerializeToElement(new{kind="model_sync",status="running",processed=11,total=67,errors=1,issues=new[]{new{code="not_found",count=1}},feedback="PRIVATE_MODEL_NAME_OR_TOKEN"});
  Check(CivitaiFeedback.Issues(job).Contains("哈希未匹配")&&CivitaiFeedback.Issues(job).Contains("1")&&!CivitaiFeedback.Issues(job).Contains("PRIVATE"),"running task displays fixed reason only");
  job=JsonSerializer.SerializeToElement(new{status="completed_with_errors",processed=1,total=67,issues=new[]{new{code="unauthorized",count=1}}});
  Check(CivitaiFeedback.Issues(job).Contains("密钥")&&CivitaiFeedback.Issues(job).Contains("66"),"early stop shows reason and unprocessed count");
  job=JsonSerializer.SerializeToElement(new{status="completed",errors=0,feedback="模型资料同步完成；封面已保存 167 个"});
  Check(CivitaiFeedback.SyncResult(job)=="封面 167 个","cover feedback beyond page limit");return n;
 }
}
