using System.Text.Json;
using Luma.Core.Management;
namespace Luma.Preview.Views;
public sealed partial class GalleryView
{
 private Microsoft.UI.Dispatching.DispatcherQueueTimer? _jobPoll;
 private readonly ManagementJobSelection _galleryJob=new();
 private string _galleryJobId="";private bool _jobReading;
 private void TrackGalleryJob(JsonElement result)
 {
  if(!result.TryGetProperty("id",out var value)||value.ValueKind!=JsonValueKind.String)return;
  _galleryJobId=value.GetString()??"";_galleryJob.Select(_galleryJobId);
  if(_jobPoll is null){_jobPoll=DispatcherQueue.CreateTimer();_jobPoll.Interval=TimeSpan.FromSeconds(1);_jobPoll.Tick+=async(_,_)=>await PollGalleryJob();}
  Feedback.Text="处理中";_jobPoll.Start();_=PollGalleryJob();
 }
 private async Task PollGalleryJob()
 {
  if(_jobReading||_disposed||!_active||_client is not{} client||_galleryJobId.Length==0)return;
  _jobReading=true;string id=_galleryJobId;
  try{
   var jobs=await new ManagementApi(client).ReadAsync("jobs",new{},CancellationToken.None);
   if(_disposed||!ReferenceEquals(client,_client)||id!=_galleryJobId)return;
   _galleryJob.Update(jobs.EnumerateArray());
   if(_galleryJob.Value is not{} job){Feedback.Text="处理中";_jobPoll?.Stop();return;}
   string status=job.GetProperty("status").GetString()??"";
   long processed=job.GetProperty("processed").GetInt64(),total=job.GetProperty("total").GetInt64();
   if(status is "queued" or "running")Feedback.Text=$"{processed:N0} / {total:N0}";
   else{
    _jobPoll?.Stop();_galleryJobId="";_catalogDirty=true;
    if(_active){await ReloadManaged();_catalogDirty=false;}
    Feedback.Text=status=="completed"?"处理完成。":status=="cancelled"?"已取消":"部分未完成";
   }
   UpdateManagement();Changed?.Invoke();
  }catch(Exception){if(!_disposed&&ReferenceEquals(client,_client)){Feedback.Text="进度读取失败";_jobPoll?.Stop();Changed?.Invoke();}}
  finally{_jobReading=false;}
 }
 private async Task CancelGalleryJob()
 {
  if(_management is not{} state||_galleryJobId.Length==0||!_galleryJob.CanCancel)return;
  await state.WriteAsync("job.cancel",new{jobId=_galleryJobId});Feedback.Text="正在停止";Changed?.Invoke();
 }
}
