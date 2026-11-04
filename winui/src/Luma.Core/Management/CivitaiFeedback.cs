using System.Text.Json;
namespace Luma.Core.Management;

public static class CivitaiFeedback
{
 private static bool Flag(JsonElement value,string key)=>value.ValueKind==JsonValueKind.Object&&value.TryGetProperty(key,out var flag)&&flag.ValueKind==JsonValueKind.True;
 private static string Text(JsonElement value,string key)=>value.ValueKind==JsonValueKind.Object&&value.TryGetProperty(key,out var text)&&text.ValueKind==JsonValueKind.String?text.GetString()??"":"";
 public static string Completed(string action,JsonElement result)
 {
  if(action is "credential.test" or "credential.connect"){
   if(Flag(result,"ok"))return Flag(result,"authenticated")?"密钥已验证":"匿名连接";
   return Text(result,"code") switch{
    "unauthorized"=>"密钥无效",
    "forbidden"=>"访问受限",
    "rate_limited"=>"请求频率受限",
    "credentials"=>"密钥读取失败",
    "redirect"=>"接口地址异常",
    "network"=>"连接失败",
    _=>"连接失败"};
  }
  if(action=="credential.set")return Flag(result,"remembered")?"密钥已保存":"密钥本次有效";
  if(action=="credential.clear")return "Civitai 密钥已清除。";
  if(action=="models.sync")return "正在更新资料";
  return "已完成";
 }
 public static string CoverStatus(JsonElement model)
 {
  if(Flag(model,"cover_available"))return "加载封面…";
  string status=Text(model,"status");
  return status switch{
   "sync_error_unauthorized"=>"密钥验证失败",
   "sync_error_not_found"=>"未找到模型资料",
   "sync_error_forbidden"=>"访问受限",
   "sync_error_rate_limited"=>"请求频率受限",
   "synced"=>"未获取到封面",
   _ when status.StartsWith("sync_error",StringComparison.Ordinal)=>"获取失败，可重试",
   _=>"待获取封面"};
 }
 public static string SyncResult(JsonElement job)
 {
  string message=Text(job,"feedback");
  foreach(var pair in new[]{("密钥无效","密钥无效"),("请求过于频繁","请求频率受限"),("无法连接","连接失败"),("拒绝访问","访问受限"),("未找到公开","未找到模型"),("无法验证","资料格式错误")})if(message.Contains(pair.Item1,StringComparison.Ordinal))return pair.Item2;
  if(job.ValueKind==JsonValueKind.Object&&job.TryGetProperty("errors",out var errors)&&errors.TryGetInt32(out var count)&&count>0)return "部分未完成";
  var saved=System.Text.RegularExpressions.Regex.Match(message,@"封面已保存 (\d{1,2}) 个");
  if(saved.Success)return "封面 "+saved.Groups[1].Value+" 个";
  return Text(job,"status") switch{"completed"=>"已完成","completed_with_errors"=>"部分未完成","cancelled"=>"已取消","failed"=>"获取失败",_=>"更新中"};
 }
}
