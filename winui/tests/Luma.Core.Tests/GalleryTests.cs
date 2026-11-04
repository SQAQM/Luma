using Luma.Core.Gallery;
using Luma.Core.Contracts;
using Luma.Core.Engine;

internal static class GalleryTests
{
    public static async Task<int> Run()
    {
        int passed=0;
        void Check(bool value,string label){if(!value)throw new Exception("GALLERY_FAIL "+label);passed++;}
        var api=new FakeGallery();using var model=new GalleryModel(api);
        await model.RefreshAsync();Check(model.Items.Count==120 && model.CanNext,"first page bounded");
        await model.NextAsync();Check(model.Items.Count==11 && model.Items.Last().Id>1_000_000,"cursor crosses 120 and large id");
        await model.PreviousAsync();Check(model.Items.Count==120 && model.PageNumber==1,"previous window restored");
        model.SetSelection(model.Items.Take(3).Select(i=>i.Id));Check(model.SelectedIds.Count==3,"bounded page multiselect");
        await model.ChangeScopeAsync("none","name",null,null);Check(model.Items.Count==0 && model.SelectedIds.Count==0 && model.Detail is null,"scope clears old items and selection");
        var old=model.ChangeScopeAsync("slow","name",null,null);await Task.Delay(10);
        var current=model.ChangeScopeAsync("current","name",null,null);await current;await old;
        Check(model.Query=="current" && model.Items[0].Name=="current","out of order query rejected");
        var d1=model.SelectAsync(model.Items[0]);var d2=model.SelectAsync(model.Items[1]);await Task.WhenAll(d1,d2);
        Check(model.Detail?.Id==model.Items[1].Id,"late detail cannot overwrite selection");
        Check(model.Detail?.NegativePrompt=="" && model.Detail.Prompt.Length>2048,"full prompt and blank negative");
        Check(model.Detail?.Tags[0].Name=="风景","content tags distinct from prompts");
        model.Deactivate();Check(!model.Active,"deactivate cancels publication");
        model.Activate();Check(model.Items.Count<=120 && model.Query=="current","return retains scope bounded");
        var previousItem=model.Items[0];await model.ChangeScopeAsync("none","name",null,null);await model.SelectAsync(previousItem);
        Check(model.Current is null && model.Detail is null,"recycled item outside current scope rejected");
        var cancelled=model.ChangeScopeAsync("slow","name",null,null);await Task.Delay(10);model.Cancel();await cancelled;
        Check(!model.Loading&&model.Items.Count==0&&model.Error=="cancelled","cancel gives feedback and rejects late query");
        await model.ChangeScopeAsync("history","name",null,null);for(int i=0;i<140;i++)await model.NextAsync();
        Check(model.PageNumber==141 && model.CursorCount<=128 && model.Items.Count<=120,"cursor history remains bounded without resetting page number");
        foreach(string bad in new[]{"https://example.test/a.png",@"\\server\a.png",@"C:\a.exe"})
        {try{ShellIntent.Create("open",bad);throw new Exception("unsafe file launch accepted");}catch(EngineRpcException){passed++;}}
        var intent=ShellIntent.Create("select",@"C:\合成 空格\file.png");Check(intent.Path.EndsWith("file.png") && intent.Action=="select","shell path stays structured");
        foreach(string extension in new[]{"safetensors","ckpt","GGUF","pt","pth","bin"})
        {var modelIntent=ShellIntent.CreateModel("select",@"C:\合成 空格\模型."+extension);Check(modelIntent.Action=="select","model reveal accepts indexed model type");Check(ShellIntent.CreateModel("copy",modelIntent.Path).Path==modelIntent.Path,"model path copy preserves Unicode");}
        foreach(string action in new[]{"open","folder","run"}){try{ShellIntent.CreateModel(action,@"C:\合成\模型.safetensors");throw new Exception("model execution accepted");}catch(EngineRpcException){passed++;}}
        foreach(string bad in new[]{@"C:\合成\run.exe",@"\\server\share\file.gguf",@"relative\file.ckpt",@"C:\合成\file.png"}){try{ShellIntent.CreateModel("select",bad);throw new Exception("unsafe model action accepted");}catch(EngineRpcException){passed++;}}
        byte[] sealedBytes=[1,2,3];string checksum=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(sealedBytes));
        CachePayload.Verify(sealedBytes,3,checksum);passed++;
        try{CachePayload.Verify([3,2,1],3,checksum);throw new Exception("replaced cache accepted");}catch(EngineRpcException){passed++;}
        string assetRoot=Path.GetFullPath(Path.Combine(Path.GetTempPath(),"luma-stage4-handle-"+Guid.NewGuid().ToString("N")));
        Directory.CreateDirectory(Path.Combine(assetRoot,"thumbnails"));
        try
        {
            string inside=Path.Combine(assetRoot,"thumbnails","owned.jpg"),outside=Path.Combine(assetRoot,"outside.jpg");
            File.WriteAllBytes(inside,[1,2,3]);File.WriteAllBytes(outside,[4,5,6]);
            using(var file=File.OpenRead(inside)){EngineAssets.VerifyOpenedFile(file,assetRoot);passed++;}
            using(var file=File.OpenRead(outside))try{EngineAssets.VerifyOpenedFile(file,assetRoot);throw new Exception("opened outside handle accepted");}catch(EngineRpcException){passed++;}
        }
        finally
        {
            string prefix=Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
            if(!assetRoot.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)||!Path.GetFileName(assetRoot).StartsWith("luma-stage4-handle-",StringComparison.Ordinal))throw new Exception("unsafe test cleanup");
            Directory.Delete(assetRoot,true);
        }
        return passed;
    }

    private sealed class FakeGallery:IGalleryApi
    {
        public async Task<GalleryPage> QueryAsync(QueryRequest request,EngineContext context,CancellationToken token)
        {
            string q=request.Filters!.GetValueOrDefault("q")?.ToString()??"";
            await Task.Delay(q=="slow"?100:1); // Intentionally ignores cancellation to exercise stale publication.
            int start=request.Cursor is null?1:121,count=q=="none"?0:request.Cursor is null?120:11;
            return new(Enumerable.Range(start,count).Select(i=>new GalleryItem(i==131?1_000_001:i,new string('a',64),q=="current"?q:$"Fixture-{i}",@"C:\synthetic.png",480,320,1200,"PNG","fixture","Unknown",[])).ToArray(),(q=="history"||start==1) && count>0?"next":null,q=="none"?0:131);
        }
        public async Task<GalleryDetail> DetailAsync(GalleryItem item,EngineContext context,CancellationToken token)
        {await Task.Delay(item.Id==1?90:1);return new(item.Id,item.Identity,item.Path,item.Name,new string('雪',3000),"",[new(1,"风景",true)],"fixture","Unknown",480,320);}
    }
}
