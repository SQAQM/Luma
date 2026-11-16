using System.Collections.ObjectModel;
using System.Collections.Specialized;
using Luma.Core.Gallery;
using Luma.Core.Viewer;

internal static class FilmstripWindowTests
{
 private sealed class Tile(GalleryItem item){public GalleryItem Item{get;}=item;}
 public static int Run()
 {
  int count=0;void Check(bool passed,string name){if(!passed)throw new Exception("FILMSTRIP_FAIL "+name);count++;}
  GalleryItem Item(int id)=>new(id,new string((char)('a'+id),64),$"fixture-{id}.png",$"synthetic/fixture-{id}.png",960,640,1234,"PNG","","",[new(1,"fixture",true)]);
  var tiles=new ObservableCollection<Tile>();var removed=new List<Tile>();int resets=0,maxCount=0;
  tiles.CollectionChanged+=(_,e)=>{if(e.Action==NotifyCollectionChangedAction.Reset)resets++;maxCount=Math.Max(maxCount,tiles.Count);};
  void Reconcile(IReadOnlyList<GalleryItem> window)=>FilmstripWindow.Reconcile(tiles,window,t=>t.Item,i=>new Tile(i),tile=>{Check(tiles.Contains(tile),"removed tile becomes invisible before collection discard");removed.Add(tile);});
  Reconcile(Enumerable.Range(1,9).Select(Item).ToArray());var initial=tiles.ToArray();
  Reconcile(Enumerable.Range(2,9).Select(Item).ToArray());
  Check(tiles.Count==9&&tiles[0].Item.Id==2&&tiles[8].Item.Id==10,"next window contains nine ordered neighbors");
  Check(tiles.Take(8).Select((tile,i)=>ReferenceEquals(tile,initial[i+1])).All(v=>v),"next preserves all eight overlapping tile instances and their decoded state");
  Check(removed.Count==1&&ReferenceEquals(removed[0],initial[0]),"only departed neighbor becomes invisible");
  var forward=tiles.ToArray();Reconcile(Enumerable.Range(1,9).Select(Item).ToArray());
  Check(tiles.Skip(1).Select((tile,i)=>ReferenceEquals(tile,forward[i])).All(v=>v),"back preserves overlapping tiles");
  Check(!ReferenceEquals(tiles[0],initial[0]),"discarded neighbor has no unbounded object registry");
  var ordered=tiles.ToArray();Reconcile(Enumerable.Range(1,9).Reverse().Select(Item).ToArray());
  Check(tiles.Select(t=>t.Item.Id).SequenceEqual(new long[]{9,8,7,6,5,4,3,2,1}),"sort moves existing tiles into the requested order");
  Check(tiles.All(t=>ordered.Any(old=>ReferenceEquals(old,t))),"sort keeps every unchanged native item");
  Check(resets==0&&maxCount==9,"incremental changes never reset or exceed nine items");
  var original=Item(5);var originalTile=tiles.Single(t=>t.Item.Id==5);
  Reconcile(tiles.Select(t=>t.Item with{Tags=t.Item.Tags.Select(tag=>tag with{}).ToArray()}).ToArray());
  Check(ReferenceEquals(tiles.Single(t=>t.Item.Id==5),originalTile),"equal tags from a fresh response preserve tile references");
  // Each changed record must replace its immutable tile, even when image ID overlaps.
  GalleryItem[] changes=[original with{Identity=new string('0',64)},original with{Name="renamed.png"},original with{Path="synthetic/new.png"},original with{Width=1920},original with{Height=1280},original with{Size=9876},original with{Format="WEBP"},original with{Model="changed"},original with{Family="changed"},original with{Favorite=true},original with{Rating=5},original with{Tags=[new(1,"changed",true)]},original with{Tags=[new(1,"fixture",false)]},original with{Tags=[new(2,"fixture",true)]}];
  foreach(var changed in changes)
  {
   Reconcile([original]);var old=tiles[0];Reconcile([changed]);
   Check(!ReferenceEquals(old,tiles[0])&&ReferenceEquals(tiles[0].Item,changed),"identity or metadata change creates a fresh tile");
   Check(removed.Contains(old),"stale tile stops being visible");
  }
  Reconcile([]);Check(tiles.Count==0,"empty scope discards all current neighbors");
  Reconcile([Item(12)]);Check(tiles.Count==1&&tiles[0].Item.Id==12,"new scope uses only its current records");
  return count;
 }
}
