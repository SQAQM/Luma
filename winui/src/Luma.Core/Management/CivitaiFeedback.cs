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
  if(action=="models.retrySync")return "正在重试失败项";
  return "已完成";
 }
 public static string CoverStatus(JsonElement model)
 {
  if(Flag(model,"cover_available"))return "加载封面…";
  string status=Text(model,"status");
  return status switch{
   "sync_error_unauthorized"=>"密钥验证失败",
   "sync_error_not_found"=>"当前文件哈希未匹配",
   "sync_error_forbidden"=>"访问受限",
   "sync_error_rate_limited"=>"请求频率受限",
   "synced"=>"未获取到封面",
   _ when status.StartsWith("sync_error",StringComparison.Ordinal)=>"获取失败，可重试",
   _=>"待获取封面"};
 }
 public static string SyncResult(JsonElement job)
 {
  string issues=Issues(job);if(issues.Length>0)return issues.Replace("\n"," · ");
  string message=Text(job,"feedback");
  foreach(var pair in new[]{("密钥无效","密钥无效"),("请求过于频繁","请求频率受限"),("无法连接","连接失败"),("拒绝访问","访问受限"),("未找到公开","未找到模型"),("无法验证","资料格式错误")})if(message.Contains(pair.Item1,StringComparison.Ordinal))return pair.Item2;
  if(job.ValueKind==JsonValueKind.Object&&job.TryGetProperty("errors",out var errors)&&errors.TryGetInt32(out var count)&&count>0)return "部分未完成";
  var saved=System.Text.RegularExpressions.Regex.Match(message,@"封面已保存 (\d{1,10}) 个");
  if(saved.Success)return "封面 "+saved.Groups[1].Value+" 个";
  return Text(job,"status") switch{"completed"=>"已完成","completed_with_errors"=>"部分未完成","cancelled"=>"已取消","failed"=>"获取失败",_=>"更新中"};
 }
 public static string Issues(JsonElement job)
 {
  var lines=new List<string>();
  if(job.ValueKind!=JsonValueKind.Object)return "";
  if(job.TryGetProperty("issues",out var issues)&&issues.ValueKind==JsonValueKind.Array)foreach(var item in issues.EnumerateArray().Take(32)){
   string label=Text(item,"code") switch{
    "not_found"=>"当前文件哈希未匹配","unauthorized"=>"密钥无效","forbidden"=>"访问受限","rate_limited"=>"请求频率受限",
    "sidecar_busy"=>"文件正在使用","sidecar_permission"=>"目录没有写入权限","sidecar_format"=>"已有资料格式异常",
    "sidecar_size"=>"资料文件过大","sidecar_conflict"=>"同名资料冲突","sidecar_space"=>"磁盘空间不足","sidecar_write"=>"资料保存失败",
    "cover_not_found"=>"封面资源不存在","profile_not_found"=>"模型介绍暂不可用","version_not_found"=>"版本资料暂不可用",
    "network"=>"网络连接失败","unavailable"=>"Civitai 暂时不可用","credentials"=>"密钥读取失败","source_changed"=>"模型文件已变化",
    "file_missing"=>"模型文件不存在","file_error"=>"本地文件操作失败","sync_error"=>"同步失败","invalid_cover"=>"封面读取失败","hash_mismatch"=>"模型哈希不匹配",
    "response_too_large"=>"资料过大","invalid_response"=>"资料格式错误","redirect"=>"接口发生重定向","invalid_hash"=>"模型哈希无效","invalid_id"=>"模型编号无效",_=>""};
   if(label.Length>0&&item.TryGetProperty("count",out var count)&&count.ValueKind==JsonValueKind.Number&&count.TryGetInt64(out long n)&&n>0)lines.Add($"{label}：{n} 个");
  }
  if(Text(job,"status") is "completed_with_errors" or "failed" or "cancelled"){
   if(job.TryGetProperty("total",out var total)&&total.TryGetInt64(out long t)&&job.TryGetProperty("processed",out var processed)&&processed.TryGetInt64(out long p)&&t>p&&p>=0)lines.Add($"未处理：{t-p} 个");
  }
  return string.Join("\n",lines);
 }
}
