using Luma.Core.Viewer;

internal static class SlideshowTests
{
 public static int Run()
 {
  int count=0;void Check(bool value,string name){if(!value)throw new Exception("SLIDESHOW_FAIL "+name);count++;}
  var show=new SlideshowClock(TimeSpan.FromSeconds(5));
  Check(!show.Playing&&!show.Observe("one",true,true,TimeSpan.Zero),"initially stopped");
  show.Start();
  Check(!show.Observe("one",false,true,TimeSpan.FromSeconds(20)),"loading is not counted");
  Check(!show.Observe("one",true,true,TimeSpan.FromSeconds(30)),"first ready frame starts countdown");
  Check(!show.Observe("one",true,true,TimeSpan.FromSeconds(34.9)),"whole interval visible");
  Check(show.Observe("one",true,true,TimeSpan.FromSeconds(35)),"advance after visible interval");
  Check(!show.Observe("one",true,true,TimeSpan.FromSeconds(100)),"only one request per image");
  Check(!show.Observe("two",false,true,TimeSpan.FromSeconds(110)),"next loading may be slow");
  Check(!show.Observe("two",true,true,TimeSpan.FromSeconds(120)),"new image gets full interval");
  Check(show.Observe("two",true,true,TimeSpan.FromSeconds(125)),"second image advances");
  Check(!show.Observe("last",true,false,TimeSpan.FromSeconds(130))&&!show.Playing,"last image stops playback");
  show.Start();show.Observe("one",true,true,TimeSpan.Zero);show.Stop();
  Check(!show.Observe("one",true,true,TimeSpan.FromSeconds(30)),"manual pause prevents navigation");
  show.Start();Check(!show.Observe("one",true,true,TimeSpan.FromSeconds(40)),"resume starts fresh interval");
  Check(!show.Observe("one",false,true,TimeSpan.FromSeconds(43)),"loading cancels previous deadline");
  Check(!show.Observe("one",true,true,TimeSpan.FromSeconds(50)),"ready after loading restarts deadline");
  Check(show.Observe("one",true,true,TimeSpan.FromSeconds(55)),"fresh deadline honored");
  show.Stop();show.Start();Check(!show.Observe(null,false,true,TimeSpan.Zero),"empty frame never advances");
  return count;
 }
}
