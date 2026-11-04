using System.Text.Json;
namespace Luma.Core.Management;

public sealed record FileReview(string Id,string Kind,string State,string Fingerprint,long Total,long Actionable,long Skipped,long Conflicts)
{
 public static FileReview Parse(JsonElement value)
 {
  string S(string key)=>value.TryGetProperty(key,out var item)&&item.ValueKind==JsonValueKind.String?item.GetString()??"":"";
  long N(string key)=>value.TryGetProperty(key,out var item)&&item.TryGetInt64(out long number)?number:0;
  return new(S("id"),S("kind"),S("state"),S("fingerprint"),N("total"),N("actionable"),N("skipped"),N("conflicts"));
 }
 public bool Ready=>State=="ready"&&Id.Length==32&&Fingerprint.Length==64&&Actionable>0;
}

// Dispatcher-owned consent is scoped to one immutable server preview and one UI draft.
public sealed class FileReviewState
{
 private string _context="";private bool _approved,_consumed;
 public long Version{get;private set;}
 public FileReview? Review{get;private set;}
 public bool CanConfirm=>Review is{Ready:true}&&!_consumed;
 public bool CanExecute=>CanConfirm&&_approved;
 public void ChangeContext(string context)
 {
  if(context==_context)return;
  _context=context;Version++;Review=null;_approved=_consumed=false;
 }
 public void Invalidate(){Version++;Review=null;_approved=_consumed=false;}
 public void Accept(JsonElement value,long version)
 {
  if(version!=Version)return;
  Review=FileReview.Parse(value);_approved=_consumed=false;
 }
 public void Approve(bool value)=>_approved=value&&Review is{Ready:true}&&!_consumed;
 public void Revoke()=>_approved=false;
 public FileReview BeginExecute()
 {
  if(!CanExecute)throw new InvalidOperationException("preview confirmation required");
  _consumed=true;_approved=false;return Review!;
 }
}
