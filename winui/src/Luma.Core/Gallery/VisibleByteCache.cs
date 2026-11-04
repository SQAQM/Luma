namespace Luma.Core.Gallery;
public sealed class VisibleByteCache<TKey,TValue>(int capacity,long budget) where TKey:notnull
{
 private readonly Dictionary<TKey,(TValue Value,long Weight,LinkedListNode<TKey> Node)> _values=[];private readonly LinkedList<TKey> _order=[];
 public long Bytes{get;private set;}public int Count=>_values.Count;public IEnumerable<TKey> Keys=>_values.Keys;
 public bool ContainsKey(TKey key)=>_values.ContainsKey(key);
 public bool TryGetValue(TKey key,out TValue value){if(_values.TryGetValue(key,out var v)){_order.Remove(v.Node);_order.AddLast(v.Node);value=v.Value;return true;}value=default!;return false;}
 public bool Put(TKey key,TValue value,long weight,Func<TKey,bool> pinned,out IReadOnlyList<KeyValuePair<TKey,TValue>> evicted)
 {
  var removed=new List<KeyValuePair<TKey,TValue>>();evicted=removed;if(weight<0||weight>budget||capacity<1)return false;
  bool replacing=_values.TryGetValue(key,out var old);long bytes=Bytes-(replacing?old.Weight:0)+weight;int count=Count+(replacing?0:1);var victims=new List<TKey>();
  foreach(var candidate in _order){if(count<=capacity&&bytes<=budget)break;if(EqualityComparer<TKey>.Default.Equals(candidate,key)||pinned(candidate))continue;victims.Add(candidate);bytes-=_values[candidate].Weight;count--;}
  if(count>capacity||bytes>budget)return false;
  if(replacing)victims.Add(key);
  foreach(var victim in victims){var v=_values[victim];removed.Add(new(victim,v.Value));Bytes-=v.Weight;_order.Remove(v.Node);_values.Remove(victim);}
  _values[key]=(value,weight,_order.AddLast(key));Bytes+=weight;return true;
 }
 public void Clear(){_values.Clear();_order.Clear();Bytes=0;}
}
