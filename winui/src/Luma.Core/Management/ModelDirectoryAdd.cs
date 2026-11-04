using System.Text.Json;
namespace Luma.Core.Management;

// Authorization to continue an add is deliberately process-local. Historical receipts
// can restore the selected root, but do not authorize a new indexing operation.
public sealed class ModelDirectoryAdd(ManagementState state)
{
 private int _busy;
 private string? _addIntent;
 public long? RootId { get; private set; }
 public bool IndexRequested { get; private set; }
 public bool Busy => Volatile.Read(ref _busy)!=0;
 public async Task<bool> AddAsync(string path)
 {
  if(Interlocked.CompareExchange(ref _busy,1,0)!=0)return false;
  try
  {
   if(state.Writing||state.Uncertain)return false;
   RootId=null;IndexRequested=false;_addIntent=null;
   var write=state.WriteAsync("models.add",new{path});
   string id=state.OperationId;
   bool added=await write;
   if(!SameAdd(id))return false;
   if(!added){if(state.Uncertain)_addIntent=id;return false;}
   if(!TryRoot(state.Result,out long root))return false;
   RootId=root;
   return await ScanAsync();
  }
  finally{Volatile.Write(ref _busy,0);}
 }
 public async Task<bool> ResolveAsync()
 {
  if(Interlocked.CompareExchange(ref _busy,1,0)!=0)return false;
  try
  {
   if(state.Writing||state.OperationId.Length==0)return false;
   string id=state.OperationId,action=state.LastAction;
   await state.ResolveAsync();
   if(action!="models.add"||!SameAdd(id)||state.LastResolvedAction!="models.add")return false;
   if(state.Uncertain||state.Outcome!="completed")return false;
   if(!TryRoot(state.LastResolvedResult,out long root))return false;
   RootId=root;
   if(_addIntent!=id)return true;
   // Consume intent before submission: an uncertain scan must only be resolved,
   // never replayed by repeating this continuation.
   _addIntent=null;
   return await ScanAsync();
  }
  finally{Volatile.Write(ref _busy,0);}
 }
 private bool SameAdd(string id)=>id.Length>0&&state.OperationId==id&&state.LastAction=="models.add";
 private async Task<bool> ScanAsync()
 {
  if(IndexRequested||state.Writing||state.Uncertain||RootId is not long root)return false;
  IndexRequested=true;
  return await state.WriteAsync("models.scan",new{rootId=root});
 }
 private static bool TryRoot(JsonElement result,out long root)
 {
  root=0;
  return result.ValueKind==JsonValueKind.Object&&result.TryGetProperty("id",out var id)&&
   id.ValueKind==JsonValueKind.Number&&id.TryGetInt64(out root)&&root>0;
 }
}
