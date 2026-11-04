using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;
public sealed partial class GalleryView
{
 private int _preferredThumbnailSize=320;private double _densityFactor=1;private bool _showContentTags=true,_pendingSharedSort;
 internal string CurrentSort=>_sortKeys[Math.Max(0,SortBox.SelectedIndex)];
 internal void ApplyPreferences(JsonElement values)
 {
  if(values.TryGetProperty("thumbnailSize",out var size)&&size.TryGetInt32(out int pixels))_preferredThumbnailSize=Math.Clamp(pixels,64,1200);
  if(values.TryGetProperty("density",out var density))_densityFactor=density.GetString()=="compact"?.85:1;
  if(values.TryGetProperty("showTags",out var tags))_showContentTags=tags.ValueKind==JsonValueKind.True;
  if(values.TryGetProperty("sort",out var sort)){int index=Array.IndexOf(_sortKeys,sort.GetString());if(index>=0&&SortBox.SelectedIndex!=index){_updating=true;SortBox.SelectedIndex=index;_updating=false;_pendingSharedSort=true;}}
  _images?.SetSize(_preferredThumbnailSize);ContentTags.Visibility=_showContentTags?Visibility.Visible:Visibility.Collapsed;Layout();
  if(_active){foreach(var tile in _tiles)if(ActiveImages.ContainerFromItem(tile)is not null)_images?.Visible(tile,true);ApplyPendingSort();}
 }
 private void ApplyPendingSort(){if(_pendingSharedSort&&_active&&_model is not null){_pendingSharedSort=false;ChangeScope();}}
 private void ScaleManagement(FrameworkElement element)
 {if(element is Control control)control.FontSize=14*_scale;if(element is Panel panel)foreach(var child in panel.Children.OfType<FrameworkElement>())ScaleManagement(child);}
}
