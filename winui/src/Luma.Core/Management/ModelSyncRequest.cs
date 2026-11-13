namespace Luma.Core.Management;
public sealed record ModelSyncRequest(long Count,object Args,string Range)
{
 public static ModelSyncRequest Create(IEnumerable<long> selection,long? rootId,long total,string query,string[] types,string[] families,bool forceRefresh=false,bool compatibility=false)
 {
  long[] ids=selection.Where(id=>id>0).Distinct().ToArray();
  return ids.Length>0?new(ids.Length,new{ids,downloadCovers=true,forceRefresh,compatibility},"所选"):
   new(Math.Max(0,total),new{scope=new{rootId,query,types,families},downloadCovers=true,forceRefresh,compatibility},"当前范围");
 }
}
