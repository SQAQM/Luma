using System.Text.Json;
using Luma.Core.Engine;
namespace Luma.Core.Management;
public sealed record PendingOperation(string OperationId,string Action);
public sealed class PendingOperationStore(string directory)
{
 private string Path=>System.IO.Path.Combine(directory,"winui-pending-operation-v1.json");
 public PendingOperation? Load()
 {
  EngineProcess.RejectReparse(Path);if(!File.Exists(Path))return null;
  try{if(new FileInfo(Path).Length>512)throw new EngineRpcException("conflict");var value=JsonSerializer.Deserialize<PendingOperation>(File.ReadAllText(Path));if(value is null||!Guid.TryParseExact(value.OperationId,"N",out _)||value.Action.Length>64)throw new EngineRpcException("conflict");return value;}
  catch(Exception e) when(e is JsonException or IOException){throw new EngineRpcException("conflict");}
 }
 public void Save(string id,string action)
 {
  EngineProcess.RejectReparse(Path);string temp=Path+"."+Guid.NewGuid().ToString("N")+".tmp";
  try{using(var file=new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None)){JsonSerializer.Serialize(file,new PendingOperation(id,action));file.Flush(true);}File.Move(temp,Path,true);}
  finally{if(File.Exists(temp))File.Delete(temp);}
 }
 public void Clear(){EngineProcess.RejectReparse(Path);if(File.Exists(Path))File.Delete(Path);}
}
