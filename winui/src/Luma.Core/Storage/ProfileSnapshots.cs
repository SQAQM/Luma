using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using Luma.Core.Engine;

namespace Luma.Core.Storage;

public sealed record SnapshotFile(string RelativePath,long Bytes,string Sha256);
public sealed record SnapshotPlan(string Source,List<SnapshotFile> Files)
{public long Bytes=>Files.Sum(file=>file.Bytes);}
internal sealed record SnapshotManifest(int Schema,List<SnapshotFile> Files);

/// <summary>Offline, opaque profile copies. Never opens original images or interprets catalog rows.</summary>
public static class ProfileSnapshots
{
    private const int MaxFiles=4096;
    private static readonly HashSet<string> RootFiles=[".luma-winui-preview.json","winui-library-v1.json","winui-window-v1.json"];
    private static readonly HashSet<string> LibraryFiles=[".luma-engine-preview.json","winui-pending-operation-v1.json","library.sqlite3","management-operations.sqlite3","file-reviews.sqlite3"];

    private static bool Allowed(string relative)
    {
        if(relative.Contains('\\')||relative.Contains(':')||relative.Split('/').Any(p=>p is "" or "." or ".."))return false;
        if(RootFiles.Contains(relative))return true;
        string[] parts=relative.Split('/');
        if(parts.Length<2||!Regex.IsMatch(parts[0],"^engine-probe-[a-f0-9]{32}$"))return false;
        if(parts.Length==3&&parts[1]=="credentials"&&parts[2]=="civitai.dpapi")return true;
        if(parts.Length==3&&parts[1]=="model-covers"&&Regex.IsMatch(parts[2],"^[a-f0-9]{64}\\.cover$"))return true;
        if(parts.Length!=2)return false;
        string name=parts[1];
        if(LibraryFiles.Contains(name))return true;
        return new[]{"-wal","-shm","-journal"}.Any(suffix=>name.EndsWith(suffix)&&LibraryFiles.Contains(name[..^suffix.Length])&&name[..^suffix.Length].EndsWith(".sqlite3"));
    }

    private static string[] Paths(string source)
    {
        string profileMarker=Path.Combine(source,".luma-winui-preview.json");
        using(var file=Open(profileMarker))
        {
            if(file.Length>256)throw new IOException("storage_profile_invalid");
            using var marker=JsonDocument.Parse(file);
            if(marker.RootElement.GetProperty("purpose").GetString()!="luma-winui-stage8-preview")throw new IOException("storage_profile_invalid");
        }
        string pointer=Path.Combine(source,"winui-library-v1.json");
        if(!File.Exists(pointer))throw new IOException("storage_no_library");
        var library=Management.PreviewLibrary.Select(source,false);
        var paths=RootFiles.Select(name=>Path.Combine(source,name)).Where(File.Exists).ToList();
        foreach(string name in LibraryFiles)
        {
            string path=Path.Combine(library.Directory,name);
            if(File.Exists(path))paths.Add(path);
            if(name.EndsWith(".sqlite3"))foreach(string suffix in new[]{"-wal","-shm","-journal"})if(File.Exists(path+suffix))paths.Add(path+suffix);
        }
        string credentials=Path.Combine(library.Directory,"credentials","civitai.dpapi");
        if(File.Exists(credentials))paths.Add(credentials);
        string covers=Path.Combine(library.Directory,"model-covers");
        if(Directory.Exists(covers))
        {
            EngineProcess.RejectReparse(covers);
            foreach(string path in Directory.EnumerateFiles(covers))
            {
                if(Allowed(Path.GetRelativePath(source,path).Replace('\\','/')))paths.Add(path);
                if(paths.Count>MaxFiles)throw new IOException("storage_file_budget");
            }
        }
        return paths.Order(StringComparer.Ordinal).ToArray();
    }

