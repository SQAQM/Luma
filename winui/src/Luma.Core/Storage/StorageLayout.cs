using System.Text.Json;
using System.Text.RegularExpressions;
using Luma.Core.Engine;

namespace Luma.Core.Storage;

public static class StorageLayout
{
    public static string ResolveProfile(string applicationDirectory)
    {
        string data=Path.Combine(Path.GetFullPath(applicationDirectory),"Data");
        return ResolveDataProfile(data,Path.Combine(data,"Profiles","default"));
    }

    public static string ResolveDataProfile(string data,string defaultProfile)
    {
        data=Path.GetFullPath(data);
        EngineProcess.RejectReparse(data);
        string pointer=Path.Combine(data,"active-profile.json");
        EngineProcess.RejectReparse(pointer);
        if(!File.Exists(pointer))return defaultProfile;
        try
        {
            if(new FileInfo(pointer).Length>512)throw new IOException("storage_pointer_invalid");
            using var document=JsonDocument.Parse(File.ReadAllText(pointer));
            string name=document.RootElement.GetProperty("directory").GetString()??"";
            if(document.RootElement.GetProperty("schema").GetInt32()!=1||!Regex.IsMatch(name,"^profile-[a-f0-9]{32}$"))
                throw new IOException("storage_pointer_invalid");
            string selected=Path.Combine(data,"Profiles",name);
            EngineProcess.RejectReparse(selected);
            if(!Directory.Exists(selected))throw new IOException("storage_profile_missing");
            return selected;
        }
        catch(Exception error)when(error is JsonException or KeyNotFoundException or InvalidOperationException)
        {throw new IOException("storage_pointer_invalid",error);}
    }

    public static void Activate(string dataDirectory,string profile)
    {
        string data=Path.GetFullPath(dataDirectory),target=Path.GetFullPath(profile);
        string name=Path.GetFileName(target);
        if(Path.GetDirectoryName(target)!=Path.Combine(data,"Profiles")||!Regex.IsMatch(name,"^profile-[a-f0-9]{32}$"))
            throw new IOException("storage_profile_boundary");
        using var root=new StorageGuard(data);
        using var selected=new StorageGuard(target);
        Management.PreviewProfile.Open(target,false);
        _=Management.PreviewLibrary.Select(target,false);
        string pointer=Path.Combine(data,"active-profile.json");
        EngineProcess.RejectReparse(pointer);
        string temporary=pointer+"."+Guid.NewGuid().ToString("N")+".tmp";
        using(var file=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None))
        {StorageGuard.Verify(file.SafeFileHandle,temporary,true);JsonSerializer.Serialize(file,new{schema=1,directory=name});file.Flush(true);}
        File.Move(temporary,pointer,true);
    }
}
