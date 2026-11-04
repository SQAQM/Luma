using System.Text.Json;
namespace Luma.Core.Management;
public sealed class ManagementJobSelection
{
 private string _id="";
 public JsonElement? Value{get;private set;}
 public bool CanCancel=>Value is{} job&&job.TryGetProperty("status",out var status)&&status.GetString() is "queued" or "running";
 public void Select(string id){_id=id;Value=null;}
 public void Update(IEnumerable<JsonElement> jobs){Value=null;foreach(var job in jobs){if(job.TryGetProperty("id",out var id)&&id.GetString()==_id){Value=job;break;}}}
}
