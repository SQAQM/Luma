using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Services;
internal static class ActionIcons
{
 public static void Refresh(Button button,string label)
 {
  button.Style=(Style)Application.Current.Resources["LumaViewerIcon"];
  button.Content=new FontIcon{Glyph="\uE72C",FontSize=16};
  AutomationProperties.SetName(button,label);ToolTipService.SetToolTip(button,label);
 }
}
