using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;
using Luma.Preview.ViewModels;
namespace Luma.Preview.Views;

// Intrinsic integration probe, enabled only for a fresh --diagnostics profile.
public sealed partial class ShellView
{
 internal async Task VerifyReleaseUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_RELEASE_UI_TEST")!="1")return;
  var checks=new List<object>();var frames=new List<object>();
  void Check(bool passed,string name)=>checks.Add(new{passed,name});
  static T? Element<T>(DependencyObject root,string id) where T:FrameworkElement{
   if(root is UIElement{Visibility:Visibility.Collapsed})return null;
   if(root is T item&&AutomationProperties.GetAutomationId(item)==id)return item;
   for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++)if(Element<T>(VisualTreeHelper.GetChild(root,i),id) is{} found)return found;
   return null;
  }
  async Task<T> Wait<T>(string id) where T:FrameworkElement{
   for(int i=0;i<150;i++){if(Element<T>(this,id) is{IsLoaded:true} item&&item.ActualHeight>0)return item;await Task.Delay(100);}
   throw new InvalidOperationException("Missing own control "+id);
  }
  Rect Bounds(FrameworkElement element)=>element.TransformToVisual(XamlRoot.Content).TransformBounds(new(0,0,element.ActualWidth,element.ActualHeight));
  async Task CaptureNamed(string name)
  {
   Changed?.Invoke();await capture();
   string frame=Directory.EnumerateFiles(directory,"capture-*.png").MaxBy(File.GetLastWriteTimeUtc)
    ?? throw new InvalidOperationException("Own workspace capture missing");
   File.Copy(frame,Path.Combine(directory,name+".png"),true);
  }
  try{
   Navigate(WorkspacePage.Models);await Wait<ComboBox>("ManageModelRoots");
   for(int i=0;i<150&&(_engine?.Connected!=true||_managementState is null);i++)await Task.Delay(100);
   if(_managementState is null)throw new InvalidOperationException("Own service not connected");
   foreach(string name in new[]{"models-a","models-b"}){string root=Path.Combine(directory+"-fixtures",name);Directory.CreateDirectory(root);if(!await _managementState.WriteAsync("models.add",new{path=root}))throw new InvalidOperationException("Own model root fixture failed");}
   await Management.RefreshForReleaseVerification();UpdateLayout();var choice=await Wait<ComboBox>("ManageModelRoots");
   var search=await Wait<TextBox>("ManageModelSearch");Check(Math.Abs(choice.ActualHeight-search.ActualHeight)<1,"model search and directory heights match");
   bool readyOnOpen=false;
   void Opening(object? s,object e){var pop=Services.DropdownLayout.Find<Popup>(choice,"Popup");readyOnOpen=pop is not null&&pop.VerticalOffset>=choice.ActualHeight&&Services.DropdownLayout.Find<TextBlock>(choice,"OpenSelectionText") is{Visibility:Visibility.Visible,Text.Length:>0};}
   void Frame(object? s,object e){
    if(!choice.IsDropDownOpen)return;
    var pop=Services.DropdownLayout.Find<Popup>(choice,"Popup");var border=pop?.Child is{} child?Services.DropdownLayout.Find<Border>(child,"PopupBorder"):null;
    if(border is null||border.ActualHeight<1)return;
    var label=Services.DropdownLayout.Find<TextBlock>(choice,"OpenSelectionText");var field=Bounds(choice);var panel=Bounds(border);
    frames.Add(new{fieldBottom=field.Bottom,panelTop=panel.Top,fieldTextVisible=label?.Visibility==Visibility.Visible&&label.Text.Length>0});
   }
   choice.DropDownOpened+=Opening;CompositionTarget.Rendering+=Frame;
   try{choice.IsDropDownOpen=true;await Task.Delay(450);Check(readyOnOpen,"dropdown position and field text ready synchronously on open");Check(frames.Count>0,"captured native rendering frames");Check(frames.All(f=>{var j=JsonSerializer.SerializeToElement(f);return j.GetProperty("panelTop").GetDouble()>=j.GetProperty("fieldBottom").GetDouble()&&j.GetProperty("fieldTextVisible").GetBoolean();}),"all sampled open frames keep panel below and field visible");Changed?.Invoke();await capture();}
   finally{choice.IsDropDownOpen=false;choice.DropDownOpened-=Opening;CompositionTarget.Rendering-=Frame;}
   Navigate(WorkspacePage.Settings);var token=await Wait<PasswordBox>("ManageApiToken");var density=await Wait<ComboBox>("ManagePreferenceDensity");
   var tokenBounds=Bounds(token);var densityBounds=Bounds(density);Check(Math.Abs(tokenBounds.Left-densityBounds.Left)<1,"credential and preference fields share left edge");Check(Math.Abs(tokenBounds.Width-densityBounds.Width)<1,"credential and preference fields share width");
   token.StartBringIntoView(new BringIntoViewOptions{AnimationDesired=false,VerticalAlignmentRatio=.45});await Task.Delay(150);Changed?.Invoke();await capture();
   var registry=await Wait<Button>("ManageRegistry");registry.Focus(FocusState.Programmatic);await Task.Delay(150);var settingsScroll=await Wait<ScrollViewer>("ManageFormScroll");var rb=Bounds(registry);var sb=Bounds(settingsScroll);Check(rb.Top>=sb.Top&&rb.Bottom<=sb.Bottom,"lower settings action scrolls fully into view");
   Check(Element<Button>(this,"ManageRefresh") is null&&Element<Button>(this,"SettingsStorage") is null,
    "settings has no duplicate refresh or data navigation");
   string duplicateRoot=Path.Combine(directory+"-fixtures","duplicates");
   Directory.CreateDirectory(duplicateRoot);
   string first=Path.Combine(duplicateRoot,"sample-a.png"),second=Path.Combine(duplicateRoot,"sample-b.png");
   await SyntheticPng(first,3);File.Copy(first,second);
   if(!await _managementState.WriteAsync("catalog.import",new{path=duplicateRoot,recursive=true}))
    throw new InvalidOperationException("Own duplicate fixture import failed");
   var api=new Luma.Core.Management.ManagementApi(_engine!.Client!);
   for(int i=0;;i++){
    var fileState=await api.ReadAsync("files.state",new{},default);
    if(fileState.GetProperty("roots").EnumerateArray().Any(r=>r.GetProperty("name").GetString()=="duplicates"&&r.GetProperty("count").GetInt64()==2))break;
    if(i>=149)throw new TimeoutException("Own duplicate fixture index missing");
    await Task.Delay(60);
   }
   Navigate(WorkspacePage.Organize);await Wait<ComboBox>("FilesClassification");
   Check(Workspace.Background is SolidColorBrush body&&RailTint.Background is SolidColorBrush side&&
    ((body.Opacity<=.15&&side.Opacity<=.15)||(body.Opacity==1&&side.Opacity==1))&&Workspace.BorderThickness.Left==0,
    "navigation and workspace share the native material without a separate opaque frame");
   UpdateLayout();
   Check(Rail.BorderThickness==new Thickness(0)&&SettingsNavigation.Children.Count==1&&Element<Button>(this,"OpenImage") is null,
    "sidebar has no divider or extra button next to Settings");
   Check(Rail.ActualWidth<=88.5&&_navigation.Values.All(button=>{
    if(button.Content is not StackPanel label||label.ActualWidth<1)return false;
    var b=Bounds(button);var c=Bounds(label);
    return Math.Abs(b.Width-b.Height)<1&&Math.Abs((b.Left+b.Right)-(c.Left+c.Right))<2&&
     Math.Abs((b.Top+b.Bottom)-(c.Top+c.Bottom))<2&&label.Orientation==Orientation.Vertical&&
     Services.DropdownLayout.Find<Border>(button,"GlassShine") is{Opacity:>0};
   }),"all sidebar items use centered square glass tiles in a rail at most 88 DIP wide");
   var draft=await Wait<Button>("FilesGenerate");Check(!draft.IsEnabled,"classification waits for a concrete destination and scope");
   await CaptureNamed("workspace-classification");
   await Files.VerifyClassificationUi(Check,directory);await CaptureNamed("workspace-rules");
   Navigate(WorkspacePage.Duplicates);await Wait<Button>("FilesScan");
   await Files.VerifyCleanupFlowUi(Check,()=>CaptureNamed("workspace-cleanup"),first,second);
   Navigate(WorkspacePage.Organize);await Wait<ComboBox>("FilesClassification");
   Check(Files.IsReviewClearedForVerification,"changing workspaces cannot carry a cleanup preview into classification");
   Navigate(WorkspacePage.Gallery);var gallerySearch=await Wait<TextBox>("GallerySearch");var gallerySort=await Wait<ComboBox>("GallerySort");Check(Math.Abs(gallerySearch.ActualHeight-gallerySort.ActualHeight)<1,"gallery search and sorting heights match");
   Check(gallerySearch.ActualHeight>=36,"input height is at least 36");await Task.Delay(150);await CaptureNamed("workspace-gallery");
   await LiveGallery.VerifyFilterPanelUi(Check,()=>CaptureNamed("workspace-filter"));
   long selectedId=await LiveGallery.VerifyGalleryChromeUi(Check);
   Navigate(WorkspacePage.Tags);await Wait<ListView>("ManageList");
   await Management.VerifyTagsUi(Check,()=>CaptureNamed("workspace-tags"),selectedId);
  }catch(Exception error){checks.Add(new{passed=false,name="probe completed",error=error.GetType().Name+": "+error.Message});}
  bool passed=checks.All(c=>JsonSerializer.SerializeToElement(c).GetProperty("passed").GetBoolean());
  await File.WriteAllTextAsync(Path.Combine(directory,"ui-release-report.json"),JsonSerializer.Serialize(new{result=passed?"passed_native_release_ui":"failed_native_release_ui",checks,frames,scope="own fresh diagnostic profile and temporary control fixtures; intrinsic events and actual XAML Rendering samples; no OS click automation or user library"}));
 }
}

