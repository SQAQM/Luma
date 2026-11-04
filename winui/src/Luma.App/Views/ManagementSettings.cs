using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;
public sealed partial class ManagementView
{
 internal Func<string>? SettingsConnectionFeedback;internal Func<bool>? CanSettingsConnect;
 private void SettingsForm()
 {
  ClearForm();_sub="";
  if(_api is null){var connect=Button("连接本地服务","SettingsOpenLibrary",()=>{OpenSettingsLibraryRequested?.Invoke();return Task.CompletedTask;});connect.IsEnabled=CanSettingsConnect?.Invoke()!=false;Buttons(connect);Fields.Children.Add(Text(SettingsConnectionFeedback?.Invoke()??""));}
  else{PreferencesForm();Buttons(Button("保存图库偏好","ManageSavePreferences",SavePreferences,true));CivitaiForm();}
  Fields.Children.Add(Text("模型",true));Buttons(Button("模型规则","ManageRegistry",RegistryForm));Fields.Children.Add(Text("匹配"));Buttons(Button("重新匹配","ManageReclassify",()=>ConfirmTask("重新匹配","全部图片","models.reclassify",new{scope="all"}),true),Button("更新关联","ManageRebuildLinks",()=>ConfirmTask("更新关联","全部图片","models.rebuild",new{}),true));
  Fields.Children.Add(Text("数据与空间",true));Buttons(Button("管理备份与缓存","SettingsStorage",()=>{StorageRequested?.Invoke();return Task.CompletedTask;}));
 }
 private void CivitaiForm()
 {
  Fields.Children.Add(Text("Civitai连接",true));Fields.Children.Add(Text(_credential is{} c&&B(c,"configured")?"已配置":"未配置"));
  _token=new PasswordBox{PlaceholderText="API 密钥",MaxLength=4096,HorizontalAlignment=HorizontalAlignment.Stretch,FontSize=14*_scale};AutomationProperties.SetAutomationId(_token,"ManageApiToken");AutomationProperties.SetName(_token,"Civitai API 密钥");Fields.Children.Add(_token);_remember=new CheckBox{Content="记住密钥",IsChecked=_credential is{} saved&&B(saved,"configured")?B(saved,"remembered"):true,FontSize=14*_scale};AutomationProperties.SetAutomationId(_remember,"ManageRememberCredential");Fields.Children.Add(_remember);
  Buttons(Button("连接 Civitai","ManageTestCredential",ConnectCivitai,true),Button("清除密钥","ManageClearCredential",()=>Write("credential.clear",new{}),true));

 }
 private async Task ConnectCivitai()
 {
  string token=_token?.Password.Trim()??"";bool remember=_remember?.IsChecked==true;
  if(_token is not null)_token.Password="";
  if(token.Length>0)await Write("credential.connect",new{token,remember});
  else await Write("credential.test",new{});
 }
}
