using Luma.Core.Viewer;
using Luma.Core.Gallery;
internal static class ViewerTests
{
 public static int Run(){int n=0;void Check(bool v,string name){if(!v)throw new Exception("VIEWER_FAIL "+name);n++;}
  var view=new Viewport();view.SetImage(1000,500);view.Resize(500,400);view.Fit();Check(Math.Abs(view.Scale-.5)<.001,"aspect fit");
  view.ZoomAt(2,350,200);Check(Math.Abs(view.X+100)<.001&&view.Y==0,"cursor anchor stays fixed");
  view.Pan(10000,10000);Check(view.X==250&&view.Y==50,"pan bounded to image");
  view.Rotate();view.Fit();Check(Math.Abs(view.Scale-.4)<.001&&view.Turns==1,"quarter turn fit");
  view.Actual(2);Check(view.Scale==.5&&!view.IsFit,"100 percent uses physical preview pixels");
  view.ZoomAt(double.NaN,0,0);Check(double.IsFinite(view.Scale),"invalid gesture ignored");
  view.Fit();view.Resize(200,100);Check(Math.Abs(view.Scale-.1)<.001,"fit follows viewport resize");
  var cache=new ByteLru<string,byte[]>(3,10);cache.Put("a",new byte[4],4);cache.Put("b",new byte[4],4);Check(cache.TryGet("a",out _),"cache hit");cache.Put("c",new byte[4],4);Check(!cache.TryGet("b",out _)&&cache.Bytes==8,"LRU byte eviction");
  Check(!cache.Put("oversized",new byte[11],11)&&cache.Count==2,"oversized allocation rejected");cache.Clear();Check(cache.Bytes==0&&cache.Count==0,"close releases cache");
  string text=string.Concat(Enumerable.Repeat("雪😀 landscape, ",3000));var chunks=TextChunks.Split(text);Check(string.Concat(chunks)==text&&chunks.All(c=>c.Length<=1024),"full prompt chunks preserve every character");Check(chunks.All(c=>!char.IsLowSurrogate(c[0])&&!char.IsHighSurrogate(c[^1])),"virtual text never splits UTF16 pairs");return n;
 }
}
