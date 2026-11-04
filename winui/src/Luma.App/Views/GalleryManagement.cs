using Luma.Core.Gallery;
using Luma.Core.Management;
using Luma.Preview.Views.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
namespace Luma.Preview.Views;
public sealed partial class GalleryView
{
 internal nint WindowHandle{get;set;}
 public event Action<long[]>? EditTagsRequested;
 private ManagementState? _management;
 private Button? _rereadButton,_editTagsButton,_lookupModelsButton;
 private bool _annotationWriting;
 private TagChipPanel? _selectionActions;
 private long[] SelectedImageIds()=>_model?.SelectedIds.ToArray()??[];
 private Button ManageButton(string label,string id,Func<Task> action)
 {
  var b=new Button{Content=label,Style=(Style)Application.Current.Resources["LumaButton"]};AutomationProperties.SetAutomationId(b,id);
  b.Click+=async(_,_)=>{try{await action();}catch(Exception){Feedback.Text="操作失败";Changed?.Invoke();}};return b;
 }
 private void AddManagementControls()
 {
  var panel=ToolbarActions;_selectionActions=panel;
  var import=ManageButton("添加目录","GalleryImport",async()=>{var picker=new Microsoft.Windows.Storage.Pickers.FolderPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(WindowHandle)){CommitButtonText="添加"};var result=await picker.PickSingleFolderAsync();if(result is not null)await Submit("catalog.import",new{path=result.Path,recursive=true},false);else{Feedback.Text="已取消添加。";Changed?.Invoke();}});SearchRow.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});Grid.SetColumn(import,2);SearchRow.Children.Add(import);
  _editTagsButton=ManageButton("校正标签","GalleryEditTags",()=>{EditTagsRequested?.Invoke(SelectedImageIds());return Task.CompletedTask;});panel.Children.Add(_editTagsButton);
  _rereadButton=ManageButton("重读生成信息","GalleryReread",async()=>{var ids=SelectedImageIds();if(await ConfirmManage("重新读取生成信息",$"{ids.Length} 张图片"))await Submit("metadata.refresh",new{ids},false);});panel.Children.Add(_rereadButton);
  _lookupModelsButton=ManageButton("从 Civitai 查模型","GalleryLookupModels",async()=>{var ids=SelectedImageIds();if(ids.Length>60){Feedback.Text="每次模型查询最多60张，请缩小选择。";Changed?.Invoke();return;}if(await ConfirmManage("Civitai模型查询",$"{ids.Length} 张图片"))await Submit("models.lookup",new{ids},false);});panel.Children.Add(_lookupModelsButton);
  AllowDrop=true;DragOver+=(_,e)=>{if(e.DataView.Contains(StandardDataFormats.StorageItems))e.AcceptedOperation=DataPackageOperation.Copy;};Drop+=async(_,e)=>{var deferral=e.GetDeferral();try{if(!e.DataView.Contains(StandardDataFormats.StorageItems))return;var items=await e.DataView.GetStorageItemsAsync();if(items.Count==1&&items[0] is Windows.Storage.StorageFolder folder){await Submit("catalog.import",new{path=folder.Path,recursive=true},false);}else{Feedback.Text="请拖入一个目录。";Changed?.Invoke();}}catch(Exception){Feedback.Text="无法接收目录，请使用添加目录。";Changed?.Invoke();}finally{deferral.Complete();}};
 }
 private void UpdateManagement()
 {
  bool writable=_management is{Writing:false,Uncertain:false}&&!_annotationWriting&&!_catalogReloading;
  bool selected=SelectedImageIds().Length>0;bool enabled=writable&&selected;
  if(_selectionActions is not null)_selectionActions.Visibility=Visibility.Visible;
  ManagementActions.Visibility=Visibility.Collapsed;
  foreach(var tile in _tiles)tile.CanEdit=writable;
  foreach(var b in new[]{_rereadButton,_editTagsButton,_lookupModelsButton})if(b is not null){b.IsEnabled=enabled;ToolTipService.SetToolTip(b,selected?b.Content:"先选择图片");}
  if(_lookupModelsButton is not null)_lookupModelsButton.IsEnabled=enabled&&SelectedImageIds().Length<=60;
  CancelButton.IsEnabled=_model?.Loading==true||_galleryJob.CanCancel;CancelButton.Content="取消";ToolTipService.SetToolTip(CancelButton,_galleryJob.CanCancel?"取消正在处理的后台操作":"取消图片列表加载");
  Next.IsEnabled=!_annotationWriting&&!_catalogReloading&&_model?.CanNext==true;Previous.IsEnabled=!_annotationWriting&&!_catalogReloading&&_model?.CanPrevious==true;
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
  try{var roots=await client.CallAsync("catalog.roots",new{});var facets=await client.CallAsync("catalog.facets",new{});if(!ReferenceEquals(model,_model)||!_active||_disposed)return;
   long? selected=_rootIds.ElementAtOrDefault(RootsBox.SelectedIndex);_updating=true;try{SetRootOptions(roots,selected);_facets=facets;UpdateFilterValues();}finally{_updating=false;}
  }catch(Exception){Feedback.Text="筛选摘要暂不可用，可重试刷新。";}
 }
 internal void RefreshManagedLibrary(){_catalogDirty=true;if(_active&&_model is not null)_=ReloadManaged();}
}
