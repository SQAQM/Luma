using System.Text.Json;using System.Security.Cryptography;using Luma.Core.Viewer;
internal static class ViewerAssetTests
{
 public static int Run(){int n=0;byte[] bytes=[1,2,3];string hash=Convert.ToHexString(SHA256.HashData(bytes));string identity=new('a',64);
  JsonElement Data(int version=2,int width=16)=>JsonSerializer.SerializeToElement(new{version,width,height=8,sourceWidth=16,sourceHeight=8,kind="original",resourceId=identity,relativePath="thumbnails/viewer-v2/x.png",identity,sha256=hash,bytes=3,animated=false});
  var a=PreviewAsset.Parse(Data(),identity);a.Verify(bytes);n++;
  foreach(var invalid in new[]{Data(1),Data(2,4000000)}){try{PreviewAsset.Parse(invalid,identity);throw new Exception("unsafe asset accepted");}catch(Luma.Core.Engine.EngineProtocolException){n++;}}
  try{a.Verify([2,3,4]);throw new Exception("asset digest ignored");}catch(Luma.Core.Engine.EngineRpcException){n++;}return n;
 }
}
