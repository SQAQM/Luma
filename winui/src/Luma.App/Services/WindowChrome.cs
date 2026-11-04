using Luma.Core.Design;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Services;

// XAML supplies the material and identity; Windows retains caption controls/hit testing.
internal sealed class WindowChrome
{
 private readonly Window _window;
 private readonly Grid _area;
 private readonly Border _tint;
 private readonly Func<string,Brush> _brush;
 private bool _highContrast,_colorFailure;
 public bool Supported{get;private set;}
 public bool HighContrast=>_highContrast;
 public WindowChrome(Window window,TitleBar title,Grid area,Border tint,Func<string,Brush> brush)
 {
  _window=window;_area=area;_tint=tint;_brush=brush;
  try{Supported=AppWindowTitleBar.IsCustomizationSupported();if(Supported){window.ExtendsContentIntoTitleBar=true;window.SetTitleBar(title);}}
  catch(Exception e) when(e is System.Runtime.InteropServices.COMException or NotSupportedException){Supported=false;window.ExtendsContentIntoTitleBar=false;}
  Refresh();
 }
 public object Snapshot=>new{supported=Supported,extended=_window.ExtendsContentIntoTitleBar,visible=_area.Visibility==Visibility.Visible,height=_area.Visibility==Visibility.Visible?_area.ActualHeight:0,captionRightInset=_window.AppWindow.TitleBar.RightInset,captionLeftInset=_window.AppWindow.TitleBar.LeftInset,colorFailure=_colorFailure};
 public void ApplyMaterial(MaterialPlan plan){bool changed=_highContrast!=(plan.Reason=="high_contrast");_highContrast=plan.Reason=="high_contrast";_tint.Background=new SolidColorBrush(Colors.Transparent);uint border=_highContrast?0xffffffff:0xfffffffe;var hwnd=WinRT.Interop.WindowNative.GetWindowHandle(_window);_ = DwmSetWindowAttribute(hwnd,34,ref border,4);if(changed)SetWindowPos(hwnd,0,0,0,0,0,0x37);Refresh();}
 [System.Runtime.InteropServices.DllImport("dwmapi.dll")]private static extern int DwmSetWindowAttribute(nint h,int attribute,ref uint value,int size);
 [System.Runtime.InteropServices.DllImport("user32.dll")]private static extern bool SetWindowPos(nint h,nint after,int x,int y,int width,int height,uint flags);
 public void Refresh()
 {
  _area.Visibility=Supported&&_window.AppWindow.Presenter.Kind!=AppWindowPresenterKind.FullScreen?Visibility.Visible:Visibility.Collapsed;
  if(!Supported)return;
  try{
   var caption=_window.AppWindow.TitleBar;
   caption.ButtonBackgroundColor=Colors.Transparent;caption.ButtonInactiveBackgroundColor=Colors.Transparent;
   caption.ButtonForegroundColor=_highContrast?null:((SolidColorBrush)_brush("LumaText")).Color;
   caption.ButtonInactiveForegroundColor=_highContrast?null:((SolidColorBrush)_brush("LumaSecondary")).Color;
   caption.ButtonHoverBackgroundColor=_highContrast?null:((SolidColorBrush)_brush("LumaHover")).Color;
   caption.ButtonHoverForegroundColor=_highContrast?null:((SolidColorBrush)_brush("LumaText")).Color;
  }catch(System.Runtime.InteropServices.COMException){_colorFailure=true;}
 }
}
