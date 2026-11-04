using System.Text.Json;
using Luma.Core.Storage;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Luma.Preview.Services;
using Luma.Preview.Views.Controls;

namespace Luma.Preview.Views;

public sealed class StorageView:UserControl
{
    private readonly StackPanel _body=new(){Spacing=14};
    private readonly StackPanel _previewArea=new(){Spacing=8,Visibility=Visibility.Collapsed};
    private readonly TextBlock _location=new(){TextWrapping=TextWrapping.Wrap,IsTextSelectionEnabled=true};
    private readonly TextBlock _status=new(){TextWrapping=TextWrapping.Wrap,IsTextSelectionEnabled=true};
    private readonly ListView _preview=new(){Height=128,Padding=new Thickness(0),BorderThickness=new Thickness(0),SelectionMode=ListViewSelectionMode.Single};
    private readonly TextBox _fullPath=new(){Header="完整路径",IsReadOnly=true,AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,MaxHeight=92};
    private readonly TextBox _snapshot=new(){Header="备份目录",MaxLength=1024};
    private readonly List<Button> _buttons=[];
    private string _profile="",_data="";private EngineSession? _engine;
    private SnapshotPlan? _backupPlan;private string? _cacheReview,_cacheReviewHash;
    private CancellationTokenSource? _cancel;private Task? _operation;
    private ContentDialog? _dialog;
    private bool _restart;
    private string _previewRoot="";
    internal nint WindowHandle{get;set;}
    public bool Busy{get;private set;}
    public event Action? Changed;
    public object Snapshot=>new{busy=Busy,restartRequired=_restart,backupReady=_backupPlan is not null,cacheReady=_cacheReview is not null,feedback=_status.Text};

    public StorageView()
    {
        var root=new Grid{RowSpacing=12};root.RowDefinitions.Add(new(){Height=new GridLength(1,GridUnitType.Star)});root.RowDefinitions.Add(new(){Height=GridLength.Auto});
        root.Children.Add(new ScrollViewer{Content=_body,VerticalScrollBarVisibility=ScrollBarVisibility.Visible,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled});
        Grid.SetRow(_status,1);root.Children.Add(_status);Content=root;
        AutomationProperties.SetAutomationId(_preview,"StorageFilePreview");AutomationProperties.SetName(_preview,"当前操作的具体文件预览");
        _preview.ItemTemplate=(DataTemplate)Microsoft.UI.Xaml.Markup.XamlReader.Load("""
        <DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"><TextBlock Text="{Binding}" TextWrapping="Wrap" MaxLines="2" TextTrimming="CharacterEllipsis" ToolTipService.ToolTip="{Binding}"/></DataTemplate>
        """);
        _preview.ItemContainerStyle=new Style(typeof(ListViewItem));_preview.ItemContainerStyle.Setters.Add(new Setter(FrameworkElement.HeightProperty,64d));_preview.ItemContainerStyle.Setters.Add(new Setter(FrameworkElement.MinHeightProperty,64d));_preview.ItemContainerStyle.Setters.Add(new Setter(Control.PaddingProperty,new Thickness(8,4,16,4)));_preview.ItemContainerStyle.Setters.Add(new Setter(Control.HorizontalContentAlignmentProperty,HorizontalAlignment.Stretch));
        _preview.SelectionChanged+=(_,_)=>_fullPath.Text=_preview.SelectedItem is string path?Path.Combine(_previewRoot,path):"";
        ScrollViewer.SetVerticalScrollBarVisibility(_preview,ScrollBarVisibility.Visible);
        AutomationProperties.SetAutomationId(_fullPath,"StorageFullPath");AutomationProperties.SetName(_fullPath,"所选文件完整路径");
        Heading("位置");_body.Children.Add(_location);

        Buttons(Add("断开图库","StorageDisconnect",async()=>{if(_engine is not null)await _engine.StopAsync();_status.Text="已断开";}));
        Heading("备份与还原");

        Buttons(Add("预览备份","StorageBackupPreview",async()=>{await Offline();ResetPlans();_backupPlan=await Task.Run(()=>ProfileSnapshots.Preview(_profile,Token));ShowFiles(_backupPlan.Files.Select(f=>f.RelativePath),_backupPlan.Source);_status.Text=$"备份预览：{_backupPlan.Files.Count}份记录，{Size(_backupPlan.Bytes)}";}),
            Add("创建备份","StorageBackupCreate",async()=>{await Offline();if(_backupPlan is null)throw new IOException("先点击预览备份。");string path=Path.Combine(_data,"Backups","snapshot-"+Guid.NewGuid().ToString("N"));_snapshot.Text=await Task.Run(()=>ProfileSnapshots.Create(_backupPlan,path,Token));_status.Text="备份已保存并通过逐文件校验。";_backupPlan=null;}));
        _body.Children.Add(_snapshot);AutomationProperties.SetAutomationId(_snapshot,"StorageSnapshotPath");AutomationProperties.SetName(_snapshot,"备份目录");
        _snapshot.PlaceholderText="选择或粘贴已创建的备份目录";
        Buttons(Add("选择备份目录","StoragePickSnapshot",async()=>{var picker=new Microsoft.Windows.Storage.Pickers.FolderPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(WindowHandle));var result=await picker.PickSingleFolderAsync();if(result is not null){_snapshot.Text=result.Path;_status.Text="备份目录已选择；核验并确认后才还原。";}else _status.Text="已取消选择目录。";}),Add("核验并还原","StorageRestore",async()=>{await Offline();string source=_snapshot.Text.Trim();var plan=await Task.Run(()=>ProfileSnapshots.Verify(source,Token));if(!await Confirm("还原到新图库",$"已核验{plan.Files.Count}份记录，{Size(plan.Bytes)}"))return;string restored=await Task.Run(()=>ProfileSnapshots.Restore(source,_data,Token));Restart(restored);}));
        Heading("缓存");

