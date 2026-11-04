using System.Text.Json;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
namespace Luma.Preview.Views;
public sealed partial class ManagementView
{
 private JsonElement _preferences;
 private ComboBox? _prefDensity,_prefSort;private NumberBox? _prefThumbnail,_prefCache;private CheckBox? _prefTags;
 internal string CurrentTheme{get;set;}="System";
 internal bool CurrentReducedMotion{get;set;}
 internal string CurrentSort{get;set;}="name";
 private readonly string[] _preferenceSortKeys=["name","name_desc","newest","oldest","modified_desc","modified_asc","size_desc","size_asc","resolution_desc","resolution_asc","rating_desc","rating_asc"];
 private void PreferencesForm()
 {
  Fields.Children.Add(Text("图库偏好",true));
  _prefDensity=Choice("图库密度","ManagePreferenceDensity",new[]{"舒适","紧凑"});_prefDensity.SelectedIndex=S(_preferences,"density")=="compact"?1:0;
  _prefSort=Choice("默认排序","ManagePreferenceSort",new[]{"名称 A–Z","名称 Z–A","最新添加","最早添加","最近修改","最早修改","大小从大到小","大小从小到大","分辨率从高到低","分辨率从低到高","评分从高到低","评分从低到高"});string sort=S(_preferences,"sort");_prefSort.SelectedIndex=Math.Max(0,Array.IndexOf(_preferenceSortKeys,sort==""?CurrentSort:sort));
  Fields.Children.Add(Text("缩略图尺寸（64–1200像素）"));_prefThumbnail=new NumberBox{Minimum=64,Maximum=1200,SmallChange=32,Width=180,HorizontalAlignment=Microsoft.UI.Xaml.HorizontalAlignment.Left,Value=N(_preferences,"thumbnailSize") is long size&&size>0?size:320,FontSize=14*_scale};AutomationProperties.SetAutomationId(_prefThumbnail,"ManagePreferenceThumbnail");Fields.Children.Add(_prefThumbnail);
  Fields.Children.Add(Text("缩略缓存上限（64–8192 MiB）"));_prefCache=new NumberBox{Minimum=64,Maximum=8192,SmallChange=64,Width=180,HorizontalAlignment=Microsoft.UI.Xaml.HorizontalAlignment.Left,Value=N(_preferences,"thumbnail_cache_limit_bytes") is long limit&&limit>0?limit/1048576:512,FontSize=14*_scale};AutomationProperties.SetAutomationId(_prefCache,"ManagePreferenceCache");AutomationProperties.SetName(_prefCache,"缩略缓存上限 MiB");Fields.Children.Add(_prefCache);

  _prefTags=new CheckBox{Content="显示详情内容标签卡片",IsChecked=_preferences.ValueKind!=JsonValueKind.Object||!_preferences.TryGetProperty("showTags",out var shown)||shown.ValueKind!=JsonValueKind.False,FontSize=14*_scale};AutomationProperties.SetAutomationId(_prefTags,"ManagePreferenceTags");Fields.Children.Add(_prefTags);
 }
 private async Task SavePreferences()
 {
  if(_prefThumbnail is null||!double.IsFinite(_prefThumbnail.Value)||_prefThumbnail.Value<64||_prefThumbnail.Value>1200){await Missing("缩略图尺寸应在64至1200之间。");return;}
  if(_prefCache is null||!double.IsFinite(_prefCache.Value)||_prefCache.Value<64||_prefCache.Value>8192||Math.Truncate(_prefCache.Value)!=_prefCache.Value){await Missing("缓存上限应为64至8192MiB的整数。");return;}
  await Write("settings.save",new{values=new{theme=CurrentTheme.ToLowerInvariant(),density=_prefDensity!.SelectedIndex==1?"compact":"comfortable",sort=_preferenceSortKeys[Math.Max(0,_prefSort!.SelectedIndex)],thumbnailSize=(int)_prefThumbnail.Value,thumbnailCacheMiB=(int)_prefCache.Value,showTags=_prefTags!.IsChecked==true,reduceMotion=CurrentReducedMotion}});
 }
}
