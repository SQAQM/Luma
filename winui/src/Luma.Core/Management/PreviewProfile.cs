using System.Text.Json;
using Luma.Core.Engine;
namespace Luma.Core.Management;
public static class PreviewProfile
{
 public static void Open(string directory,bool fresh)
 {
  EngineProcess.RejectReparse(directory);
  if(Directory.Exists(directory))
  {
   if(fresh)throw new EngineRpcException("unsafe_data_directory");
   if(Directory.EnumerateFileSystemEntries(directory).Any())
   {
    string marker=Path.Combine(directory,".luma-winui-preview.json");EngineProcess.RejectReparse(marker);
    try{if(!File.Exists(marker)||new FileInfo(marker).Length>256)throw new EngineRpcException("unsafe_data_directory");using var doc=JsonDocument.Parse(File.ReadAllText(marker));if(doc.RootElement.GetProperty("purpose").GetString()!="luma-winui-stage8-preview")throw new EngineRpcException("unsafe_data_directory");return;}
    catch(Exception e)when(e is IOException or JsonException or KeyNotFoundException or InvalidOperationException){throw new EngineRpcException("unsafe_data_directory");}
   }
  }
  Directory.CreateDirectory(directory);EngineProcess.RejectReparse(directory);
  using var stream=new FileStream(Path.Combine(directory,".luma-winui-preview.json"),FileMode.CreateNew,FileAccess.Write,FileShare.None);
  JsonSerializer.Serialize(stream,new{purpose="luma-winui-stage8-preview"});stream.Flush(true);
 }
}
