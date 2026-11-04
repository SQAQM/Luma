using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;

// Explicit, fresh-profile diagnostics only. No Windows UI Automation or user input injection.
public sealed partial class ManagementView
{
 private bool _verifyModelUi,_modelUiVerificationStarted;
 private async Task VerifyModelUi()
 {
  if(!_verifyModelUi||_modelUiVerificationStarted||_models.Length<2)return;
  _modelUiVerificationStarted=true;var checks=new List<string>();
  void Check(bool value,string name){if(!value)throw new InvalidOperationException(name);checks.Add(name);}
  try{
   await Task.Delay(350);if(!_visible||_page!="Models")return;
   double height=ModelControlScroll.ActualHeight;
   var rows=ModelCards.Items.OfType<ManagementRow>().Take(2).ToArray();
   Check(rows.Length==2,"two indexed models");
   for(int attempt=0;attempt<30&&_coverCompleted<2;attempt++)await Task.Delay(100);
   Check(_coverCompleted>=2,"local previews rendered without network sync");
   ModelCards.SelectedItems.Clear();ModelCards.SelectedItems.Add(rows[0]);await Task.Delay(300);
   Check(!_modelInspector&&Records.SelectedItems.Count==1,"single selection stays in card browser");
   Check(ModelControlScroll.Visibility==Visibility.Visible,"toolbar remains after selection");
   await OpenSelectedModelDetails();await Task.Delay(400);
   Check(_modelInspector,"explicit details opens inspector");
   Check(ModelControlScroll.Visibility==Visibility.Visible,"toolbar remains in details");
   ModelCards.SelectedItems.Add(rows[1]);await Task.Delay(300);
   Check(!_modelInspector&&Records.SelectedItems.Count==2,"multi-selection closes inspector without losing selection");
   Check(ModelControlScroll.Visibility==Visibility.Visible&&Math.Abs(ModelControlScroll.ActualHeight-height)<1,"toolbar geometry remains stable");
   ModelCards.SelectedItems.Clear();await Task.Delay(300);
   Check(!_modelInspector,"clearing selection stays in browser");
   Check(ModelPager.Children.OfType<Button>().All(button=>button.Content is FontIcon),"header actions are icon-only");
   object? dropdown=null;
   if(_rootChoice is not null){
    _rootChoice.IsDropDownOpen=true;await Task.Delay(500);Check(_rootChoice.IsDropDownOpen,"native dropdown opens");
    var popup=Services.DropdownLayout.Find<Microsoft.UI.Xaml.Controls.Primitives.Popup>(_rootChoice,"Popup");Check(popup is not null&&popup.VerticalOffset>=_rootChoice.ActualHeight,"dropdown defaults below field");
    var border=popup?.Child is{} child?Services.DropdownLayout.Find<Border>(child,"PopupBorder"):null;
    var field=_rootChoice.TransformToVisual(XamlRoot.Content).TransformBounds(new Windows.Foundation.Rect(0,0,_rootChoice.ActualWidth,_rootChoice.ActualHeight));
    var panel=border?.TransformToVisual(XamlRoot.Content).TransformBounds(new Windows.Foundation.Rect(0,0,border.ActualWidth,border.ActualHeight));
    Check(panel is{} bounds&&bounds.Top>=field.Bottom,"popup visual bounds lie below field");
    dropdown=new{field,panel};
    Check(Services.DropdownLayout.Find<TextBlock>(_rootChoice,"OpenSelectionText") is{Visibility:Visibility.Visible,Text.Length:>0},"open dropdown retains field text");Notify();
   }
   await File.WriteAllTextAsync(Path.Combine(_data,"model-ui-check.json"),JsonSerializer.Serialize(new{result="passed_native_model_workflow_fixture",checks,dropdown,scope="own fresh diagnostics profile, synthetic model rows; intrinsic WinUI selection events and layouts, no OS pointer automation"}));
  }catch(Exception error){await File.WriteAllTextAsync(Path.Combine(_data,"model-ui-check.json"),JsonSerializer.Serialize(new{result="failed_native_model_workflow_fixture",checks,error=error.Message}));}
 }
}
