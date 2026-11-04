using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;
namespace Luma.Preview.Services;

// Arrange during the native opening/layout pass, before the popup's first frame.
public static class DropdownLayout
{
 public static readonly DependencyProperty EnabledProperty=DependencyProperty.RegisterAttached("Enabled",typeof(bool),typeof(DropdownLayout),new PropertyMetadata(false,Changed));
 private static readonly DependencyProperty OwnerProperty=DependencyProperty.RegisterAttached("Owner",typeof(object),typeof(DropdownLayout),new PropertyMetadata(null));
 public static bool GetEnabled(DependencyObject value)=>(bool)value.GetValue(EnabledProperty);
 public static void SetEnabled(DependencyObject value,bool enabled)=>value.SetValue(EnabledProperty,enabled);
 private static void Changed(DependencyObject value,DependencyPropertyChangedEventArgs args)
 {
  if(value is not ComboBox box)return;
  if(box.GetValue(OwnerProperty) is Owner old)old.Dispose();box.ClearValue(OwnerProperty);
  if(args.NewValue is true)box.SetValue(OwnerProperty,new Owner(box));
 }
 public static T? Find<T>(DependencyObject root,string name) where T:FrameworkElement
 {
  if(root is T element&&element.Name==name)return element;
  for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++)if(Find<T>(VisualTreeHelper.GetChild(root,i),name) is{} found)return found;
  return null;
 }
 private sealed class Owner:IDisposable
 {
  private readonly ComboBox _box;private Popup? _popup;private Border? _border;private long _offsetToken;private bool _placing;
  public Owner(ComboBox box){_box=box;box.Loaded+=Loaded;box.Unloaded+=Unloaded;box.DropDownOpened+=Opened;box.DropDownClosed+=Closed;if(box.IsLoaded)Attach();}
  private void Loaded(object sender,RoutedEventArgs args)=>Attach();
  private void Unloaded(object sender,RoutedEventArgs args)=>Detach();
  private void Attach(){var popup=Find<Popup>(_box,"Popup");if(ReferenceEquals(_popup,popup))return;Detach();_popup=popup;if(popup is not null){_offsetToken=popup.RegisterPropertyChangedCallback(Popup.VerticalOffsetProperty,(_,_)=>Place());popup.Opened+=Opened;}}
  private void Detach(){if(_popup is not null){_popup.UnregisterPropertyChangedCallback(Popup.VerticalOffsetProperty,_offsetToken);_popup.Opened-=Opened;}_popup=null;if(_border is not null)_border.SizeChanged-=Measured;_border=null;}
  private void Opened(object? sender,object args){Attach();if(Find<TextBlock>(_box,"OpenSelectionText") is{} label){label.Text=(_box.SelectedItem is ComboBoxItem item?item.Content:_box.SelectedItem)?.ToString()??_box.PlaceholderText;label.Visibility=Visibility.Visible;}Place();}
  private void Closed(object? sender,object args){if(Find<TextBlock>(_box,"OpenSelectionText") is{} label)label.Visibility=Visibility.Collapsed;}
  private void Measured(object sender,SizeChangedEventArgs args)=>Place();
  private void Place(){
   if(_placing||!_box.IsDropDownOpen||_popup?.Child is not{} child||_box.XamlRoot?.Content is not UIElement root)return;
   _placing=true;
   try{
    var border=Find<Border>(child,"PopupBorder");if(border is null)return;
    if(!ReferenceEquals(_border,border)){if(_border is not null)_border.SizeChanged-=Measured;_border=border;border.SizeChanged+=Measured;}
    double top=_box.TransformToVisual(root).TransformPoint(new Point()).Y;
    double below=_box.XamlRoot.Size.Height-top-_box.ActualHeight-6;
    double offset=below>=border.ActualHeight||below>=top?_box.ActualHeight+6:-border.ActualHeight-6;
    if(Math.Abs(_popup.VerticalOffset-offset)>.5)_popup.VerticalOffset=offset;
   }finally{_placing=false;}
  }
  public void Dispose(){Detach();_box.Loaded-=Loaded;_box.Unloaded-=Unloaded;_box.DropDownOpened-=Opened;_box.DropDownClosed-=Closed;}
 }
}
