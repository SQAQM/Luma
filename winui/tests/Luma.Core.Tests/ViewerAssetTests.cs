using System.Text.Json;using System.Security.Cryptography;using Luma.Core.Viewer;
internal static class ViewerAssetTests
{
 public static int Run(){int n=0;byte[] bytes=[1,2,3];string hash=Convert.ToHexString(SHA256.HashData(bytes));string identity=new('a',64);
  JsonElement Data(int version=2,int width=16)=>JsonSerializer.SerializeToElement(new{version,width,height=8,sourceWidth=16,sourceHeight=8,kind="original",resourceId=identity,relativePath="thumbnails/viewer-v2/x.png",identity,sha256=hash,bytes=3,animated=false});
  var a=PreviewAsset.Parse(Data(),identity);a.Verify(bytes);n++;
  foreach(var invalid in new[]{Data(1),Data(2,4000000)}){try{PreviewAsset.Parse(invalid,identity);throw new Exception("unsafe asset accepted");}catch(Luma.Core.Engine.EngineProtocolException){n++;}}
  try{a.Verify([2,3,4]);throw new Exception("asset digest ignored");}catch(Luma.Core.Engine.EngineRpcException){n++;}
  var fields=JsonSerializer.Deserialize<Dictionary<string,object>>(Data().GetRawText())!;
  fields["frame"]=1;fields["frameCount"]=3;fields["durationMs"]=140;fields["repeatLimit"]=2;
  var multi=JsonSerializer.SerializeToElement(fields);var parsed=PreviewAsset.Parse(multi,identity,1);
  if(parsed.FrameCount!=3||parsed.DurationMs!=140||parsed.RepeatLimit!=2)throw new Exception("sequence fields lost");n++;
  try{PreviewAsset.Parse(multi,identity,0);throw new Exception("stale frame accepted");}catch(Luma.Core.Engine.EngineProtocolException){n++;}
  foreach(var pair in new (string,object)[]{("frameCount",0),("frameCount",4097),("durationMs",-1),("durationMs",60001),("repeatLimit",-1),("frame",true)}){
   var invalid=new Dictionary<string,object>(fields){[pair.Item1]=pair.Item2};
   try{PreviewAsset.Parse(JsonSerializer.SerializeToElement(invalid),identity,1);throw new Exception("invalid sequence field accepted");}catch(Luma.Core.Engine.EngineProtocolException){n++;}
  }return n;
 }
}
