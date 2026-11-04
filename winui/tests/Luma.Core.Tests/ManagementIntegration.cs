using System.Text.Json;
using Luma.Core.Engine;
using Luma.Core.Management;
using Luma.Core.Contracts;
static class ManagementIntegration
{
 public static async Task<int> Run(string executable,string output)
 {
  Directory.CreateDirectory(output);string directory=PreviewDirectory.Create(output);await EngineProcess.PrepareDemoAsync(executable,directory);
  int count=0;void Check(bool value,string name){if(!value)throw new Exception("management integration "+name);count++;}
  await using(var client=new EngineClient(executable,directory))
  {
   await client.ConnectAsync();Check(client.ManagementEnabled,"capability negotiated");var api=new ManagementApi(client);
   var runtime=await api.ReadAsync("runtime",new{},default);File.WriteAllText(Path.Combine(output,"runtime-check.json"),runtime.GetRawText());Check(runtime.GetProperty("onnxruntime").GetBoolean()&&runtime.GetProperty("tokenizers").GetBoolean(),"frozen runtime native imports");
   async Task<JsonElement> Write(string action,object args){string id=Guid.NewGuid().ToString("N");var result=await api.WriteAsync(action,args,id,default);var receipt=await api.StatusAsync(id,default);Check(receipt.GetProperty("state").GetString()=="completed","durable receipt");return result;}
   var definitions=await api.ReadAsync("tags",new{},default);Check(definitions.GetArrayLength()==2,"semantic definitions");
   var preview=await api.ReadAsync("labels",new{ids=new long[]{1,2}},default);
   await Write("labels.apply",new{ids=new long[]{1,2},changes=new[]{new{tag_id=1,action="exclude"}},snapshot=preview.GetProperty("snapshot_token").GetString()});
   var excluded=await api.ReadAsync("labels",new{ids=new long[]{1,2}},default);Check(excluded.GetProperty("tags")[0].GetProperty("excluded_count").GetInt32()==2,"manual excludes preserve semantic distinction");
   try{await Write("labels.apply",new{ids=new long[]{1,2},changes=new[]{new{tag_id=1,action="include"}},snapshot=preview.GetProperty("snapshot_token").GetString()});throw new Exception("stale edit accepted");}catch(EngineRpcException){count++;}
   var page=await client.QueryAsync(new(new(){["sort"]="name"}));var image=page.Items[0];
   await Write("image.patch",new{imageId=1,identity=image.GetProperty("identity").GetString(),favorite=true,rating=5});
   page=await client.QueryAsync(new(new(){["sort"]="rating_desc"}));Check(page.Items[0].GetProperty("rating").GetInt32()==5,"rating indexed");
   await Write("settings.save",new{values=new{theme="dark",reduceMotion=true,thumbnailCacheMiB=128}});var settings=await api.ReadAsync("settings",new{},default);Check(settings.GetProperty("theme").GetString()=="dark","shared preferences");Check(settings.GetProperty("thumbnail_cache_limit_bytes").GetInt64()==128L*1024*1024,"actual engine quota protocol");
   await Write("credential.set",new{token="synthetic-test-token-0123456789",remember=false});var credential=await api.ReadAsync("credential",new{},default);Check(credential.GetProperty("configured").GetBoolean()&&!credential.GetRawText().Contains("synthetic-test-token"),"credential presence only");await Write("credential.clear",new{});
   string folder=Path.Combine(output,"inert-models");Directory.CreateDirectory(folder);string weight=Path.Combine(folder,"合成模型 SDXL.safetensors");await File.WriteAllTextAsync(weight,"inert synthetic weight");
   var root=await Write("models.add",new{path=folder});var models=await api.ReadAsync("models",new{},default);Check(models.GetProperty("total").GetInt64()==0,"registration is not scanning");
   var task=await Write("models.scan",new{rootId=root.GetProperty("id").GetInt64()});string jobId=task.GetProperty("id").GetString()!;
   using(var timeout=new CancellationTokenSource(TimeSpan.FromSeconds(15)))
   {while(true){var jobs=await api.ReadAsync("jobs",new{},timeout.Token);Check(!jobs.GetRawText().Contains(folder)&&!jobs.GetRawText().Contains("payload"),"jobs project no private payload");var job=jobs.EnumerateArray().First(j=>j.GetProperty("id").GetString()==jobId);if(job.GetProperty("status").GetString()=="completed")break;await Task.Delay(40,timeout.Token);}}
   models=await api.ReadAsync("models",new{},default);Check(models.GetProperty("total").GetInt64()==1,"property inventory");Check(await File.ReadAllTextAsync(weight)=="inert synthetic weight","weight bytes unchanged");
   long modelId=models.GetProperty("items")[0].GetProperty("id").GetInt64();
   var verified=await api.ReadAsync("modelImages",new{modelId,match="verified"},default);Check(verified.GetProperty("total").GetInt64()==0,"same name not verified");
   var candidates=await api.ReadAsync("modelImages",new{modelId,match="candidate"},default);Check(candidates.GetProperty("total").GetInt64()==66&&candidates.GetProperty("items").GetArrayLength()==60,"bounded candidates first page");
   var tail=await api.ReadAsync("modelImages",new{modelId,match="candidate",cursor=candidates.GetProperty("next_cursor").GetString()},default);Check(tail.GetProperty("items").GetArrayLength()==6,"candidate next page");
   Check(client.EngineVersion=="0.8.5-stage8.6","stage8 reported version");
   var diagnosis=await api.ReadAsync("diagnosis",new{},default);Check(!diagnosis.GetProperty("privacy").GetProperty("prompt_access").GetBoolean(),"model leaves only");
   await Write("registry.save",new{entries=new[]{new{kind="name",match="fixture-checkpoint",family="Anima",enabled=true}}});
   await client.StopAsync();
  }
  await using(var reopened=new EngineClient(executable,directory))
  {await reopened.ConnectAsync();var api=new ManagementApi(reopened);var jobs=await api.ReadAsync("jobs",new{},default);Check(jobs.GetArrayLength()>0,"persistent jobs reopen");var roots=await api.ReadAsync("modelRoots",new{},default);Check(roots.GetArrayLength()==1,"model registration persists");await reopened.StopAsync();}
  var manifest=JsonDocument.Parse(File.ReadAllText(Path.Combine(directory,"synthetic-manifest.json")));int originals=0;foreach(var item in manifest.RootElement.GetProperty("images").EnumerateArray()){string path=Path.Combine(directory,"demo-images",item.GetProperty("name").GetString()!);Check(Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant()==item.GetProperty("sha256").GetString(),"original unchanged");originals++;}
  File.WriteAllText(Path.Combine(output,"management-integration-report.json"),JsonSerializer.Serialize(new{result="passed",count,originals,privateDataAccess=false,realNetworkUsed=false}));return count;
 }
}
