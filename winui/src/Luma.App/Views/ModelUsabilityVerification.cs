using System.Text.Json;
using Luma.Core.Management;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Views;

// Opt-in native interaction probes. All inputs are inert files created inside the
// new diagnostic profile; no user model directory or remote metadata is opened.
public sealed partial class ShellView
{
 internal async Task VerifyModelUsabilityUi(string directory,Func<Task> capture)
 {
  if(!_diagnostics||Environment.GetEnvironmentVariable("LUMA_MODEL_USABILITY_UI_TEST")!="1")return;
  var checks=new List<object>();
  async Task Until(Func<bool> predicate){for(int i=0;i<200;i++){if(predicate())return;await Task.Delay(50);}throw new TimeoutException("Own model usability fixture timed out");}
  async Task Capture(string label){await capture();string frame=Directory.EnumerateFiles(directory,"capture-*.png").MaxBy(File.GetLastWriteTimeUtc)??throw new InvalidOperationException("Own frame missing");File.Copy(frame,Path.Combine(directory,"model-"+label+".png"),true);}
  try{
   Navigate(WorkspacePage.Models);await Until(()=>_engine?.Connected==true&&_managementState is not null);
   string models=Path.Combine(directory,"model-usability-fixtures","演示目录");Directory.CreateDirectory(models);
   for(int i=0;i<67;i++)
   {
    string stem=$"fixture-{i:000}"+(i==9?"-Aurora-Character-Portrait-Detail-XL":"");
    await File.WriteAllTextAsync(Path.Combine(models,stem+".safetensors"),$"inert model fixture {i}");
    if(i<4)await SyntheticPng(Path.Combine(models,stem+".preview.png"),i+2);
   }
   var api=new ManagementApi(_engine!.Client!);
   var added=await api.WriteAsync("models.add",new{path=models},Guid.NewGuid().ToString("N"),CancellationToken.None);
   long root=added.GetProperty("id").GetInt64();
   await api.WriteAsync("models.scan",new{rootId=root},Guid.NewGuid().ToString("N"),CancellationToken.None);
   for(int i=0;i<200;i++){
    var page=await api.ReadAsync("models",new{},default);
    if(page.GetProperty("total").GetInt64()==67)break;
    await Task.Delay(50);
   }
   await Management.RefreshForReleaseVerification();
   await Management.VerifyModelUsabilityUi(checks,Capture,_navigation[WorkspacePage.Models]);
   string first=Path.Combine(models,"fixture-000");
   await File.WriteAllTextAsync(first+".civitai.info",JsonSerializer.Serialize(new{id=31,modelId=17,modelName="Fixture",model=new{name="Fixture",type="LORA"}}));
   await File.WriteAllTextAsync(first+".cm-info.json",JsonSerializer.Serialize(new{ModelId=17,VersionId=31,ModelName="Fixture",ModelType="LORA"}));
   await File.WriteAllTextAsync(first+".json",JsonSerializer.Serialize(new{description="Synthetic model notes",modelVersionId=31}));
   await SyntheticPng(first+".preview.png",0);await SyntheticPng(first+".png",0);
   var tidy=await api.ReadAsync("modelInfoCleanupPreview",new{rootId=root},default);
   await Management.VerifyCompanionTidyUi(checks,Capture,tidy);
   File.WriteAllText(Path.Combine(directory,"model-usability-ui-report.json"),JsonSerializer.Serialize(new{result=checks.All(c=>JsonSerializer.SerializeToElement(c).GetProperty("passed").GetBoolean())?"passed_model_usability_ui":"failed_model_usability_ui",checks,privateDataRead=false,networkUsed=false}));
  }catch(Exception error){File.WriteAllText(Path.Combine(directory,"model-usability-ui-report.json"),JsonSerializer.Serialize(new{result="failed_model_usability_ui",checks,error=error.ToString(),privateDataRead=false,networkUsed=false}));}
 }
}