    private static FileStream Open(string path)
    {
        EngineProcess.RejectReparse(path);
        var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.None,1,FileOptions.SequentialScan);
        try{StorageGuard.Verify(stream.SafeFileHandle,path,true);return stream;}catch{stream.Dispose();throw;}
    }
    private static void PinDirectoryTree(string root,string directory,List<StorageGuard> guards)
    {
        string relative=Path.GetRelativePath(root,directory);
        if(relative==".")return;
        string current=root;
        foreach(string part in relative.Split(Path.DirectorySeparatorChar))
        {
            if(part is ".." or "")throw new IOException("storage_destination_conflict");
            current=Path.Combine(current,part);EngineProcess.RejectReparse(current);
            Directory.CreateDirectory(current);guards.Add(new(current));
        }
    }
    private static string Hash(Stream stream,CancellationToken token)
    {
        using var hash=IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        byte[] buffer=new byte[1024*1024];int count;
        while((count=stream.Read(buffer))!=0){token.ThrowIfCancellationRequested();hash.AppendData(buffer,0,count);}
        stream.Position=0;return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    public static SnapshotPlan Preview(string source,CancellationToken token=default)
    {
        source=Path.GetFullPath(source);using var guard=new StorageGuard(source);
        string[] paths=Paths(source);
        string library=Path.GetDirectoryName(paths.Single(p=>Path.GetFileName(p)==".luma-engine-preview.json"))!;
        using var owner=AcquireOwner(library);
        var files=new List<SnapshotFile>();
        foreach(string path in Paths(source))
        {
            token.ThrowIfCancellationRequested();using var parent=new StorageGuard(Path.GetDirectoryName(path)!);using var file=Open(path);
            files.Add(new(Path.GetRelativePath(source,path).Replace('\\','/'),file.Length,Hash(file,token)));
        }
        if(!files.Any(f=>f.RelativePath.EndsWith("/library.sqlite3")))throw new IOException("storage_no_library");
        return new(source,files);
    }

    private static FileStream AcquireOwner(string library)
    {
        string path=Path.Combine(library,"native-instance.lock");EngineProcess.RejectReparse(path);
        var stream=new FileStream(path,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        try{StorageGuard.Verify(stream.SafeFileHandle,path,true);return stream;}catch{stream.Dispose();throw;}
    }

    public static string Create(SnapshotPlan plan,string destination,CancellationToken token=default)
    {
        token.ThrowIfCancellationRequested();string source=Path.GetFullPath(plan.Source),target=Path.GetFullPath(destination);
        if(target.Equals(source,StringComparison.OrdinalIgnoreCase)||target.StartsWith(source+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)||source.StartsWith(target+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)||Directory.Exists(target)||File.Exists(target))
            throw new IOException("storage_destination_conflict");
        ValidateFiles(plan.Files);EngineProcess.RejectReparse(target);
        string volume=Path.GetPathRoot(target)!;
        if(new DriveInfo(volume).AvailableFreeSpace<plan.Bytes+64L*1024*1024)throw new IOException("storage_space_low");
        using var root=new StorageGuard(source);
        string[] markers=plan.Files.Where(f=>f.RelativePath.EndsWith("/.luma-engine-preview.json")).Select(f=>f.RelativePath.Split('/')[0]).ToArray();
        if(markers.Length!=1)throw new IOException("storage_profile_invalid");
        using var owner=AcquireOwner(Path.Combine(source,markers[0]));
        var current=Paths(source).Select(p=>Path.GetRelativePath(source,p).Replace('\\','/')).Order(StringComparer.Ordinal);
        if(!current.SequenceEqual(plan.Files.Select(f=>f.RelativePath).Order(StringComparer.Ordinal)))throw new IOException("storage_source_changed");
        var streams=new List<FileStream>();var guards=new List<StorageGuard>();
        try
        {
            foreach(var file in plan.Files)
            {
                string path=Path.Combine(source,file.RelativePath);
                guards.Add(new(Path.GetDirectoryName(path)!));var stream=Open(path);streams.Add(stream);
                if(stream.Length!=file.Bytes||Hash(stream,token)!=file.Sha256)throw new IOException("storage_source_changed");
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            using var parent=new StorageGuard(Path.GetDirectoryName(target)!);
            string pending=target+".pending-"+Guid.NewGuid().ToString("N");Directory.CreateDirectory(pending);
            byte[] copyBuffer=new byte[1024*1024];
            using(var pendingGuard=new StorageGuard(pending))
            {
                var outputGuards=new List<StorageGuard>();
                try
                {
                    for(int i=0;i<plan.Files.Count;i++)
                    {
                        token.ThrowIfCancellationRequested();var file=plan.Files[i];string output=Path.Combine(pending,file.RelativePath);
                        PinDirectoryTree(pending,Path.GetDirectoryName(output)!,outputGuards);
                        using var destinationFile=new FileStream(output,FileMode.CreateNew,FileAccess.Write,FileShare.None);
                        StorageGuard.Verify(destinationFile.SafeFileHandle,output,true);
                        int count;
                        while((count=streams[i].Read(copyBuffer))!=0){token.ThrowIfCancellationRequested();destinationFile.Write(copyBuffer,0,count);}
                        destinationFile.Flush(true);
                    }
                    using(var manifest=new FileStream(Path.Combine(pending,"snapshot.json"),FileMode.CreateNew,FileAccess.Write,FileShare.None))
                    {StorageGuard.Verify(manifest.SafeFileHandle,Path.Combine(pending,"snapshot.json"),true);JsonSerializer.Serialize(manifest,new SnapshotManifest(1,plan.Files));manifest.Flush(true);}
                    _=Verify(pending,token);
                }
                finally{foreach(var guard in outputGuards.AsEnumerable().Reverse())guard.Dispose();}
            }
            token.ThrowIfCancellationRequested();Directory.Move(pending,target);return target;
        }
        finally{foreach(var stream in streams)stream.Dispose();foreach(var guard in guards)guard.Dispose();}
    }

    private static void ValidateFiles(List<SnapshotFile> files)
    {
        if(files is null||files.Count is <1 or >MaxFiles||files.Any(f=>f is null||f.RelativePath is null||f.Sha256 is null)||files.Select(f=>f.RelativePath).Distinct(StringComparer.OrdinalIgnoreCase).Count()!=files.Count||files.Any(f=>!Allowed(f.RelativePath)||f.Bytes<0||f.Sha256.Length!=64||!f.Sha256.All(Uri.IsHexDigit)))throw new IOException("storage_manifest_invalid");
        string[] libraries=files.Where(f=>f.RelativePath.Contains('/')).Select(f=>f.RelativePath.Split('/')[0]).Distinct().ToArray();
        if(libraries.Length!=1||!new[]{".luma-winui-preview.json","winui-library-v1.json",libraries[0]+"/.luma-engine-preview.json",libraries[0]+"/library.sqlite3"}.All(required=>files.Any(f=>f.RelativePath==required)))throw new IOException("storage_manifest_invalid");
        foreach(var file in files)foreach(string suffix in new[]{"-wal","-shm","-journal"})
            if(file.RelativePath.EndsWith(suffix)&&!files.Any(f=>f.RelativePath==file.RelativePath[..^suffix.Length]))throw new IOException("storage_manifest_invalid");
    }

    public static SnapshotPlan Verify(string snapshot,CancellationToken token=default)
    {
        snapshot=Path.GetFullPath(snapshot);using var guard=new StorageGuard(snapshot);
        SnapshotManifest manifest;
        try
        {
            using var file=Open(Path.Combine(snapshot,"snapshot.json"));if(file.Length>2*1024*1024)throw new IOException("storage_manifest_invalid");
            manifest=JsonSerializer.Deserialize<SnapshotManifest>(file)??throw new IOException("storage_manifest_invalid");
        }
        catch(JsonException error){throw new IOException("storage_manifest_invalid",error);}
        if(manifest.Schema!=1)throw new IOException("storage_manifest_version");ValidateFiles(manifest.Files);
        foreach(var item in manifest.Files)
        {
            string path=Path.Combine(snapshot,item.RelativePath);using var parent=new StorageGuard(Path.GetDirectoryName(path)!);using var file=Open(path);
            if(file.Length!=item.Bytes||Hash(file,token)!=item.Sha256)throw new IOException("storage_snapshot_changed");
        }
        return new(snapshot,manifest.Files);
    }

    public static string Restore(string snapshot,string dataDirectory,CancellationToken token=default)
    {
        var plan=Verify(snapshot,token);
        string profile=Path.Combine(Path.GetFullPath(dataDirectory),"Profiles","profile-"+Guid.NewGuid().ToString("N"));
        Create(plan,profile,token);token.ThrowIfCancellationRequested();
        using var guard=new StorageGuard(profile);_=Verify(profile,token);
        StorageLayout.Activate(dataDirectory,profile);return profile;
    }

    public static string Import(SnapshotPlan plan,string dataDirectory,CancellationToken token=default)
    {
        string profile=Path.Combine(Path.GetFullPath(dataDirectory),"Profiles","profile-"+Guid.NewGuid().ToString("N"));
        Create(plan,profile,token);token.ThrowIfCancellationRequested();using var guard=new StorageGuard(profile);_=Verify(profile,token);
        StorageLayout.Activate(dataDirectory,profile);return profile;
    }
}
