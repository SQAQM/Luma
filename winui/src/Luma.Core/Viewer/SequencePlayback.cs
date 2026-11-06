namespace Luma.Core.Viewer;

// Time starts when decoded pixels become visible. A slow decoder cannot queue ticks.
public sealed class SequencePlayback
{
 private string? _key;private int _limit=1,_loops,_duration;private TimeSpan _deadline;
 public int Count{get;private set;}=1;public int Index{get;private set;}public int RequestedIndex{get;private set;}
 public bool Animated{get;private set;}public bool Playing{get;private set;}public bool Waiting{get;private set;}public bool Completed{get;private set;}
 public void Accept(string key,int index,int count,bool animated,int repeatLimit,int durationMs,TimeSpan now,bool reduced)
 {
  if(count is <1 or >4096||index<0||index>=count||repeatLimit is <0 or >65536)throw new ArgumentOutOfRangeException(nameof(count));
  if(_key!=key){Reset();_key=key;Playing=animated&&!reduced;}
  Count=count;Index=RequestedIndex=index;Animated=animated&&count>1;_limit=repeatLimit;_duration=Math.Clamp(durationMs,20,60000);Waiting=false;
  if(!Animated)Playing=false;_deadline=now+TimeSpan.FromMilliseconds(_duration);
 }
 public int? Due(TimeSpan now)
 {
  if(!Playing||Waiting||now<_deadline)return null;
  int next=Index+1;
  if(next>=Count){_loops++;if(_limit>0&&_loops>=_limit){Playing=false;Completed=true;return null;}next=0;}
  return Request(next);
 }
 public int? Start(TimeSpan now)
 {
  if(!Animated)return null;Playing=true;_deadline=now+TimeSpan.FromMilliseconds(_duration);
  if(!Completed)return null;Completed=false;_loops=0;return Request(0);
 }
 public void Pause()=>Playing=false;
 public int? Step(int direction)
 {
  Pause();int next=RequestedIndex+Math.Sign(direction);
  if(next<0||next>=Count||next==RequestedIndex)return null;
  _loops=0;Completed=false;return Request(next);
 }
 private int Request(int index){RequestedIndex=index;Waiting=true;return index;}
 public void Fail(){Pause();Waiting=false;RequestedIndex=Index;}
 public void Reset(){_key=null;Count=1;Index=RequestedIndex=_loops=0;_limit=1;Animated=Playing=Waiting=Completed=false;}
}
