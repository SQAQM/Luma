using System.Text.Json;
namespace Luma.Core.Management;
public sealed class ManagementJobSelection
{
 private string _id="";
 public JsonElement? Value{get;private set;}
 public bool CanCancel=>Value is{} job&&job.TryGetProperty("status",out var status)&&status.GetString() is "queued" or "running";
 public bool CanRetryFailed=>Value is{} job&&job.TryGetProperty("kind",out var kind)&&kind.GetString()=="model_sync"
  &&job.TryGetProperty("status",out var status)&&status.GetString() is "completed_with_errors" or "failed" or "cancelled" or "interrupted"
  &&job.TryGetProperty("retryable_count",out var count)&&count.TryGetInt64(out long n)&&n>0;
 public void Select(string id){_id=id;Value=null;}
 public void Update(IEnumerable<JsonElement> jobs){Value=null;foreach(var job in jobs){if(job.TryGetProperty("id",out var id)&&id.GetString()==_id){Value=job;break;}}}
}
