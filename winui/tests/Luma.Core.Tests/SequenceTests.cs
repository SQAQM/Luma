using Luma.Core.Viewer;
internal static class SequenceTests
{
 public static int Run(){int n=0;void Check(bool value,string name){if(!value)throw new Exception("SEQUENCE_FAIL "+name);n++;}
  var s=new SequencePlayback();var zero=TimeSpan.Zero;
  s.Accept("a",0,3,true,1,80,zero,false);Check(s.Playing&&s.Index==0,"auto animation");
  Check(s.Due(TimeSpan.FromMilliseconds(79)) is null,"frame duration");
  Check(s.Due(TimeSpan.FromMilliseconds(80))==1&&s.Waiting,"one next request");
  Check(s.Due(TimeSpan.FromSeconds(5)) is null,"slow frame never queues");
  s.Accept("a",1,3,true,1,140,TimeSpan.FromSeconds(5),false);
  Check(s.Due(TimeSpan.FromMilliseconds(5139)) is null,"full visible duration after slow decode");
  Check(s.Due(TimeSpan.FromMilliseconds(5140))==2,"advance second");
  s.Accept("a",2,3,true,1,200,TimeSpan.FromSeconds(6),false);
  Check(s.Due(TimeSpan.FromSeconds(7)) is null&&!s.Playing&&s.Completed,"finite loop ends on last frame");
  Check(s.Start(TimeSpan.FromSeconds(8))==0&&s.Playing,"replay restarts first frame");
  s.Accept("a",0,3,true,1,80,TimeSpan.FromSeconds(9),false);s.Pause();
  Check(s.Due(TimeSpan.FromSeconds(99)) is null,"paused");
  s.Accept("b",0,3,true,0,80,zero,true);Check(!s.Playing,"reduced motion stops autoplay");
  Check(s.Start(zero) is null&&s.Playing,"explicit play overrides reduced motion");
  Check(s.Step(1)==1&&s.Step(1)==2&&!s.Playing,"rapid manual paging tracks requested frame");
  Check(s.Step(1) is null,"last page boundary");s.Fail();Check(s.RequestedIndex==s.Index&&!s.Waiting,"failed navigation rolls back");
  s.Accept("c",0,2,false,1,0,zero,false);Check(!s.Playing&&s.Count==2,"TIFF is paged not autoplayed");
  Check(s.Step(-1) is null&&s.Step(1)==1,"page boundaries");
  s.Accept("d",0,1,false,1,0,zero,false);Check(!s.Waiting&&s.Count==1,"new file resets state");
  s.Reset();Check(s.Count==1&&!s.Playing&&!s.Waiting,"close resets");return n;
 }
}
