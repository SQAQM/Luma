using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
using Luma.Core.Engine;

namespace Luma.Core.Storage;

// Keep source ancestors pinned while inspecting/copying application data.
// A directory cannot become a junction or be renamed underneath held handles.
internal sealed class StorageGuard:IDisposable
{
    private readonly List<SafeFileHandle> _directories=[];
    public StorageGuard(string directory)
    {
        if(!Path.IsPathFullyQualified(directory)||directory.StartsWith(@"\\",StringComparison.Ordinal))throw new IOException("storage_local_path_required");
        EngineProcess.RejectReparse(directory);
        if(!OperatingSystem.IsWindows())throw new IOException("storage_platform_unsupported");
        try
        {
            var parents=new Stack<string>();
            for(string? item=Path.GetFullPath(directory);item is not null;item=Path.GetDirectoryName(item))parents.Push(item);
            foreach(string item in parents)
            {
                var handle=CreateFileW(@"\\?\"+Path.GetFullPath(item),0x80,3,0,3,0x02200000,0);
                if(handle.IsInvalid){int error=Marshal.GetLastWin32Error();handle.Dispose();throw new IOException("storage_directory_busy",new System.ComponentModel.Win32Exception(error));}
                _directories.Add(handle);Verify(handle,item,false);
            }
        }
        catch{Dispose();throw;}
    }

    internal static void Verify(SafeFileHandle handle,string expected,bool file)
    {
        if(!GetFileInformationByHandle(handle,out var info)||(info.Attributes&0x400)!=0||(file&&info.Links!=1))
            throw new IOException("storage_unsafe_identity");
        var path=new StringBuilder(32768);
        uint length=GetFinalPathNameByHandleW(handle,path,(uint)path.Capacity,0);
        if(length==0||length>=path.Capacity)throw new IOException("storage_unsafe_identity");
        string actual=path.ToString();if(actual.StartsWith(@"\\?\"))actual=actual[4..];
        if(!Path.TrimEndingDirectorySeparator(actual).Equals(Path.TrimEndingDirectorySeparator(Path.GetFullPath(expected)),StringComparison.OrdinalIgnoreCase))
            throw new IOException("storage_unsafe_identity");
    }

    public void Dispose(){foreach(var handle in _directories.AsEnumerable().Reverse())handle.Dispose();_directories.Clear();}
    internal static string DirectoryIdentity(string path)
    {
        using var handle=CreateFileW(path,0x80,3,0,3,0x02200000,0);
        if(handle.IsInvalid)throw new IOException("storage_directory_busy");
        Verify(handle,path,false);
        if(!GetFileInformationByHandle(handle,out var info)||(info.Attributes&0x10)==0)throw new IOException("storage_unsafe_identity");
        return $"{info.Volume:X8}:{info.IndexHigh:X8}{info.IndexLow:X8}";
    }
    internal static string Identity(string path,bool remove=false,string? expected=null)
    {
        using var handle=CreateFileW(path,remove?0x10080u:0x80u,0,0,3,0x00200000,0);
        if(handle.IsInvalid)throw new IOException("storage_file_busy");
        Verify(handle,path,true);
        if(!GetFileInformationByHandle(handle,out var info)||(info.Attributes&0x10)!=0)throw new IOException("storage_unsafe_identity");
        string identity=$"{info.Volume:X8}:{info.IndexHigh:X8}{info.IndexLow:X8}:{info.SizeHigh:X8}{info.SizeLow:X8}:{info.Written.dwHighDateTime:X8}{info.Written.dwLowDateTime:X8}";
        if(remove)
        {
            if(identity!=expected)throw new IOException("storage_source_changed");
            int delete=1;if(!SetFileInformationByHandle(handle,4,ref delete,4))throw new IOException("storage_delete_failed");
        }
        return identity;
    }
    [StructLayout(LayoutKind.Sequential)]private struct FileInfoData
    {public uint Attributes;public System.Runtime.InteropServices.ComTypes.FILETIME Created,Accessed,Written;public uint Volume,SizeHigh,SizeLow,Links,IndexHigh,IndexLow;}
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern SafeFileHandle CreateFileW(string path,uint access,uint share,nint security,uint creation,uint flags,nint template);
    [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetFileInformationByHandle(SafeFileHandle file,out FileInfoData info);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern uint GetFinalPathNameByHandleW(SafeFileHandle file,StringBuilder path,uint capacity,uint flags);
    [DllImport("kernel32.dll",SetLastError=true)]private static extern bool SetFileInformationByHandle(SafeFileHandle file,int kind,ref int value,uint bytes);
}
