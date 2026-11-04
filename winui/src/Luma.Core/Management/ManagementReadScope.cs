namespace Luma.Core.Management;
// Dispatcher-owned. A held poll or old refresh cannot consume a newer refresh demand.
public sealed class ManagementReadScope
{
 public long Generation{get;private set;}
 public bool Busy{get;private set;}
 public bool RefreshPending{get;private set;}
 public void Invalidate(){Generation++;}
 public bool TryRefresh(){RefreshPending=true;if(Busy)return false;Busy=true;RefreshPending=false;return true;}
 public bool TryPoll(){if(Busy||RefreshPending)return false;Busy=true;return true;}
 public void Complete()=>Busy=false;
}
