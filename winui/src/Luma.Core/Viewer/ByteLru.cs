namespace Luma.Core.Viewer;
public sealed class ByteLru<TKey,TValue>(int capacity,long budget) where TKey:notnull
{
 private readonly Dictionary<TKey,(TValue Value,long Weight,LinkedListNode<TKey> Node)> _values=[];
 private readonly LinkedList<TKey> _order=[];
 public long Bytes{get;private set;}public int Count=>_values.Count;
 public bool TryGet(TKey key,out TValue value){if(_values.TryGetValue(key,out var item)){_order.Remove(item.Node);_order.AddLast(item.Node);value=item.Value;return true;}value=default!;return false;}
 public bool Put(TKey key,TValue value,long weight){if(weight<0||weight>budget||capacity<1)return false;if(_values.Remove(key,out var previous)){Bytes-=previous.Weight;_order.Remove(previous.Node);}while(_values.Count>=capacity||Bytes+weight>budget){TKey first=_order.First!.Value;var item=_values[first];_order.RemoveFirst();_values.Remove(first);Bytes-=item.Weight;}_values[key]=(value,weight,_order.AddLast(key));Bytes+=weight;return true;}
 public void Clear(){_values.Clear();_order.Clear();Bytes=0;}
}
