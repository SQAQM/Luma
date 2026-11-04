using Luma.Core.Contracts;
using Microsoft.Win32.SafeHandles;
using System.Runtime.InteropServices;
using System.Text;
namespace Luma.Core.Engine;
public static class EngineAssets
{
    public static void VerifyOpenedFile(FileStream file,string dataDirectory)
    {
        if(!OperatingSystem.IsWindows())return;
        var buffer=new StringBuilder(32768);uint length=GetFinalPathNameByHandleW(file.SafeFileHandle,buffer,(uint)buffer.Capacity,0);
        if(length==0||length>=buffer.Capacity)throw new EngineRpcException("asset_boundary");
        string actual=buffer.ToString();if(actual.StartsWith(@"\\?\",StringComparison.Ordinal))actual=actual[4..];
        string root=Path.GetFullPath(Path.Combine(dataDirectory,"thumbnails"))+Path.DirectorySeparatorChar;
        if(!actual.StartsWith(root,StringComparison.OrdinalIgnoreCase))throw new EngineRpcException("asset_boundary");
    }
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern uint GetFinalPathNameByHandleW(SafeFileHandle file,StringBuilder path,uint length,uint flags);
    public static string Resolve(string dataDirectory, CachedAsset asset)
    {
        string relative = asset.RelativePath;
        if (asset.ResourceId.Length != 64 || !asset.ResourceId.All(Uri.IsHexDigit) || !relative.StartsWith("thumbnails/", StringComparison.Ordinal) || relative.Contains(':') || relative.Contains('\\') || relative.Split('/').Any(p => p is "" or "." or "..") || relative.Length > 512) throw new EngineRpcException("asset_boundary");
        string root = Path.GetFullPath(Path.Combine(dataDirectory, "thumbnails")) + Path.DirectorySeparatorChar;
        string path = Path.GetFullPath(Path.Combine(dataDirectory, relative));
        if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new EngineRpcException("asset_boundary");
        EngineProcess.RejectReparse(path); return path;
    }
}
