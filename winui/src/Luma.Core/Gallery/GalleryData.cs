using System.Text.Json;
using Luma.Core.Contracts;
using Luma.Core.Engine;
namespace Luma.Core.Gallery;

public sealed record ContentTag(long Id,string Name,bool Effective);
public sealed record GalleryItem(long Id,string Identity,string Name,string Path,int Width,int Height,long Size,string Format,string Model,string Family,ContentTag[] Tags)
{public bool Favorite{get;init;}public int Rating{get;init;}}
public sealed record GalleryDetail(long Id,string Identity,string Path,string Name,string Prompt,string NegativePrompt,ContentTag[] Tags,string Model,string Family,int Width,int Height)
{public string[] PromptTags{get;init;}=[];}
public sealed record GalleryPage(GalleryItem[] Items,string? NextCursor,long Total);
public interface IGalleryApi
{
    Task<GalleryPage> QueryAsync(QueryRequest request,EngineContext context,CancellationToken token);
    Task<GalleryDetail> DetailAsync(GalleryItem item,EngineContext context,CancellationToken token);
}
public sealed class GalleryApi(EngineClient client):IGalleryApi
{
    private static string Text(JsonElement e,string key)=>e.TryGetProperty(key,out var v)&&v.ValueKind==JsonValueKind.String?v.GetString()!:"";
    private static ContentTag[] Tags(JsonElement e)=>e.GetProperty("content_tags").EnumerateArray().Select(t=>new ContentTag(t.GetProperty("id").GetInt64(),Text(t,"name"),t.GetProperty("enabled").GetBoolean()&&!t.GetProperty("stale").GetBoolean()&&(Text(t,"source")=="manual"||t.GetProperty("score").GetDouble()>=t.GetProperty("threshold").GetDouble()))).ToArray();
    private static void Identity(JsonElement value)
    { string identity=Text(value,"identity");if(identity.Length!=64||!identity.All(Uri.IsHexDigit)||value.GetProperty("id").GetInt64()<1)throw new EngineProtocolException(); }
    public static GalleryItem ParseItem(JsonElement e){Identity(e);return new(e.GetProperty("id").GetInt64(),Text(e,"identity"),Text(e,"name"),Text(e,"path"),e.GetProperty("width").GetInt32(),e.GetProperty("height").GetInt32(),e.GetProperty("size").GetInt64(),Text(e,"format"),Text(e,"model"),Text(e,"family"),Tags(e)){Favorite=e.TryGetProperty("favorite",out var f)&&(f.ValueKind==JsonValueKind.True||f.ValueKind==JsonValueKind.Number&&f.GetInt32()!=0),Rating=e.TryGetProperty("rating",out var r)&&r.ValueKind==JsonValueKind.Number?r.GetInt32():0};}
    public async Task<GalleryPage> QueryAsync(QueryRequest request,EngineContext context,CancellationToken token)
    {
        var page=await client.QueryAsync(request,context,token).ConfigureAwait(false);
        if(page.Items.Length>120||page.Total<page.Items.Length)throw new EngineProtocolException();
        var items=page.Items.Select(ParseItem).ToArray();
        if(items.Select(i=>i.Id).Distinct().Count()!=items.Length)throw new EngineProtocolException();
        return new(items,page.NextCursor,page.Total);
    }
    public async Task<GalleryDetail> DetailAsync(GalleryItem item,EngineContext context,CancellationToken token)
    {
        var e=await client.CallAsync("catalog.detail",new{imageId=item.Id,identity=item.Identity},context,token).ConfigureAwait(false);Identity(e);
        if(e.GetProperty("id").GetInt64()!=item.Id||Text(e,"identity")!=item.Identity)throw new EngineRpcException("stale_identity");
        return new(item.Id,item.Identity,Text(e,"path"),Text(e,"name"),Text(e,"prompt"),Text(e,"negative_prompt"),Tags(e),Text(e,"model"),Text(e,"family"),e.GetProperty("width").GetInt32(),e.GetProperty("height").GetInt32()){PromptTags=e.GetProperty("prompt_tags").EnumerateArray().Select(t=>t.GetString()??"").ToArray()};
    }
}
