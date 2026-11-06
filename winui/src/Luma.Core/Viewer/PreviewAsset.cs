using System.Text.Json;using System.Security.Cryptography;using Luma.Core.Engine;
namespace Luma.Core.Viewer;
public sealed record PreviewAsset(string ResourceId,string RelativePath,string Identity,string Checksum,long Bytes,int Width,int Height,int SourceWidth,int SourceHeight,string Kind,bool Animated)
{
 public int Frame{get;init;}public int FrameCount{get;init;}=1;public int DurationMs{get;init;}public int RepeatLimit{get;init;}=1;
 public static PreviewAsset Parse(JsonElement e,string expected,int expectedFrame=0){
  string Text(string k)=>e.GetProperty(k).GetString()??"";
  var a=new PreviewAsset(Text("resourceId"),Text("relativePath"),Text("identity"),Text("sha256"),e.GetProperty("bytes").GetInt64(),e.GetProperty("width").GetInt32(),e.GetProperty("height").GetInt32(),e.GetProperty("sourceWidth").GetInt32(),e.GetProperty("sourceHeight").GetInt32(),Text("kind"),e.GetProperty("animated").GetBoolean());
  int Number(string key,int fallback)=>e.TryGetProperty(key,out var v)?v.ValueKind==JsonValueKind.Number&&v.TryGetInt32(out int n)?n:throw new EngineProtocolException():fallback;
  a=a with{Frame=Number("frame",0),FrameCount=Number("frameCount",1),DurationMs=Number("durationMs",0),RepeatLimit=Number("repeatLimit",1)};
  if(e.GetProperty("version").GetInt32()!=2||a.Identity!=expected||a.ResourceId.Length!=64||!a.ResourceId.All(Uri.IsHexDigit)||a.Checksum.Length!=64||!a.Checksum.All(Uri.IsHexDigit)||a.Bytes is <1 or >32*1024*1024||a.Width is <1 or >16384||a.Height is <1 or >16384||(long)a.Width*a.Height>24_000_000||a.SourceWidth<1||a.SourceHeight<1||(long)a.SourceWidth*a.SourceHeight>50_000_000||a.Kind is not ("original" or "standard" or "compatibility")||a.Frame!=expectedFrame||a.Frame<0||a.Frame>=a.FrameCount||a.FrameCount is <1 or >4096||a.DurationMs is <0 or >60000||a.RepeatLimit is <0 or >65536)throw new EngineProtocolException();return a;
 }
 public void Verify(byte[] bytes){if(bytes.LongLength!=Bytes||!CryptographicOperations.FixedTimeEquals(SHA256.HashData(bytes),Convert.FromHexString(Checksum)))throw new EngineRpcException("asset_changed");}
}
