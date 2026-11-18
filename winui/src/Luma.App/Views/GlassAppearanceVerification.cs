using System.Text.Json;
using Luma.Preview.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Views;

public sealed partial class ViewerView
{
 internal async Task VerifyGlassAppearanceUi(List<object> checks,Func<string,Task> capture,string theme,bool exerciseMotion)
 {
  if(Environment.GetEnvironmentVariable("LUMA_VIEWER_GLASS_UI_TEST")!="1")return;
  void Check(bool value,string name)=>checks.Add(new{passed=value,name});
  UpdateLayout();await Task.Delay(200);
  foreach(var border in new[]{HeaderMetadata,HeaderCommands,ToolDock,Info}){
   Check(GlassSurfaceEffects.GetEnabled(border),theme+" shared surfaces use the bounded native material owner");
   Check(border.Shadow is ThemeShadow,theme+" glass surfaces have native elevation");
  }
  var state=JsonSerializer.SerializeToElement(GlassSurfaceEffects.Snapshot(XamlRoot));
  Check(state.GetProperty("surfaces").GetInt32()<=8,theme+" active viewer effect surfaces stay bounded");
  await capture("appearance-"+theme.ToLowerInvariant());
  var dialog=new ContentDialog{XamlRoot=XamlRoot,Title="整理资料",Content="3 个文件",PrimaryButtonText="确认整理",CloseButtonText="取消"};
  var shown=dialog.ShowAsync();
  try{await Task.Delay(240);Check(ReferenceEquals(dialog.PrimaryButtonStyle,(Style)Application.Current.Resources["LumaPrimaryButton"]),theme+" confirmation primary action uses its shared prominent style");await capture("dialog-"+theme.ToLowerInvariant());}
  finally{dialog.Hide();await shown;}
  if(!exerciseMotion)return;
  await Task.Delay(200);
  var button=GlassButtonEffects.CreateNativeMotionVerificationButton();
  var host=new Border{Style=(Style)Application.Current.Resources["LumaGlassDock"],Padding=new(18),HorizontalAlignment=HorizontalAlignment.Center,VerticalAlignment=VerticalAlignment.Center,Child=button};
  GlassSurfaceEffects.SetReceiver(host,Stage);ViewerRoot.Children.Add(host);
  try{
   UpdateLayout();await Task.Delay(180);
   await GlassButtonEffects.VerifyNativeMotion(button,checks,capture);
   GlassSurfaceEffects.SetPolicy(XamlRoot,false,false);await Task.Delay(50);
   Check(ToolDock.Shadow is null&&!GlassSurfaceEffects.HasTransitions(ToolDock),"high-contrast policy removes optional shadows and surface transition requests");
   GlassSurfaceEffects.SetPolicy(XamlRoot,false,true);await Task.Delay(50);
   Check(ToolDock.Shadow is ThemeShadow&&!GlassSurfaceEffects.HasTransitions(ToolDock),"reduced motion keeps static material depth without requesting animated dismissal");
  }finally{
   ViewerRoot.Children.Remove(host);GlassSurfaceEffects.SetPolicy(XamlRoot,GlassButtonEffects.IsMotionEnabled(XamlRoot),true);UpdateLayout();await Task.Delay(100);
  }
 }
}