public sealed partial class GalleryView
{
 internal async Task<long> VerifyGalleryChromeUi(Action<bool,string> check)
 {
  for(int i=0;i<120&&(_model?.Loading!=false||_tiles.Count==0);i++)await Task.Delay(50);
  UpdateLayout();
  check(PageButtons.Visibility==Visibility.Collapsed&&ToolbarActions.Children.Count==5,
   "gallery uses one tool row and omits the pager for one page");
  check(AutomationProperties.GetName(RefreshButton)=="刷新"&&RefreshButton.IsEnabled,
   "idle gallery keeps one refresh slot and no abandoned cancel action");
  if(_tiles.FirstOrDefault() is not{} tile||ActiveImages.ContainerFromItem(tile) is not ContentControl item)
   throw new InvalidOperationException("Own synthetic gallery tile was not realized");
  ShowImageMenu(item,tile,null);
  try{
   for(int i=0;i<40&&!_menu.IsOpen;i++)await Task.Delay(50);
   check(SelectedImageIds().SequenceEqual(new long[]{tile.Item.Id}),"opening image actions preserves the selected image");
   check(_menu.IsOpen,"selected image context menu opens");
   check(_menu.Items.OfType<MenuFlyoutItem>().Count(command=>AutomationProperties.GetAutomationId(command) is "GalleryReread" or "GalleryLookupModels")==2,
    "image metadata commands exist only once in the context menu");
  }
  finally{_menu.Hide();}
  return tile.Item.Id;
 }
}

