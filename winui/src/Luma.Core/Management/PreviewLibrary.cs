using System.Text.Json;
using System.Text.RegularExpressions;
using Luma.Core.Engine;
namespace Luma.Core.Management;
public sealed record PreviewLibrarySelection(string Directory,bool Created);
public static class PreviewLibrary
{
 public static PreviewLibrarySelection Select(string parent,bool fresh)
 {
  EngineProcess.RejectReparse(parent);
  string pointer=Path.Combine(parent,"winui-library-v1.json");EngineProcess.RejectReparse(pointer);
  if(!fresh&&File.Exists(pointer))
  {
   try{
    if(new FileInfo(pointer).Length>256)throw new EngineRpcException("catalog_unavailable");
    using var doc=JsonDocument.Parse(File.ReadAllText(pointer));string name=doc.RootElement.GetProperty("directory").GetString()??"";
    if(!Regex.IsMatch(name,"^engine-probe-[a-f0-9]{32}$"))throw new EngineRpcException("catalog_unavailable");
    string path=Path.Combine(parent,name);string marker=Path.Combine(path,".luma-engine-preview.json");EngineProcess.RejectReparse(marker);
    if(!File.Exists(marker)||new FileInfo(marker).Length>256)throw new EngineRpcException("catalog_unavailable");
    using var identity=JsonDocument.Parse(File.ReadAllText(marker));if(identity.RootElement.GetProperty("purpose").GetString()!="luma-stage3-preview")throw new EngineRpcException("catalog_unavailable");
    return new(path,false);
   }catch(Exception e) when(e is IOException or JsonException or KeyNotFoundException or InvalidOperationException){throw new EngineRpcException("catalog_unavailable");}
  }
  string created=PreviewDirectory.Create(parent);
  if(!fresh){string temp=pointer+"."+Guid.NewGuid().ToString("N")+".tmp";try{File.WriteAllText(temp,JsonSerializer.Serialize(new{directory=Path.GetFileName(created)}));File.Move(temp,pointer,false);}finally{if(File.Exists(temp))File.Delete(temp);}}
  return new(created,true);
 }
}