public sealed partial class ManagementView
{
 internal async Task VerifyCompanionTidyUi(List<object> checks,Func<string,Task> capture,JsonElement preview)
 {
  var (sync,refresh)=ModelInformationDialog(CurrentModelSyncRequest());var shown=sync.ShowAsync();
  try{await Task.Delay(250);bool one=sync.Content is StackPanel options&&options.Children.OfType<CheckBox>().Count()==1;
   checks.Add(new{name="native sync has no redundant compatibility export option",passed=one&&!refresh.IsChecked.GetValueOrDefault()});
   var request=JsonSerializer.SerializeToElement(CurrentModelSyncRequest(true).Args);checks.Add(new{name="native sync does not request extra companion formats",passed=!request.GetProperty("compatibility").GetBoolean()});
  }finally{sync.Hide();await shown;}
  await Task.Delay(300);var dialog=Confirmation("整理资料","");dialog.Content=ModelInfoCleanupContent(preview);dialog.PrimaryButtonText="确认整理";shown=dialog.ShowAsync();
  try{await Task.Delay(400);
   var content=(StackPanel)dialog.Content;var list=(StackPanel)content.Children.OfType<ScrollViewer>().Single().Content;
   string[] names=list.Children.OfType<TextBlock>().Select(t=>t.Text).ToArray();
   bool correct=N(preview,"count")==3&&names.Contains("保留")&&names.Contains("收起")&&names.Contains("fixture-000.civitai.info")&&names.Contains("fixture-000.preview.png")&&names.Contains("fixture-000.cm-info.json")&&names.Contains("fixture-000.json")&&names.Contains("fixture-000.png");
   checks.Add(new{name="native review separates two surviving files and three redundant companions",passed=correct});
   var scroll=content.Children.OfType<ScrollViewer>().Single();checks.Add(new{name="five-file review fits fully without hidden rows at configured text scale",passed=scroll.ExtentHeight<=scroll.ViewportHeight+1});await capture("companion-review");
  }finally{dialog.Hide();await shown;}
 }
 internal async Task VerifyModelUsabilityUi(List<object> checks,Func<string,Task> capture,Control outside)
 {
  async Task Until(Func<bool> predicate){for(int i=0;i<200;i++){if(predicate())return;await Task.Delay(50);}throw new TimeoutException("Own model interaction timed out");}
  void Check(bool value,string message){if(!value)throw new InvalidOperationException(message);}
  async Task Case(string name,Func<Task> action){try{await action();checks.Add(new{name,passed=true});}catch(Exception error){checks.Add(new{name,passed=false,error=error.Message});}finally{await Until(()=>!_loading&&!_formLoading);}}
  object? Focused()=>FocusManager.GetFocusedElement(XamlRoot);
  async Task Reset(){await Until(()=>!_loading&&!_formLoading);_modelSearch!.Text="";await SearchModels();await Until(()=>!_loading&&!_formLoading);UpdateLayout();}
  await Until(()=>_visible&&_page=="Models"&&!_loading&&!_formLoading&&_modelTotal==67);

  await Case("typing stays enabled and focused while a result read is pending",async()=>{
   var search=_modelSearch!;Check(search.Focus(FocusState.Keyboard),"search must accept initial keyboard focus");search.Text="fixture";search.Select(3,2);
   Task pending=SearchModels();
   bool interactive=search.IsEnabled&&ReferenceEquals(Focused(),search);
   await pending;
   Check(interactive,"pending model read disabled the typing field or evicted keyboard focus");
   Check(ReferenceEquals(Focused(),_modelSearch)&&_modelSearch!.SelectionStart==3&&_modelSearch.SelectionLength==2,"result rendering must retain typing focus and selection");await capture("typing-focus");
  });
  await Reset();
  await Case("edits made during an older read survive and query the newest text",async()=>{
   var search=_modelSearch!;search.Focus(FocusState.Keyboard);search.Text="fixture-0";Task pending=SearchModels();
   search.Text="fixture-006";search.Select(8,2);await pending;
   await Until(()=>!_loading&&!_formLoading&&_query=="fixture-006");
   Check(_modelSearch!.Text=="fixture-006","an older result discarded the latest draft");
   Check(ReferenceEquals(Focused(),_modelSearch)&&_modelSearch.SelectionStart==8&&_modelSearch.SelectionLength==2,"the newest draft must retain its caret and selection");
   Check(_modelTotal==1&&S(_models.Single(),"name").Contains("fixture-006",StringComparison.Ordinal),"newest text must determine the visible model result");await capture("latest-draft");
  });
  await Reset();
  await Case("completed reads do not reclaim focus after the user moves away",async()=>{
   _modelSearch!.Focus(FocusState.Keyboard);_modelSearch.Text="fixture-01";Task pending=SearchModels();
   Check(outside.Focus(FocusState.Keyboard),"navigation must accept focus while the read is pending");await pending;await Task.Delay(300);
   Check(ReferenceEquals(Focused(),outside),"result rendering must not steal focus back to search");
  });
  await Reset();
  await Case("model filter keyboard focus survives a result refresh",async()=>{
   var filter=ModelControls.Children.OfType<Panel>().SelectMany(p=>p.Children.OfType<Control>()).Single(c=>AutomationProperties.GetAutomationId(c)=="ManageFiltertypesAll");
   Check(filter.Focus(FocusState.Keyboard),"type filter must accept keyboard focus");await RefreshPage();
   Check(ReferenceEquals(Focused(),filter),"refreshing model results must preserve the focused filter");
  });
  await Reset();
  await Case("same workspace reactivation applies a draft hidden before debounce",async()=>{
   Check(outside.Focus(FocusState.Keyboard),"navigation must accept focus before hiding");
   _modelSearch!.Text="fixture-006";_modelSearch.Select(8,2);Hide();ShowPage("Models");
   await Until(()=>!_loading&&!_formLoading);
   Check(_modelSearch!.Text=="fixture-006"&&_query=="fixture-006","reactivated search text and applied query must agree");
   Check(_modelTotal==1&&S(_models.Single(),"name").Contains("fixture-006",StringComparison.Ordinal),"reactivation must display the hidden draft's model result");
   Check(ReferenceEquals(Focused(),outside),"reactivation must not reclaim keyboard focus");
  });
  await Reset();
  await Case("viewer return applies a draft hidden before debounce",async()=>{
   Check(outside.Focus(FocusState.Keyboard),"navigation must accept focus before viewer suspension");
   _modelSearch!.Text="fixture-007";SuspendForViewer();ResumeFromViewer();
   await Until(()=>!_loading&&!_formLoading);
   Check(_modelSearch!.Text=="fixture-007"&&_query=="fixture-007","viewer return must apply the retained search draft");
   Check(_modelTotal==1&&S(_models.Single(),"name").Contains("fixture-007",StringComparison.Ordinal),"viewer return must display the hidden draft's model result");
   Check(ReferenceEquals(Focused(),outside),"viewer return must not reclaim keyboard focus");
  });
  await Reset();
  await Case("changing management pages resets the model search draft",async()=>{
   _modelSearch!.Text="fixture-008";Hide();ShowPage("Jobs");await Until(()=>!_loading&&!_formLoading);ShowPage("Models");
   await Until(()=>!_loading&&!_formLoading);
   Check(_modelSearch!.Text==""&&_query==""&&_modelTotal==67,"a changed management page must retain the normal cleared-search behavior");
  });
  await Reset();
  await Case("idle model layout uses the available grid width and reclaims the inactive task row",async()=>{
   UpdateLayout();await Until(()=>ModelCards.ItemsPanelRoot is ItemsWrapGrid{ItemWidth: >0}&&_modelProgressRow is not null);
   var panel=(ItemsWrapGrid)ModelCards.ItemsPanelRoot;
   double used=panel.ItemWidth*panel.MaximumRowsOrColumns;
   Check(_modelProgressRow!.Visibility==Visibility.Collapsed,"inactive task row is consuming space");
   Check(used<=ModelCards.ActualWidth+1&&ModelCards.ActualWidth-used<=20,"model columns leave an unused strip or overflow");
   var first=ModelCards.Items.OfType<ManagementRow>().First();
   Check(first.CardTitle=="fixture-000"&&first.Label=="fixture-000.safetensors","card title must be concise while retaining its real filename");
   await capture("grid-wide");
  });
  await Case("all directories label fits the compact toolbar at the configured text scale",async()=>{
   double previous=MaxWidth;try{
    MaxWidth=620;UpdateLayout();LayoutModels();UpdateLayout();await Task.Delay(100);
    _rootChoice!.SelectedIndex=_roots.Length;UpdateLayout();
    IEnumerable<FrameworkElement> Descendants(DependencyObject node){for(int i=0;i<VisualTreeHelper.GetChildrenCount(node);i++){var child=VisualTreeHelper.GetChild(node,i);if(child is FrameworkElement element)yield return element;foreach(var nested in Descendants(child))yield return nested;}}
    var presenter=Descendants(_rootChoice).OfType<ContentPresenter>().Single(p=>p.Name=="ContentPresenter");
    var label=new TextBlock{Text="全部目录",FontFamily=_rootChoice.FontFamily,FontSize=_rootChoice.FontSize};label.Measure(new(double.PositiveInfinity,double.PositiveInfinity));
    Check(presenter.ActualWidth+1>=label.DesiredSize.Width,$"directory label clipped: available {presenter.ActualWidth:0.0}, required {label.DesiredSize.Width:0.0}");
    var bounds=_rootChoice.TransformToVisual(ModelControlScroll).TransformBounds(new(0,0,_rootChoice.ActualWidth,_rootChoice.ActualHeight));
    Check(bounds.Left>=0&&bounds.Right<=ModelControlScroll.ActualWidth+1,"directory field must fit inside the toolbar");await capture("directory-compact");
    _rootChoice.SelectedIndex=0;await Task.Delay(150);await Until(()=>!_loading&&!_formLoading);UpdateLayout();
    var selectedLabel=new TextBlock{Text=_rootChoice.SelectedItem!.ToString(),FontFamily=_rootChoice.FontFamily,FontSize=_rootChoice.FontSize};selectedLabel.Measure(new(double.PositiveInfinity,double.PositiveInfinity));
    Check(presenter.ActualWidth+1>=selectedLabel.DesiredSize.Width,"a normal directory name and model count must fit the selected field");await capture("directory-selected");
   }finally{MaxWidth=previous;UpdateLayout();LayoutModels();}
  });
 }
}
