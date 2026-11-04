using Luma.Core.Engine;
namespace Luma.Core.Gallery;
public sealed record ShellIntent(string Action,string Path)
{
    private static readonly HashSet<string> Images=new(StringComparer.OrdinalIgnoreCase){".png",".jpg",".jpeg",".webp",".gif",".bmp",".tif",".tiff",".ico",".avif",".heic",".jxl"};
    private static readonly HashSet<string> Models=new(StringComparer.OrdinalIgnoreCase){".safetensors",".ckpt",".gguf",".pt",".pth",".bin"};
    public static ShellIntent Create(string action,string path)
    {
        if(action is not ("open" or "folder" or "select" or "copy"))throw new EngineRpcException("unsafe_file_action");
        return CreateAllowed(action,path,Images);
    }
    public static ShellIntent CreateModel(string action,string path)
    {
        if(action is not ("select" or "copy"))throw new EngineRpcException("unsafe_file_action");
        return CreateAllowed(action,path,Models);
    }
    private static ShellIntent CreateAllowed(string action,string path,HashSet<string> extensions)
    {
        if(!System.IO.Path.IsPathFullyQualified(path)||path.StartsWith(@"\\",StringComparison.Ordinal)||path.Length>32760||path.Any(char.IsControl)||path.Contains('"')||!extensions.Contains(System.IO.Path.GetExtension(path)))throw new EngineRpcException("unsafe_file_action");
        return new(action,System.IO.Path.GetFullPath(path));
    }
}
