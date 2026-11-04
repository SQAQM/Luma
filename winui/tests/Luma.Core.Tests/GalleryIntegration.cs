using System.Security.Cryptography;
using System.Text.Json;
using Luma.Core.Contracts;
using Luma.Core.Engine;
using Luma.Core.Gallery;

internal static class GalleryIntegration
{
    public static async Task<int> Run(string executable,string output)
    {
        Directory.CreateDirectory(output);string data=PreviewDirectory.Create(output);
        await EngineProcess.PrepareDemoAsync(executable,data);
        int passed=0;void Check(bool value,string name){if(!value)throw new Exception("REAL_GALLERY_FAIL "+name);passed++;}
        var hashes=Directory.GetFiles(Path.Combine(data,"demo-images")).ToDictionary(p=>p,p=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(p))));
        Check(hashes.Count==131,"explicit synthetic preparation");
        try{await EngineProcess.PrepareDemoAsync(executable,data);throw new Exception("existing catalog reseeded");}catch(EngineRpcException){passed++;}
        await using var client=new EngineClient(executable,data);await client.ConnectAsync();var api=new GalleryApi(client);
        using var model=new GalleryModel(api);await model.RefreshAsync();
        Check(model.Items.Count==120&&model.Total==131,"real page bounded");
        var first=model.Items[0];await model.SelectAsync(first);
        Check(model.Detail!.Prompt.Length>2048&&model.Detail.NegativePrompt.Length>1024,"full Unicode cached detail");
        Check(model.Detail.Tags.Single().Name=="风景"&&model.Detail.Tags.Single().Effective,"manual fixture content tag");
        await model.SelectAsync(model.Items[1]);Check(model.Detail!.NegativePrompt=="","empty negative exact");
        await model.NextAsync();Check(model.Items.Count==11&&model.Items.Last().Id==1000001,"cursor page and long id");
        await model.PreviousAsync();Check(model.Items[0].Id==first.Id,"previous cursor snapshot");
        await model.ChangeScopeAsync("","name",1,null);Check(model.Total==87,"indexed semantic filter");
        await model.ChangeScopeAsync("","name",null,null,artistFilter:"合成画师 A");Check(model.Total==66,"exact artist filter");
        await model.ChangeScopeAsync("","name",null,null,modelFilter:"合成模型 SDXL",formatFilter:"PNG");Check(model.Total>0&&model.Items.All(i=>i.Model=="合成模型 SDXL"&&i.Format=="PNG"),"combined model and format");
        await model.ChangeScopeAsync("","name",null,null,promptTagFilter:"synthetic-prompt-token");Check(model.Total==131,"prompt token filter remains separate");
        await model.ChangeScopeAsync("no-such-fixture","name",null,null);Check(model.Items.Count==0&&model.Detail is null,"real no matches clears detail");
        foreach(var sort in new[]{"name","name_desc","newest","oldest","modified_desc","modified_asc","size_desc","size_asc","resolution_desc","resolution_asc","rating_desc","rating_asc"})
        {await model.ChangeScopeAsync("",sort,null,null);Check(model.Items.Count==120&&model.Total==131,"real sort "+sort);}
        var asset=await client.CallAsync("catalog.thumbnail",new{imageId=first.Id,identity=first.Identity,size=320});
        var resource=new CachedAsset(asset.GetProperty("resourceId").GetString()!,asset.GetProperty("relativePath").GetString()!);
        Check(File.Exists(EngineAssets.Resolve(data,resource))&&asset.GetProperty("identity").GetString()==first.Identity,"controlled actual thumbnail");
        var bad=client.CallAsync("catalog.detail",new{imageId=first.Id,identity=new string('0',64)});
        try{await bad;throw new Exception("stale identity accepted");}catch(EngineRpcException e)when(e.Code=="stale_identity"){passed++;}
        Check(hashes.All(pair=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(pair.Key)))==pair.Value),"all synthetic originals unchanged");
        await client.StopAsync();Check(!client.IsAlive,"gallery closes safely");
        File.WriteAllText(Path.Combine(output,"gallery-integration-report.json"),JsonSerializer.Serialize(new{result="passed",count=passed,syntheticImages=hashes.Count,allOriginalBytesUnchanged=true,productionDataAccess=false}));return passed;
    }
}