public sealed partial class ManagementView
{
 internal Task RefreshForReleaseVerification()=>RefreshFromStart();
}

public sealed partial class FilesView
{
 internal bool IsReviewClearedForVerification=>_reviewId is null&&_review.Review is null&&!CanConfirmCurrentReview;
 internal async Task VerifyCleanupFlowUi(Action<bool,string> check,Func<Task> capture,string first,string second)
 {
  async Task Until(Func<bool> ready)
  {
   for(int i=0;i<150;i++){if(ready())return;await Task.Delay(60);}
   throw new TimeoutException("Own cleanup fixture did not reach the next phase");
  }
  await Scan();await Until(()=>_groups.Length==1&&_members.Length==2);
  check(_visible&&_mode=="duplicates","a direct duplicate scan keeps results in cleanup");
  check(_members.Count(m=>B(m,"is_keep"))==1&&Entries.Items.OfType<FileRow>().Count(row=>row.Label.StartsWith("保留"))==1,
   "duplicate members display exactly one keeper from the engine flag");
  await SelectAll(true);
  check(_groups.All(g=>B(g,"selected")),"group selection reflects the saved engine flags");
  await Generate("duplicate_cleanup");
  await Until(()=>CanConfirmCurrentReview);
  check(_review.Review?.Actionable==1&&File.Exists(first)&&File.Exists(second),
   "duplicate preview stays non-destructive and reaches the explicit confirmation");
  check(_execute is {Visibility:Visibility.Visible,IsEnabled:true} && _generate is {Visibility:Visibility.Collapsed},
   "preview and confirm share one action slot");
  check(WorkbenchControls.Visibility==Visibility.Collapsed&&Summary.Text.Contains("整个图库"),
   "cleanup review shows its saved scope without an unrelated scan selector");
  var entry=Entries.Items.OfType<FileRow>().SingleOrDefault();
  check(entry?.Label.Contains("隔离 · sample-",StringComparison.Ordinal)==true&&
   entry.Label.Contains("保留 · sample-",StringComparison.Ordinal)&&
   Path.IsPathFullyQualified(S(entry.Value,"source"))&&Path.IsPathFullyQualified(S(entry.Value,"keep_path")),
   "cleanup preview leads with filenames and retains full paths for inspection");
  await capture();
 }