        Buttons(Add("预览缓存","StorageCachePreview",()=>PreviewCache(_profile)),
            Add("清理预览中的缓存","StorageCacheApply",async()=>{await Offline();if(_cacheReview is null||_cacheReviewHash is null)throw new IOException("先预览缓存。");string review=_cacheReview;var plan=await Task.Run(()=>CacheCleanup.LoadReview(review,_cacheReviewHash));if(!await Confirm("释放可重建缓存",$"清理{plan.Files.Count}份缓存，最多{Size(plan.Bytes)}；改变或被占用的文件跳过。图库索引和原图不清理。"))return;long before=new DriveInfo(Path.GetPathRoot(plan.Library)!).AvailableFreeSpace;var result=await Task.Run(()=>CacheCleanup.Apply(plan));long after=new DriveInfo(Path.GetPathRoot(plan.Library)!).AvailableFreeSpace;_cacheReview=null;_cacheReviewHash=null;_status.Text=$"已清理{result.Removed}份，逻辑大小{Size(result.Bytes)}；跳过{result.Skipped}份。磁盘可用空间变化{Size(after-before)}。";await File.WriteAllTextAsync(review+".result.json",JsonSerializer.Serialize(new{result,freeBefore=before,freeAfter=after}));}));
        _previewArea.Children.Add(new TextBlock{Text="操作预览",Style=(Style)Application.Current.Resources["LumaHeading"],FontSize=20});_previewArea.Children.Add(_preview);_previewArea.Children.Add(_fullPath);_body.Children.Add(_previewArea);
        AutomationProperties.SetAutomationId(_status,"StorageStatus");AutomationProperties.SetLiveSetting(_status,AutomationLiveSetting.Polite);
        GotFocus+=(_,e)=>{if(e.OriginalSource is FrameworkElement target)target.StartBringIntoView(new BringIntoViewOptions{AnimationDesired=false,VerticalAlignmentRatio=.5});};
    }
    internal void ConfigureScale(double scale)
    {
        FontSize=14*scale;
        void Apply(FrameworkElement element)
        {
            if(element is Control control)control.FontSize=14*scale;
            if(element is TextBlock text)text.FontSize=(ReferenceEquals(text.Style,Application.Current.Resources["LumaHeading"])?20:14)*scale;
            if(element is Panel panel)foreach(var child in panel.Children.OfType<FrameworkElement>())Apply(child);
        }
        Apply(_body);Apply(_status);
        string pixels=(14*scale).ToString(System.Globalization.CultureInfo.InvariantCulture);
        _preview.ItemTemplate=(DataTemplate)Microsoft.UI.Xaml.Markup.XamlReader.Load("""
        <DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"><TextBlock Text="{Binding}" TextWrapping="Wrap" MaxLines="2" TextTrimming="CharacterEllipsis" ToolTipService.ToolTip="{Binding}" FontSize="
        """+pixels+"""
        "/></DataTemplate>
        """);
    }
    internal void Configure(string profile,string data,EngineSession engine)
    {
        _profile=profile;_data=data;_engine=engine;
        _location.Text=data;AutomationProperties.SetAutomationId(_location,"StorageDataLocation");
        _status.Text="";
    }
    private CancellationToken Token=>_cancel?.Token??CancellationToken.None;
    private Task Offline()
    {
        if(_restart)throw new IOException("需要重启");
        if(_engine?.IsAlive==true||_engine?.CanStore!=true)throw new IOException("图库正在使用");
        return Task.CompletedTask;
    }
    private async Task PreviewCache(string source)
    {
        await Offline();ResetPlans();var plan=await Task.Run(()=>CacheCleanup.Preview(source));
        string reviews=Path.Combine(_data,"Maintenance");Luma.Core.Engine.EngineProcess.RejectReparse(reviews);Directory.CreateDirectory(reviews);
        string path=Path.Combine(reviews,"cache-"+Guid.NewGuid().ToString("N")+".json");
        _cacheReviewHash=await Task.Run(()=>CacheCleanup.SaveReview(path,plan));_cacheReview=path;ShowFiles(plan.Files.Select(f=>f.RelativePath),plan.Library);
        _status.Text=$"缓存预览：{plan.Files.Count}份，{Size(plan.Bytes)}。"+(plan.More?"还有后续批次。":"")+"";
    }
    private void Restart(string profile)
    {
        _restart=true;_backupPlan=null;_cacheReview=null;
        _status.Text="重启后生效";
        if(_engine is not null)_engine.RestartRequired=true;
    }
    private void Heading(string text)=>_body.Children.Add(new TextBlock{Text=text,Style=(Style)Application.Current.Resources["LumaHeading"],FontSize=20});
    private void Note(string text)=>_body.Children.Add(new TextBlock{Text=text,TextWrapping=TextWrapping.Wrap,Style=(Style)Application.Current.Resources["LumaBody"]});
    private void Buttons(params Button[] buttons){var group=new TagChipPanel();foreach(var button in buttons)group.Children.Add(button);_body.Children.Add(group);}
    private Button Add(string label,string id,Func<Task> action)
    {
        var button=new Button{Content=label,Style=(Style)Application.Current.Resources["LumaButton"]};_buttons.Add(button);
        AutomationProperties.SetAutomationId(button,id);AutomationProperties.SetName(button,label);ToolTipService.SetToolTip(button,label);
        button.Click+=(_,_)=>{if(!Busy)_operation=Run(action);};return button;
    }
    private async Task Run(Func<Task> action)
    {
        Busy=true;_cancel=new();if(_engine is not null)_engine.StorageBusy=true;
        _snapshot.IsEnabled=false;
        foreach(var button in _buttons)button.IsEnabled=false;_status.Text="正在处理，请稍候…";Changed?.Invoke();
        try{await action();}
        catch(OperationCanceledException){_status.Text="已取消";}
        catch(Exception error){_status.Text=error.Message switch{"storage_no_library"=>"尚未创建图库；先打开独立图库并导入目录，再进行备份。","storage_source_changed"=>"文件或预览已变化，请重新预览；本次不继续清理或切换。","storage_unowned_library"=>"目录格式错误","storage_space_low"=>"目标磁盘空间不足，备份或迁移未开始，原库保留。",_=>error is IOException&& !error.Message.StartsWith("storage_")?error.Message:"操作失败"};}
        finally{Busy=false;if(_engine is not null)_engine.StorageBusy=false;foreach(var button in _buttons)button.IsEnabled=true;_snapshot.IsEnabled=true;_cancel.Dispose();_cancel=null;Changed?.Invoke();}
    }
    private async Task<bool> Confirm(string title,string message)
    {
        Token.ThrowIfCancellationRequested();
        var dialog=new ContentDialog{Title=title,Content=message,PrimaryButtonText="确认",CloseButtonText="取消",DefaultButton=ContentDialogButton.Close,XamlRoot=XamlRoot,RequestedTheme=RequestedTheme};_dialog=dialog;
        try{return await dialog.ShowAsync()==ContentDialogResult.Primary;}finally{if(ReferenceEquals(_dialog,dialog))_dialog=null;}
    }
    internal async Task PrepareCloseAsync(){_cancel?.Cancel();_dialog?.Hide();if(_operation is not null)await _operation;}
    private static string Size(long bytes)=>$"{bytes/1048576.0:F2} MiB";
    private void ResetPlans(){_backupPlan=null;_cacheReview=null;_cacheReviewHash=null;_preview.ItemsSource=null;_fullPath.Text="";_previewArea.Visibility=Visibility.Collapsed;}
    private void ShowFiles(IEnumerable<string> paths,string root){_previewRoot=root;_preview.ItemsSource=paths.ToArray();_preview.SelectedIndex=_preview.Items.Count>0?0:-1;_previewArea.Visibility=_preview.Items.Count>0?Visibility.Visible:Visibility.Collapsed;}
}
