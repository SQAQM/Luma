using Luma.Core.Gallery;
using Luma.Core.Management;
using Luma.Preview.Services;
using Luma.Preview.Views.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
namespace Luma.Preview.Views;
public sealed partial class GalleryView
{
 internal nint WindowHandle{get;set;}
 private ManagementState? _management;
 private bool _annotationWriting;
 private long[] SelectedImageIds()=>_model?.SelectedIds.ToArray()??[];
 private Button ManageButton(string label,string id,Func<Task> action)
 {
  var b=new Button{Content=label,Style=(Style)Application.Current.Resources["LumaButton"]};AutomationProperties.SetAutomationId(b,id);
  b.Click+=async(_,_)=>{try{await action();}catch(Exception){Feedback.Text="操作失败";Changed?.Invoke();}};return b;
 }
 private void AddManagementControls()
 {
  var import=ManageButton("添加目录","GalleryImport",async()=>{var picker=new Microsoft.Windows.Storage.Pickers.FolderPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(WindowHandle)){CommitButtonText="添加"};var result=await picker.PickSingleFolderAsync();if(result is not null)await Submit("catalog.import",new{path=result.Path,recursive=true},false);else{Feedback.Text="已取消添加。";Changed?.Invoke();}});import.Style=(Style)Application.Current.Resources["LumaPrimaryButton"];SearchRow.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});Grid.SetColumn(import,2);SearchRow.Children.Add(import);
  AllowDrop=true;DragOver+=(_,e)=>{if(e.DataView.Contains(StandardDataFormats.StorageItems))e.AcceptedOperation=DataPackageOperation.Copy;};Drop+=async(_,e)=>{var deferral=e.GetDeferral();try{if(!e.DataView.Contains(StandardDataFormats.StorageItems))return;var items=await e.DataView.GetStorageItemsAsync();if(items.Count==1&&items[0] is Windows.Storage.StorageFolder folder){await Submit("catalog.import",new{path=folder.Path,recursive=true},false);}else{Feedback.Text="请拖入一个目录。";Changed?.Invoke();}}catch(Exception){Feedback.Text="无法接收目录，请使用添加目录。";Changed?.Invoke();}finally{deferral.Complete();}};
 }
 private void AddImageMenuCommands()
 {
  MenuFlyoutItem Item(string label,string id,Func<long[],Task> action)
  {
   var item=new MenuFlyoutItem{Text=label};
   AutomationProperties.SetAutomationId(item,id);
   item.Click+=async(_,_)=>{
    var ids=SelectedImageIds();
    if(!_active||!CanManageImages||ids.Length==0)return;
    try{await action(ids);}
    catch(Exception){if(_active){Feedback.Text="操作失败";Changed?.Invoke();}}
   };
   return item;
  }
  var reread=Item("重读生成信息","GalleryReread",async ids=>{
   if(await ConfirmManage("重读生成信息",$"{ids.Length} 张图片"))await Submit("metadata.refresh",new{ids},false);
  });
  var lookup=Item("从 Civitai 查模型","GalleryLookupModels",async ids=>{
   if(ids.Length<=60&&await ConfirmManage("从 Civitai 查模型",$"{ids.Length} 张图片"))await Submit("models.lookup",new{ids},false);
  });
  _menu.Items.Add(new MenuFlyoutSeparator());
  _menu.Items.Add(reread);
  _menu.Items.Add(lookup);
  _menu.Opening+=(_,_)=>{
   int count=SelectedImageIds().Length;
   reread.IsEnabled=CanManageImages&&count>0;
   lookup.IsEnabled=CanManageImages&&count is >0 and <=60;
  };
 }
 private bool CanManageImages=>_management is{Writing:false,Uncertain:false}&&!_annotationWriting&&!_catalogReloading;
 private void UpdateManagement()
 {
  bool writable=CanManageImages;
  foreach(var tile in _tiles)tile.CanEdit=writable;
  UpdateNavigationControls();
 }
 private void UpdateNavigationControls()
 {
  bool canCancel=_model?.Loading==true||_galleryJob.CanCancel;
  string label=_galleryJob.CanCancel?"取消图像任务":canCancel?"取消加载":"刷新";
  LineIcons.SetKind(RefreshIcon,canCancel?LineGlyph.Cancel:LineGlyph.Refresh);
  AutomationProperties.SetName(RefreshButton,label);
  ToolTipService.SetToolTip(RefreshButton,label);
  bool paging=!_annotationWriting&&!_catalogReloading;
  First.IsEnabled=Previous.IsEnabled=paging&&_model?.CanPrevious==true;
  Next.IsEnabled=paging&&_model?.CanNext==true;
  if(_model?.Loading!=true)PageButtons.Visibility=_model?.CanPrevious==true||_model?.CanNext==true?Visibility.Visible:Visibility.Collapsed;
 }
 private async Task<bool> ConfirmManage(string title,string body){var dialog=new ContentDialog{Title=title,Content=body,PrimaryButtonText=title.Replace("预览",""),CloseButtonText="取消",XamlRoot=XamlRoot};return await dialog.ShowAsync()==ContentDialogResult.Primary;}
 private async Task Submit(string action,object args,bool reload=true)
 {
  if(_management is not{} state)return;bool ok=await state.WriteAsync(action,args);Feedback.Text=state.Feedback;
  if(ok){if(reload&&_active)await ReloadManaged();else{_catalogDirty=true;TrackGalleryJob(state.Result);}}Changed?.Invoke();
 }
 private void ReactionsLoaded(object sender,Microsoft.UI.Xaml.RoutedEventArgs e)
 {
  if(sender is not ImageReactions reactions)return;
  if(ReferenceEquals(reactions.Tag,this))return;reactions.Tag=this;
  reactions.ChangeRequested+=async(favorite,rating)=>{if(reactions.DataContext is Luma.Preview.Services.GalleryTile tile)await PatchImage(tile.Item,favorite,rating);};
 }
 private async Task PatchImage(GalleryItem item,bool? favorite,int? rating)
 {
  var model=_model;var state=_management;
  if(!_active||_annotationWriting||model is null||state is not{Writing:false,Uncertain:false}||!model.Items.Any(i=>i.Id==item.Id&&i.Identity==item.Identity))return;
  _annotationWriting=true;UpdateManagement();Changed?.Invoke();
  var args=new Dictionary<string,object?>{{"imageId",item.Id},{"identity",item.Identity}};
  if(favorite.HasValue)args["favorite"]=favorite.Value;if(rating.HasValue)args["rating"]=rating.Value;
  try{bool ok=await state.WriteAsync("image.patch",args);if(_disposed||!ReferenceEquals(state,_management))return;
   if(ok&&!state.Uncertain){await ReloadManaged(false);if(_disposed||!ReferenceEquals(state,_management))return;Feedback.Text=favorite.HasValue?(favorite.Value?"已收藏":"已取消收藏"):rating==0?"已清除评分":$"已评为{rating}星";}else Feedback.Text=state.Feedback;
  }catch(Exception){if(!_disposed&&ReferenceEquals(state,_management))Feedback.Text="状态待核对";}
  finally{_annotationWriting=false;if(!_disposed){UpdateManagement();Changed?.Invoke();}}
 }
 internal void MarkAnnotationsDirty(){_catalogDirty=true;if(_active&&_model is not null)_=ReloadManaged(false);}
 private bool _catalogReloading;private int _reloadVersion;
 private async Task ReloadManaged(bool refreshCatalog=true)
 {
  var model=_model;if(model is null||!_active){_catalogDirty=true;return;}
  int version=++_reloadVersion;_catalogReloading=true;Update();
  try{
   if(refreshCatalog)await ReloadCatalog();if(!_active||!ReferenceEquals(model,_model)||version!=_reloadVersion)return;_catalogDirty=false;long? current=model.Current?.Id;long[] selected=model.SelectedIds.ToArray();bool inspector=_inspector;
   await model.RefreshAsync();if(!ReferenceEquals(model,_model)||_disposed||version!=_reloadVersion)return;
   var restored=model.Items.Where(i=>selected.Contains(i.Id)).Select(i=>i.Id).ToArray();
   _updating=true;try{ActiveImages.SelectedItems.Clear();foreach(var tile in _tiles)if(restored.Contains(tile.Item.Id))ActiveImages.SelectedItems.Add(tile);}finally{_updating=false;}
   model.SetSelection(restored);if(model.Items.FirstOrDefault(i=>i.Id==current) is{} item)await model.SelectAsync(item);
   if(version!=_reloadVersion)return;_inspector=inspector;Layout();if(current is not null&&model.Current is null)Feedback.Text="索引已更新；保留筛选与排序，已回到范围首页。";Changed?.Invoke();
  }finally{if(version==_reloadVersion){_catalogReloading=false;if(!_disposed)Update();}}
 }
 private bool _catalogDirty;
 private void SetRootOptions(System.Text.Json.JsonElement roots,long? selected)
 {
  RootsBox.Items.Clear();_rootIds.Clear();
  foreach(var root in roots.EnumerateArray().Take(200)){RootsBox.Items.Add(root.GetProperty("name").GetString());_rootIds.Add(root.GetProperty("id").GetInt64());}
  RootsBox.Items.Add("全部目录");_rootIds.Add(null);RootsBox.SelectedIndex=Math.Max(0,_rootIds.IndexOf(selected));
 }
 private async Task ReloadCatalog()
 {
  var client=_client;var model=_model;if(client is null||model is null)return;
  int version=++_facetRequestVersion;
  try{var roots=await client.CallAsync("catalog.roots",new{});var facets=await client.CallAsync("catalog.facets",new{});if(version!=_facetRequestVersion||!ReferenceEquals(model,_model)||!_active||_disposed)return;
   long? selected=_rootIds.ElementAtOrDefault(RootsBox.SelectedIndex);_updating=true;try{SetRootOptions(roots,selected);SetFilterFacets(facets);}finally{_updating=false;}
   Changed?.Invoke();
  }catch(Exception){if(version==_facetRequestVersion&&ReferenceEquals(model,_model)&&_active&&!_disposed)Feedback.Text="筛选摘要暂不可用，可重试刷新。";}
 }
 internal void RefreshManagedLibrary(){_catalogDirty=true;if(_active&&_model is not null)_=ReloadManaged();}
}
