using System.Text.Json;
using System.Text.Json.Serialization;
using Luma.Core.Design;
namespace Luma.Core.Management;
public sealed record WindowAppearance(string Background="Material",string? TextColor=null,
 string? BackgroundColor=null,string? ImagePath=null)
{
 public static WindowAppearance Normalize(WindowAppearance? value)
 {
  if(value is null)return new();
  string? path=value.ImagePath is {Length: >0 and <=8192} image&&
   !image.Any(char.IsControl)&&System.IO.Path.IsPathFullyQualified(image)?image:null;
  string? color=NormalizeColor(value.BackgroundColor);
  string source=value.Background switch
  {
   "Color"=>"Color",
   "Image" when path is not null=>"Image",
   _=>"Material"
  };
  return new(source,NormalizeColor(value.TextColor),color,path);
 }

 private static string? NormalizeColor(string? value)=>
  value is {Length:7}&&value[0]=='#'&&value[1..].All(Uri.IsHexDigit)?value.ToUpperInvariant():null;
}

public sealed record WindowPreferences(int Width=1000,int Height=650,bool Maximized=false,string Theme="System",bool ReduceMotion=false,
 bool UseMaterials=true,string? Material=null,int BackgroundTransparency=MaterialPolicy.DefaultTransparency,WindowAppearance? Appearance=null)
{
 [JsonIgnore]
 public WindowAppearance CustomAppearance=>WindowAppearance.Normalize(Appearance);
 [JsonIgnore]
 public BackgroundMaterial SelectedMaterial=>Material switch
 {
  nameof(BackgroundMaterial.Acrylic)=>BackgroundMaterial.Acrylic,
  nameof(BackgroundMaterial.Frosted)=>BackgroundMaterial.Frosted,
  nameof(BackgroundMaterial.Liquid)=>BackgroundMaterial.Liquid,
  nameof(BackgroundMaterial.Solid)=>BackgroundMaterial.Solid,
  _=>UseMaterials?BackgroundMaterial.Acrylic:BackgroundMaterial.Solid
 };
}
public sealed class UiPreferences(string directory)
{
 public string Path=>System.IO.Path.Combine(directory,"winui-window-v1.json");
 public WindowPreferences Load()
 {
  try{Luma.Core.Engine.EngineProcess.RejectReparse(Path);if(!File.Exists(Path)||new FileInfo(Path).Length>64*1024)return new();var value=JsonSerializer.Deserialize<WindowPreferences>(File.ReadAllText(Path));return value is{} v&&v.Width>=760&&v.Width<=7680&&v.Height>=500&&v.Height<=4320&&v.Theme is "System" or "Light" or "Dark"?v with{BackgroundTransparency=MaterialPolicy.NormalizeTransparency(v.BackgroundTransparency),Appearance=v.CustomAppearance}:new();}
  catch(Exception e) when(e is IOException or JsonException or UnauthorizedAccessException or Luma.Core.Engine.EngineRpcException){return new();}
 }
 public void Save(WindowPreferences value)
 {
  Luma.Core.Engine.EngineProcess.RejectReparse(Path);string temp=Path+"."+Guid.NewGuid().ToString("N")+".tmp";
  try{File.WriteAllText(temp,JsonSerializer.Serialize(value));File.Move(temp,Path,true);}
  finally{if(File.Exists(temp))File.Delete(temp);}
 }
}
