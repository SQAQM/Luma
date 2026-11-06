using System.Text.Json;using System.Security.Cryptography;using Luma.Core.Viewer;using Luma.Core.Gallery;using Luma.Core.Engine;using Luma.Core.Contracts;
internal static class ViewerIntegration
{
 public static async Task<int> Run(string executable,string output){
  Directory.CreateDirectory(output);string data=PreviewDirectory.Create(output);await EngineProcess.PrepareDemoAsync(executable,data);int n=0;
  void Check(bool v,string s){if(!v)throw new Exception("REAL_VIEWER_FAIL "+s);n++;}
  var hashes=Directory.GetFiles(Path.Combine(data,"demo-images")).ToDictionary(p=>p,p=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(p))));
  await using var client=new EngineClient(executable,data);await client.ConnectAsync();var page=await new GalleryApi(client).QueryAsync(new(new(){["sort"]="name"}),new(),default);
  var api=new ViewerApi(client);using var model=new ViewerModel(api);await model.OpenAsync(page.Items.Last(),new(){["sort"]="name"});await model.MoveAsync(1);Check(model.Current?.Id==121,"real120 to121 neighbors");
  foreach(string sort in new[]{"name","name_desc","newest","oldest","modified_desc","modified_asc","size_desc","size_asc","resolution_desc","resolution_asc","rating_desc","rating_asc"}){await model.SetSortAsync(sort);Check(model.Current?.Id==121&&model.Window.Count<=9,"real scoped sort "+sort);}
  await model.OpenAsync(page.Items[0],new(){["sort"]="name",["artist"]="合成画师 A"});Check(model.Total==66,"real artist scope");await model.MoveAsync(1);Check(model.Current?.Id==3,"scoped arrow skips other artist");
  var first=page.Items[0];foreach(string mode in new[]{"standard","original"}){
   var asset=PreviewAsset.Parse(await client.CallAsync("catalog.preview.v2",new{imageId=first.Id,identity=first.Identity,mode}),first.Identity);string path=EngineAssets.Resolve(data,new CachedAsset(asset.ResourceId,asset.RelativePath));using var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);EngineAssets.VerifyOpenedFile(file,data);byte[] bytes=new byte[file.Length];file.ReadExactly(bytes);asset.Verify(bytes);Check(asset.Width==1600&&asset.Height==1067&&asset.Kind==(mode=="original"?"original":"standard"),"v2 actual resource "+mode);
  }
  Check((await client.CallAsync("catalog.preview.release",new{imageId=first.Id,identity=first.Identity})).GetProperty("released").GetBoolean(),"native client releases source container");
  Check(hashes.All(x=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(x.Key)))==x.Value),"all131 original bytes unchanged");model.Close();await client.StopAsync();Check(!client.IsAlive,"safe read-only viewer shutdown");
  File.WriteAllText(Path.Combine(output,"viewer-integration-report.json"),JsonSerializer.Serialize(new{result="passed",count=n,originals=hashes.Count,allOriginalBytesUnchanged=true}));return n;
 }
}
