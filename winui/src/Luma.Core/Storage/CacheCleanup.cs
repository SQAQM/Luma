using System.Text.RegularExpressions;
using System.Text.Json;
using System.Security.Cryptography;
using Luma.Core.Engine;
namespace Luma.Core.Storage;

public sealed record CacheFile(string RelativePath,string Identity,long Bytes);
public sealed record CachePlan(string Library,List<CacheFile> Files,bool More,string OwnerIdentity)
{public long Bytes=>Files.Sum(f=>f.Bytes);}
public sealed record CacheCleanupResult(int Removed,int Skipped,long Bytes);

/// <summary>Metadata only. Never reads pixels, catalog rows, model weights or original folders.</summary>
public static class CacheCleanup
{
    private static void VerifyOwner(string library)
    {
        string marker=Path.Combine(library,".luma-engine-preview.json");EngineProcess.RejectReparse(marker);
        if(!File.Exists(marker))throw new IOException("storage_unowned_library");
        using var stream=new FileStream(marker,FileMode.Open,FileAccess.Read,FileShare.Read);
        StorageGuard.Verify(stream.SafeFileHandle,marker,true);
        if(stream.Length>256)throw new IOException("storage_unowned_library");
        try
        {
            using var value=JsonDocument.Parse(stream);
            if(value.RootElement.GetProperty("schema").GetInt32()!=1||value.RootElement.GetProperty("purpose").GetString()!="luma-stage3-preview")throw new IOException("storage_unowned_library");
        }
        catch(Exception error)when(error is JsonException or KeyNotFoundException or InvalidOperationException){throw new IOException("storage_unowned_library",error);}
    }
    private static bool Allowed(string relative)
    {
        if(relative.Contains('\\')||relative.Contains(':'))return false;
        string[] parts=relative.Split('/');
        if(parts.Length<2||parts[0]!="thumbnails"||parts.Any(p=>p is "" or "." or ".."))return false;
        return parts.Skip(1).SkipLast(1).All(p=>Regex.IsMatch(p,"^[a-f0-9]{2}$")||p is "viewer-v2" or "model-covers")
            &&Regex.IsMatch(parts[^1],"^[a-f0-9]{64}\\.(jpg|png|json|img|sha256)$");
    }
    public static CachePlan Preview(string profile)
    {
        profile=Path.GetFullPath(profile);using var root=new StorageGuard(profile);
        string library=File.Exists(Path.Combine(profile,"winui-library-v1.json"))?Management.PreviewLibrary.Select(profile,false).Directory:profile;
        VerifyOwner(library);string ownerIdentity=StorageGuard.DirectoryIdentity(library);
        if(!File.Exists(Path.Combine(library,"library.sqlite3")))throw new IOException("storage_no_library");
        string cache=Path.Combine(library,"thumbnails");var files=new List<CacheFile>();
        if(!Directory.Exists(cache))return new(library,files,false,ownerIdentity);
        var folders=new Stack<string>();folders.Push(cache);int visited=0;bool more=false;
        while(folders.Count>0)
        {
            string directory=folders.Pop();using var guard=new StorageGuard(directory);
            foreach(string path in Directory.EnumerateFileSystemEntries(directory))
            {
                if(++visited>20000||files.Count>=5000){more=true;break;}
                if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)continue;
                if(Directory.Exists(path))
                {
                    string name=Path.GetFileName(path);
                    if(Regex.IsMatch(name,"^[a-f0-9]{2}$")||name is "viewer-v2" or "model-covers")folders.Push(path);
                    continue;
                }
                string relative=Path.GetRelativePath(library,path).Replace('\\','/');
                if(!Allowed(relative))continue;
                try{files.Add(new(relative,StorageGuard.Identity(path),new FileInfo(path).Length));}catch(IOException){}
            }
            if(more)break;
        }
        return new(library,files,more,ownerIdentity);
    }
    public static CacheCleanupResult Apply(CachePlan plan)
    {
        if(plan.Files is null||plan.Files.Count>5000||plan.Files.Any(f=>f is null||f.RelativePath is null||!Allowed(f.RelativePath)||f.Bytes<0))throw new IOException("storage_cache_plan_invalid");
        using var root=new StorageGuard(plan.Library);
        VerifyOwner(plan.Library);
        if(plan.OwnerIdentity!=StorageGuard.DirectoryIdentity(plan.Library))throw new IOException("storage_source_changed");
        string owner=Path.Combine(plan.Library,"native-instance.lock");EngineProcess.RejectReparse(owner);
        using var lockFile=new FileStream(owner,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        StorageGuard.Verify(lockFile.SafeFileHandle,owner,true);
        int removed=0,skipped=0;long bytes=0;
        foreach(var file in plan.Files)
        {
            string path=Path.Combine(plan.Library,file.RelativePath);
            try
            {
                using var parent=new StorageGuard(Path.GetDirectoryName(path)!);
                StorageGuard.Identity(path,true,file.Identity);removed++;bytes+=file.Bytes;
            }
            catch(Exception error)when(error is IOException or UnauthorizedAccessException){skipped++;}
        }
        return new(removed,skipped,bytes);
    }
    public static string SaveReview(string path,CachePlan plan)
    {
        using var parent=new StorageGuard(Path.GetDirectoryName(path)!);
        byte[] bytes=JsonSerializer.SerializeToUtf8Bytes(plan);
        using var file=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None);
        StorageGuard.Verify(file.SafeFileHandle,path,true);file.Write(bytes);file.Flush(true);
        return Convert.ToHexString(SHA256.HashData(bytes));
    }
    public static CachePlan LoadReview(string path,string expected)
    {
        using var parent=new StorageGuard(Path.GetDirectoryName(path)!);
        using var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.None);
        StorageGuard.Verify(file.SafeFileHandle,path,true);
        if(file.Length>2*1024*1024)throw new IOException("storage_cache_plan_invalid");
        byte[] bytes=new byte[file.Length];file.ReadExactly(bytes);
        if(Convert.ToHexString(SHA256.HashData(bytes))!=expected)throw new IOException("storage_source_changed");
        return JsonSerializer.Deserialize<CachePlan>(bytes)??throw new IOException("storage_cache_plan_invalid");
    }
}
