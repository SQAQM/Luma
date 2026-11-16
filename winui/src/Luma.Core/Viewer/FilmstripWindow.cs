using System.Collections.ObjectModel;
using Luma.Core.Gallery;

namespace Luma.Core.Viewer;

// Retain only objects in the current (at most nine-item) neighbor window.
public static class FilmstripWindow
{
 public static void Reconcile<T>(ObservableCollection<T> tiles,IReadOnlyList<GalleryItem> window,
  Func<T,GalleryItem> item,Func<GalleryItem,T> create,Action<T> removing) where T:class
 {
  for(int index=tiles.Count-1;index>=0;index--)
  {
   var tile=tiles[index];
   if(window.Any(next=>SameItem(item(tile),next)))continue;
   removing(tile);tiles.RemoveAt(index);
  }
  for(int index=0;index<window.Count;index++)
  {
   int found=index;
   while(found<tiles.Count&&!SameItem(item(tiles[found]),window[index]))found++;
   if(found==tiles.Count)tiles.Insert(index,create(window[index]));
   else if(found!=index)tiles.Move(found,index);
  }
 }

 private static bool SameItem(GalleryItem left,GalleryItem right)=>
  left.Id==right.Id&&left.Identity==right.Identity&&left.Name==right.Name&&left.Path==right.Path&&
  left.Width==right.Width&&left.Height==right.Height&&left.Size==right.Size&&left.Format==right.Format&&
  left.Model==right.Model&&left.Family==right.Family&&left.Favorite==right.Favorite&&left.Rating==right.Rating&&
  left.Tags.SequenceEqual(right.Tags);
}
