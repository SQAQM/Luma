namespace Luma.Core.Viewer;

// UI-owned clock: no queued navigation and no wall-clock dependency.
public sealed class SlideshowClock
{
 private readonly TimeSpan _interval;
 private string? _identity;
 private TimeSpan? _readyAt;
 private bool _advanced;
 public bool Playing{get;private set;}
 public SlideshowClock(TimeSpan interval)
 {
  if(interval<TimeSpan.FromSeconds(1)||interval>TimeSpan.FromHours(1))throw new ArgumentOutOfRangeException(nameof(interval));
  _interval=interval;
 }
 public void Start(){_identity=null;_readyAt=null;_advanced=false;Playing=true;}
 public void Stop(){Playing=false;_identity=null;_readyAt=null;_advanced=false;}
 public bool Observe(string? identity,bool ready,bool hasNext,TimeSpan now)
 {
  if(!Playing)return false;
  if(identity!=_identity){_identity=identity;_readyAt=null;_advanced=false;}
  if(!ready||identity is null){_readyAt=null;return false;}
  if(!hasNext){Stop();return false;}
  if(_advanced)return false;
  if(_readyAt is null){_readyAt=now;return false;}
  if(now-_readyAt.Value<_interval)return false;
  _advanced=true;return true;
 }
}