 internal async Task VerifyClassificationUi(Action<bool,string> check,string directory)
 {
  await SetMode("organize");
  string restoredId="own-restore-layout-fixture";_reviewId=restoredId;long version=_review.Version;
  RestoreOptions(JsonSerializer.SerializeToElement(new{kind="organize",options=new{mode="format",scope="all",destination=Path.Combine(directory+"-fixtures","organized")}}));await Task.Delay(100);
  check(_reviewId==restoredId&&_review.Version==version,"restoring preview fields preserves review identity");_reviewId=null;_review.Invalidate();
  await SetMode("rules");
  _rules=Rows(JsonSerializer.SerializeToElement(new[]{new{id="fixture-rule-landscape",name="风景",kind="content_tag",match="Landscape",destination="",enabled=true},new{id="fixture-rule-format",name="PNG",kind="format",match="png",destination="",enabled=true}}));
  Build();await Task.Delay(150);
  var list=FindRuleList(this);
  check(list is not null,"classification rule list exists");
  if(list is not null){var bounds=list.TransformToVisual(this).TransformBounds(new(0,0,list.ActualWidth,list.ActualHeight));var setup=SetupScroll.TransformToVisual(this).TransformBounds(new(0,0,SetupScroll.ActualWidth,SetupScroll.ActualHeight));check(bounds.Left>=setup.Right,"rule list uses main content area");check(list.ActualHeight>130,"rule list fills available height");list.SelectedIndex=0;check(_ruleName?.Text=="风景"&&_ruleMatch?.Text=="Landscape","rule selection fills editor");}
  check(Actions.Visibility==Visibility.Collapsed||!Actions.Children.OfType<Button>().Any(b=>AutomationProperties.GetAutomationId(b)=="FilesGenerate"),"rule mode hides unrelated file preview actions");
  check(PageActions.Visibility==Visibility.Collapsed,"rule mode hides file path tools");
  var actions=RuleActions.TransformToVisual(this).TransformBounds(new(0,0,RuleActions.ActualWidth,RuleActions.ActualHeight));check(actions.Bottom<=ActualHeight&&RuleActions.Children.OfType<Button>().All(b=>b.ActualHeight>=36),"rule editing actions remain fully visible outside scroll area");
  double oldWidth=Width;
  try{Width=560;UpdateLayout();await Task.Delay(100);var setup=SetupPane.TransformToVisual(this).TransformBounds(new(0,0,SetupPane.ActualWidth,SetupPane.ActualHeight));var preview=PreviewPane.TransformToVisual(this).TransformBounds(new(0,0,PreviewPane.ActualWidth,PreviewPane.ActualHeight));check(preview.Left>=setup.Right&&RulesList.ActualHeight>130,"small desktop classification keeps full-height columns");Width=480;UpdateLayout();await Task.Delay(100);setup=SetupPane.TransformToVisual(this).TransformBounds(new(0,0,SetupPane.ActualWidth,SetupPane.ActualHeight));preview=PreviewPane.TransformToVisual(this).TransformBounds(new(0,0,PreviewPane.ActualWidth,PreviewPane.ActualHeight));check(preview.Top>=setup.Bottom,"compact classification stacks without overlap");check(PreviewPane.ActualWidth<=ActualWidth&&RulesList.ActualHeight>40,"compact rule list remains visible");}
  finally{Width=oldWidth;UpdateLayout();}
  static ListView? FindRuleList(DependencyObject root){if(root is ListView list&&AutomationProperties.GetAutomationId(list)=="FilesRules")return list;for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++)if(FindRuleList(VisualTreeHelper.GetChild(root,i)) is{} found)return found;return null;}
 }
}
