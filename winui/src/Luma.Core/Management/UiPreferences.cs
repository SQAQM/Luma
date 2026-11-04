using System.Text.Json;
namespace Luma.Core.Management;
public sealed record WindowPreferences(int Width=1000,int Height=650,bool Maximized=false,string Theme="System",bool ReduceMotion=false,bool UseMaterials=true);
public sealed class UiPreferences(string directory)
{
 public string Path=>System.IO.Path.Combine(directory,"winui-window-v1.json");
 public WindowPreferences Load()
 {
  try{Luma.Core.Engine.EngineProcess.RejectReparse(Path);if(!File.Exists(Path)||new FileInfo(Path).Length>4096)return new();var value=JsonSerializer.Deserialize<WindowPreferences>(File.ReadAllText(Path));return value is{} v&&v.Width>=760&&v.Width<=7680&&v.Height>=500&&v.Height<=4320&&v.Theme is "System" or "Light" or "Dark"?v:new();}
  catch(Exception e) when(e is IOException or JsonException or UnauthorizedAccessException or Luma.Core.Engine.EngineRpcException){return new();}
 }
 public void Save(WindowPreferences value)
 {
  Luma.Core.Engine.EngineProcess.RejectReparse(Path);string temp=Path+"."+Guid.NewGuid().ToString("N")+".tmp";
  try{File.WriteAllText(temp,JsonSerializer.Serialize(value));File.Move(temp,Path,true);}
  finally{if(File.Exists(temp))File.Delete(temp);}
 }
}
