using Luma.Preview.ViewModels;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.UI.ViewManagement;
using Windows.System;
using Microsoft.UI.Xaml.Input;
using Luma.Preview.Views.Controls;
using System.Runtime.InteropServices;

namespace Luma.Preview.Views;

public sealed partial class ShellView : UserControl, IDisposable
{
    internal static string InitializationPhase { get; private set; } = "shell_new";
    private readonly ShellViewModel _model = new();
    private readonly Dictionary<WorkspacePage, Button> _navigation = [];
    private readonly UISettings _settings = new();
    private readonly AccessibilitySettings _accessibility = new();
    private bool _disposed;
    private bool _motionEventAttached, _contrastEventAttached;
    private bool _lastAnimations, _lastContrast;
    private int _preferenceChecks;
    private double _scale = 1;
    private Storyboard? _transition;
    private string _theme = "System", _layout = "Side";
    public event Action? Changed;
    public event Action? CloseRequested;
    public event Action? OpenFileRequested;
    private Luma.Core.Engine.EngineClient? _singlePresented;
    internal bool FileWindowMode{get;set;}
    private void OpenImageClicked(object sender,RoutedEventArgs e)=>OpenFileRequested?.Invoke();
    private Services.EngineSession? _engine;
    private readonly ViewerView _viewer=new();
    private readonly StorageView _storage=new(){Visibility=Visibility.Collapsed};
    internal void ConfigureStorage(string profile,string data,Services.EngineSession engine){_storage.Configure(profile,data,engine);_storage.Changed+=()=>{if(!_disposed)Changed?.Invoke();};PageBody.Children.Add(_storage);}
    internal Task PrepareStorageCloseAsync()=>_storage.PrepareCloseAsync();
    public event Action<bool>? FullscreenRequested;
    internal void SetFullscreen(bool value)=>_viewer.SetFullscreen(value);
    private bool _diagnostics;
    private bool _libraryChanged;
    internal nint WindowHandle{set{Management.WindowHandle=value;LiveGallery.WindowHandle=value;Files.WindowHandle=value;_storage.WindowHandle=value;}}
    internal void ConfigureDiagnostics(bool value,bool pressure=false,bool metadataFailure=false){_diagnostics=value;_viewer.MetadataFailureOnce=metadataFailure;LiveGallery.Configure(_scale,value);LiveGallery.ThumbnailPressure=pressure;Files.Configure(_scale,value);}
    internal void AttachEngine(Services.EngineSession engine)
    {
        _engine = engine; engine.Changed += EngineChanged;
        Management.ConnectionContent=ConnectionContent;
    }
    private Luma.Core.Management.ManagementState? _managementState;
    private Luma.Core.Engine.EngineClient? _managementClient;
    private void EngineChanged()
    {
        if (_disposed) return;
        if(_engine?.Connected==true&&_engine.Client is{} client&&_engine.DataDirectory is{} data)
        {
            if(!ReferenceEquals(_managementClient,client)){_managementState?.Dispose();_managementClient=client;_managementState=new(new Luma.Core.Management.ManagementApi(client),new Luma.Core.Management.PendingOperationStore(data));_=RestoreSharedPreferences(client);}
            Management.Bind(client,data,_managementState!);Files.Bind(client,_managementState!);
            _viewer.SetManagement(!_engine.IsSingleFile?_managementState:null);
            if(_engine.IsGallery)LiveGallery.Bind(client,data,_managementState!);
            if(_engine.IsSingleFile&&!ReferenceEquals(_singlePresented,client)){_singlePresented=client;_=PresentSingle(client,data);}
        }
        else if(_engine?.Connected!=true){_viewer.CloseViewer();_viewer.SetManagement(null);LiveGallery.Detach();Management.Detach();Files.Detach();_managementState?.Dispose();_managementState=null;_managementClient=null;}
        Management.RefreshConnection();Render();
        Changed?.Invoke();
    }
    private async Task PresentSingle(Luma.Core.Engine.EngineClient client,string data)
    {
        try
        {
            var page=await client.QueryAsync(new());
            if(_disposed||!ReferenceEquals(_engine?.Client,client)||_engine?.IsSingleFile!=true||page.Items.Length!=1)return;
            var item=Luma.Core.Gallery.GalleryApi.ParseItem(page.Items[0]);
            LiveGallery.SetActive(false);Rail.Visibility=Visibility.Collapsed;Workspace.Visibility=Visibility.Collapsed;
            _viewer.Open(client,data,item,new(){["sort"]="name"},_diagnostics);
        }
        catch(Exception){if(!_disposed){Feedback.Text="单图查看未完成，可以重新选择图片。";Changed?.Invoke();}}
    }
    public string Page => _model.Page.ToString();
    public string Mode => _model.Current.Mode.ToString();
    public string Theme => _theme;
    internal bool MaterialsRequested{get;private set;}=true;
    internal Brush ResolveThemeBrush(string key)=>Brush(key);
    internal ResourceDictionary? ThemeColors{get;set;}
    internal void RestoreMaterialPreference(bool enabled){MaterialsRequested=enabled;UpdateMaterialButton();}
    internal void ApplyMaterialPlan(Luma.Core.Design.MaterialPlan plan){RailTint.Background=Brush("LumaSidebar");UpdateMaterialButton(plan.Reason);}
    private void MaterialClicked(object sender,RoutedEventArgs e){MaterialsRequested=!MaterialsRequested;UpdateMaterialButton();Changed?.Invoke();}
    private void UpdateMaterialButton(string reason=""){MaterialButton.Content=MaterialsRequested?"透明效果：开启":"透明效果：关闭";MaterialButton.BorderThickness=new Thickness(MaterialsRequested?2:1);MaterialButton.BorderBrush=Brush(MaterialsRequested?"LumaAccent":"LumaStroke");string label=!MaterialsRequested?"实色界面":reason=="system_transparency_off"?"系统已关闭透明效果":reason=="high_contrast"?"高对比模式使用实色":reason=="unsupported"?"当前环境使用实色":"透明材质，点击切换为实色";AutomationProperties.SetName(MaterialButton,label);AutomationProperties.SetItemStatus(MaterialButton,MaterialsRequested?"已请求透明材质":"实色");ToolTipService.SetToolTip(MaterialButton,label);}
    internal bool ReduceMotionPreference=>ReduceMotion.IsChecked==true;
    internal void RestorePreferences(string theme,bool reduce){Management.CurrentTheme=theme;Management.CurrentReducedMotion=reduce;_theme=theme;RequestedTheme=theme=="Dark"?ElementTheme.Dark:theme=="Light"?ElementTheme.Light:ElementTheme.Default;ReduceMotion.IsChecked=reduce;UpdateThemeButtons();}
    public string Layout => _layout;
    public object Snapshot => new { gallery = LiveGallery.Snapshot, viewer = _viewer.Snapshot, management=Management.Snapshot,files=Files.Snapshot,storage=_storage.Snapshot, page = Page, mode = Mode, queryLength = _model.Current.Query.Length, sort = _model.Current.Sort, selectedId = _model.Current.SelectedId, reducedMotion = Reduced, animationMilliseconds = Reduced ? 0 : 120, pageOpacity = PageBody.Opacity, layout = _layout, motionEventAttached = _motionEventAttached, contrastEventAttached = _contrastEventAttached, preferenceChecks = _preferenceChecks };
    private bool Reduced => ReduceMotion.IsChecked == true || !_settings.AnimationsEnabled || _accessibility.HighContrast;
    private static readonly string[] Names = ["图片", "模型", "标签", "分类", "清理", "任务", "数据", "设置"];
    private static readonly string[] Icons = ["\uE8B9", "\uE7B8", "\uE8EC", "\uE8B7", "\uE74D", "\uE9D5", "\uEDA2", "\uE713"];

    public ShellView()
    {
        InitializationPhase = "shell_xaml";
        InitializeComponent();
        RailContent.Children.Remove(Preferences);Management.AppearanceContent=Preferences;Management.SettingsConnectionFeedback=()=>_engine?.Feedback??"外观即时生效。";Management.CanSettingsConnect=()=>_engine?.CanConnect==true;Management.OpenSettingsLibraryRequested+=()=>_engine?.OpenServices();Management.StorageRequested+=()=>Navigate(WorkspacePage.Storage);
        Grid.SetRowSpan(_viewer,2);Grid.SetColumnSpan(_viewer,2);ShellRoot.Children.Add(_viewer);
        _viewer.Changed+=()=>{if(!_disposed)Changed?.Invoke();};
        _viewer.AnnotationChanged+=()=>LiveGallery.MarkAnnotationsDirty();
        _viewer.FullscreenRequested+=value=>FullscreenRequested?.Invoke(value);
        _viewer.BackRequested+=item=>{Workspace.Visibility=Visibility.Visible;Rail.Visibility=Visibility.Visible;if(_engine?.IsSingleFile==true){if(FileWindowMode)CloseRequested?.Invoke();else _=_engine.CloseSingleAsync();return;}if(_model.Page==WorkspacePage.Models)Management.ResumeFromViewer();Render();if(_model.Page==WorkspacePage.Gallery)LiveGallery.ReturnFromViewer(item);Changed?.Invoke();};
        _viewer.TagRequested+=tag=>{_viewer.CloseViewer();Navigate(WorkspacePage.Gallery);LiveGallery.FilterContent(tag);};
        LiveGallery.OpenViewerRequested+=(item,filters)=>{if(_engine?.Client is not{} client||_engine.DataDirectory is not{} data)return;LiveGallery.SetActive(false);Rail.Visibility=Visibility.Collapsed;Workspace.Visibility=Visibility.Collapsed;_viewer.Open(client,data,item,filters,_diagnostics);};
        Management.Changed+=()=>{if(!_disposed)Changed?.Invoke();};

        Management.LibraryChanged+=()=>_libraryChanged=true;
        Files.Changed+=()=>{if(!_disposed)Changed?.Invoke();};Files.LibraryChanged+=()=>_libraryChanged=true;Files.JobsRequested+=()=>Navigate(WorkspacePage.Jobs);
        Management.PreferencesChanged+=ApplySharedPreferences;

        Management.OpenViewerRequested+=(item,filters)=>{if(_engine?.Client is not{} client||_engine.DataDirectory is not{} data)return;Management.SuspendForViewer();LiveGallery.SetActive(false);Rail.Visibility=Visibility.Collapsed;Workspace.Visibility=Visibility.Collapsed;_viewer.Open(client,data,item,filters,_diagnostics);};
        LiveGallery.EditTagsRequested+=ids=>{Navigate(WorkspacePage.Tags);Management.ShowPage("Tags",ids);};
        InitializationPhase = "shell_navigation";
        foreach (var page in Enum.GetValues<WorkspacePage>())
        {
            var content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            content.Children.Add(new FontIcon { Glyph = Icons[(int)page], FontSize = 17 });
            content.Children.Add(new TextBlock { Text = Names[(int)page], VerticalAlignment = VerticalAlignment.Center });
            var button = new Button { Content = content, Style = (Style)ResourcesOrApp("LumaNav"), Tag = page };
            AutomationProperties.SetAutomationId(button, "Nav" + page);
            AutomationProperties.SetName(button, Names[(int)page]);
            button.Click += (_, _) => Navigate(page);
            _navigation.Add(page, button);if(page==WorkspacePage.Settings){button.MinHeight=36;button.Padding=new Thickness(8,6,8,6);SettingsNavigation.Children.Add(button);}else Navigation.Children.Add(button);
            var shortcut = new KeyboardAccelerator { Key = (VirtualKey)((int)VirtualKey.Number1 + (int)page), Modifiers = VirtualKeyModifiers.Control };
            shortcut.Invoked += (_, e) => { Navigate(page); e.Handled = true; }; KeyboardAccelerators.Add(shortcut);
        }
        var searchShortcut = new KeyboardAccelerator { Key = VirtualKey.F, Modifiers = VirtualKeyModifiers.Control };
        var openShortcut=new KeyboardAccelerator{Key=VirtualKey.O,Modifiers=VirtualKeyModifiers.Control};openShortcut.Invoked+=(_,e)=>{OpenFileRequested?.Invoke();e.Handled=true;};KeyboardAccelerators.Add(openShortcut);
        searchShortcut.Invoked += (_, e) => { if(_viewer.IsOpen){e.Handled=true;return;}if(LiveGallery.Visibility==Visibility.Visible){LiveGallery.FocusSearch();e.Handled=true;} }; KeyboardAccelerators.Add(searchShortcut);
        InitializationPhase = "shell_system_events";
        try { _settings.AnimationsEnabledChanged += SystemMotionChanged; _motionEventAttached = true; }
        catch (COMException error) when (error.HResult == unchecked((int)0x80070490)) { /* Own-window WM_SETTINGCHANGE + fresh getter remain active. */ }
        try { _accessibility.HighContrastChanged += SystemContrastChanged; _contrastEventAttached = true; }
        catch (COMException error) when (error.HResult == unchecked((int)0x80070490)) { /* Same native settings-message fallback. */ }
        NavigationScroll.GotFocus+=(_,e)=>{if(e.OriginalSource is FrameworkElement element)element.StartBringIntoView(new BringIntoViewOptions{AnimationDesired=false,VerticalAlignmentRatio=.5});};
        NavigationScroll.ViewChanged+=(_,e)=>{if(!e.IsIntermediate&&!_disposed)Changed?.Invoke();};
        InitializationPhase = "shell_render";
        LiveGallery.Changed += () => { if(!_disposed)Changed?.Invoke(); };
        Render();
        _lastAnimations = _settings.AnimationsEnabled; _lastContrast = _accessibility.HighContrast;
        InitializationPhase = "shell_ready";
    }

    public void Configure(int textScale, string layout)
    {
        _scale = textScale / 100.0;
        if(layout!="Top")RailColumn.Width=new GridLength(_scale>1?168:148);
        RailContent.Padding=new Thickness(10,12,10,12);FooterActions.ColumnSpacing=4;
        LiveGallery.Configure(_scale,_diagnostics);
        Management.Configure(_scale);Files.Configure(_scale,_diagnostics);Management.SetDiagnostics(_diagnostics);
        _viewer.Configure(_scale);_viewer.SetMediaReducedMotion(Reduced);
        _layout = layout;
        ScaleTree(this);ScaleTree(Preferences);if(Preferences.Children.FirstOrDefault() is TextBlock appearanceHeading)appearanceHeading.FontSize=20*_scale;
        _storage.ConfigureScale(_scale);
        StateTitle.FontSize = 22 * _scale;
        if (layout == "Top")
        {
            RailColumn.Width = new GridLength(0); TopRow.Height = GridLength.Auto;
            Grid.SetRow(Rail, 0); Grid.SetColumnSpan(Rail, 2); Rail.Padding = new Thickness(0);
            Navigation.Orientation = Orientation.Horizontal;
            // Exploration-only composition: all six destinations remain peers.
            NavigationScroll.Content = null;
            RailContent.Children.Remove(FooterActions);
            SettingsNavigation.Orientation=Orientation.Horizontal;
            var top = new Grid { RowSpacing = 12, Padding = new Thickness(16,8,16,8) };
            top.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); top.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            top.Children.Add(FooterActions); Grid.SetRow(Navigation, 1); top.Children.Add(Navigation); RailHost.Children.Remove(RailContent);RailHost.Children.Add(top);
        }
        Render(); UpdateThemeButtons();
    }

    private object ResourcesOrApp(string key) => Application.Current.Resources[key];
    private Brush Brush(string key)
    {
        var colors = ThemeColors??Application.Current.Resources.MergedDictionaries.First(d => d.Source?.ToString().EndsWith("Themes/Colors.xaml", StringComparison.Ordinal) == true);
        string mode = _accessibility.HighContrast ? "HighContrast" : ActualTheme == ElementTheme.Dark ? "Default" : "Light";
        return (Brush)((ResourceDictionary)colors.ThemeDictionaries[mode])[key];
    }
    private TextBlock Text(string value, bool caption = false) => new() { Text = value, Style = (Style)ResourcesOrApp(caption ? "LumaCaption" : "LumaBody"), FontSize = (caption ? 12 : 14) * _scale };
    private Button Action(string text, string id, Action clicked, bool enabled = true)
    {
        var button = new Button { Content = text, Style = (Style)ResourcesOrApp("LumaButton"), IsEnabled = enabled, FontSize = 14 * _scale };
        AutomationProperties.SetAutomationId(button, id); AutomationProperties.SetName(button, text);
        ToolTipService.SetToolTip(button, text);
        button.Click += (_, _) => clicked(); return button;
    }
    private void ScaleTree(DependencyObject node)
    {
        if (node is Control control) control.FontSize = 14 * _scale;
        if (node is TextBlock text) text.FontSize = (ReferenceEquals(text.Style, ResourcesOrApp("LumaHeading")) ? 28 : ReferenceEquals(text.Style, ResourcesOrApp("LumaCaption")) ? 12 : 14) * _scale;
        for (int index = 0; index < VisualTreeHelper.GetChildrenCount(node); index++) ScaleTree(VisualTreeHelper.GetChild(node, index));
    }

    private void Navigate(WorkspacePage page)
    {
        if(_viewer.IsOpen||FileWindowMode||_model.Page==page)return;
        _model.Page = page;
        if(page is WorkspacePage.Models or WorkspacePage.Settings)_engine?.OpenServices();
        else if(page==WorkspacePage.Gallery&&_engine is{Started:true})_engine.OpenLibrary();
        else if(page==WorkspacePage.Gallery&&_engine?.Connecting==true)_engine.OpenLibrary();
        Render();if(_layout=="Side"&&_navigation.TryGetValue(page,out var target))target.StartBringIntoView(new BringIntoViewOptions{AnimationDesired=false,VerticalAlignmentRatio=.5}); Animate(); Changed?.Invoke();
    }
    private void Render()
    {
        if (_disposed) return;

        try
        {
            PageTitle.Text = Names[(int)_model.Page];
            foreach (var pair in _navigation)
            {
                bool selected = pair.Key == _model.Page;
                pair.Value.Background = selected ? Brush("LumaSelected") : new SolidColorBrush(Colors.Transparent);
                pair.Value.Foreground = selected ? Brush("LumaSelectedText") : Brush("LumaText");
                AutomationProperties.SetItemStatus(pair.Value, selected ? "当前页面" : "");
            }
            bool live = _model.Page==WorkspacePage.Gallery && _model.Current.Mode==PreviewState.Sample && _engine?.Connected==true && _engine.IsGallery;
            bool fileManaged=_engine?.Connected==true&&_model.Current.Mode==PreviewState.Sample&&_model.Page is WorkspacePage.Organize or WorkspacePage.Duplicates;
            if(fileManaged){if(!Files.IsShowing(Page))Files.ShowPage(Page,LiveGallery.SelectedIds);}else Files.Hide();
            bool managed=_model.Page==WorkspacePage.Settings||(_engine?.Connected==true&&_model.Current.Mode==PreviewState.Sample&&_model.Page is WorkspacePage.Models or WorkspacePage.Tags or WorkspacePage.Jobs);
            bool suspendedModelViewer=_viewer.IsOpen&&Management.IsSuspendedForViewer;
            if(managed){Management.CurrentTheme=_theme;Management.CurrentReducedMotion=ReduceMotionPreference;Management.CurrentSort=LiveGallery.CurrentSort;if(!suspendedModelViewer&&!Management.IsShowing(Page))Management.ShowPage(Page);ScopeNote.Text="本机管理";}else {ScopeNote.Text="本机图片与模型";if(!suspendedModelViewer)Management.Hide();}
            bool storage=_model.Page==WorkspacePage.Storage;
            _storage.Visibility=storage?Visibility.Visible:Visibility.Collapsed;
            bool managedSurface=managed||fileManaged||storage;
            bool welcome=!live&&!managedSurface;
            Grid.SetRow(Footer,managedSurface||live?0:2);Feedback.Visibility=managedSurface||live?Visibility.Collapsed:Visibility.Visible;
            ScopeNote.Visibility=live||managedSurface?Visibility.Collapsed:Visibility.Visible;PageTitle.FontSize=(live||managedSurface?20:28)*_scale;Workspace.Padding=live||managedSurface?new Thickness(20,12,20,12):new Thickness(24);Workspace.RowSpacing=live||managedSurface?10:16;
            LiveGallery.Visibility=live?Visibility.Visible:Visibility.Collapsed;LiveGallery.SetActive(live&&!_viewer.IsOpen);
            StatePanel.Visibility=Visibility.Collapsed;
            WelcomeActions.Children.Clear();WelcomeActions.Visibility=Visibility.Collapsed;LoadingRing.IsActive=false;LoadingRing.Visibility=Visibility.Collapsed;StateIcon.Visibility=Visibility.Visible;
            if(live) { if(_libraryChanged){_libraryChanged=false;LiveGallery.RefreshManagedLibrary();} }
            else if(managedSurface) { }
            else if(_model.Page==WorkspacePage.Models&&_model.Current.Mode==PreviewState.Sample) RenderModelConnection();
            else RenderWelcome();
            Feedback.Text=_engine?.Feedback??"选择图片目录开始管理。";
            if(welcome)Feedback.Text=_engine?.Feedback??"打开独立图库后开始管理。";
            if(live)Feedback.Text="本机图库 · 原图保留原位";
            if(managed)Feedback.Text="本机索引与管理 · 任务由你明确启动";
            if(_engine?.IsGallery==true && !_engine.Connected)Feedback.Text=_engine.Feedback;
        }
        finally {  }
    }

    private async Task RestoreSharedPreferences(Luma.Core.Engine.EngineClient client)
    {
        try{var prefs=await client.CallAsync("manage.read",new{section="settings",args=new{}});if(!_disposed&&ReferenceEquals(_managementClient,client))LiveGallery.ApplyPreferences(prefs);Render();Changed?.Invoke();}catch(Exception){if(!_disposed&&ReferenceEquals(_managementClient,client)){Feedback.Text="偏好读取失败，可到任务中心刷新。";Changed?.Invoke();}}
    }
    private void ApplySharedPreferences(System.Text.Json.JsonElement prefs)
    {
        string theme=prefs.TryGetProperty("theme",out var value)?value.GetString()??"system":_theme.ToLowerInvariant();bool reduce=prefs.TryGetProperty("reduceMotion",out var motion)?motion.GetBoolean():ReduceMotionPreference;RestorePreferences(theme=="dark"?"Dark":theme=="light"?"Light":"System",reduce);LiveGallery.ApplyPreferences(prefs);Render();Changed?.Invoke();
    }
    private void RenderWelcome()
    {
        if(FileWindowMode){RenderMessage("打开图片",_engine?.Feedback??"选择一张图片，不加入主图库。","选择图片",()=>OpenFileRequested?.Invoke());return;}
        ScopeNote.Text="本机图片与模型";
        RenderMessage("","打开图库并添加图片目录，原图保留原位。","",()=>{});
        StateAction.Visibility=Visibility.Collapsed;WelcomeActions.Visibility=Visibility.Visible;
        WelcomeActions.Children.Add(Action("打开图库","LibraryOpen",()=>_engine?.OpenLibrary(),_engine?.CanConnect==true||_engine?.Connected==true));
    }
    private void RenderModelConnection()
    {
        string workspace="模型管理";ScopeNote.Text=workspace;
        bool loading=_engine?.Connecting==true;
        RenderMessage(loading?"正在准备"+workspace:"本地服务未就绪",_engine?.Feedback??"启动本地服务后可直接使用此工作区。",loading?"":"重试连接",()=>_engine?.OpenServices());
        StateAction.Visibility=!loading&&_engine?.CanConnect==true?Visibility.Visible:Visibility.Collapsed;
        LoadingRing.IsActive=loading;LoadingRing.Visibility=loading?Visibility.Visible:Visibility.Collapsed;
    }
    private UIElement ConnectionContent()
    {
        var panel=new StackPanel{Spacing=8};var state=Text(_engine?.State??"未连接");AutomationProperties.SetAutomationId(state,"EngineState");panel.Children.Add(state);
        var actions=new TagChipPanel();
        if(_diagnostics){actions.Children.Add(Action("延迟请求","EngineProbe",()=>_engine?.Probe(),_engine?.CanProbe==true));actions.Children.Add(Action("取消请求","EngineCancel",()=>_engine?.Cancel(),_engine?.CanCancel==true));}
        actions.Children.Add(Action("安全断开","EngineDisconnect",()=>{if(_engine is not null)_=_engine.StopAsync();},_engine?.CanStop==true));panel.Children.Add(actions);
        var feedback=Text(_engine?.Feedback??"",true);AutomationProperties.SetAutomationId(feedback,"EngineFeedback");panel.Children.Add(feedback);return panel;
    }
    private Action? _stateAction;
    private void RenderMessage(string title, string description, string action, Action callback) { StatePanel.Visibility = Visibility.Visible; StateAction.Visibility=Visibility.Visible; StateTitle.Text = title; StateDescription.Text = description; StateAction.Content = action; _stateAction = callback; }
    private void StateActionClicked(object sender, RoutedEventArgs e) => _stateAction?.Invoke();
    private void Refresh() { Render(); Changed?.Invoke(); }
    private void ThemeClicked(object sender, RoutedEventArgs e) { _theme = (string)((Button)sender).Tag; RequestedTheme = _theme == "Dark" ? ElementTheme.Dark : _theme == "Light" ? ElementTheme.Light : ElementTheme.Default; UpdateThemeButtons(); Render(); Changed?.Invoke(); }
    private void UpdateThemeButtons() { foreach (var button in new[] { LightButton, DarkButton, SystemButton }) { button.BorderBrush = Brush((string)button.Tag == _theme ? "LumaAccent" : "LumaStroke"); button.BorderThickness = new Thickness((string)button.Tag == _theme ? 2 : 1); AutomationProperties.SetItemStatus(button, (string)button.Tag == _theme ? "已选择" : ""); } }
    private void MotionChanged(object sender, RoutedEventArgs e) {_viewer?.SetMediaReducedMotion(Reduced); if(Management is not null)Management.CurrentReducedMotion=ReduceMotionPreference; _transition?.Stop(); if (PageBody is not null) PageBody.Opacity = 1; Changed?.Invoke(); }
    private void Animate() { _transition?.Stop(); PageBody.Opacity = 1; if (Reduced) return; var animation = new DoubleAnimation { From = .9, To = 1, Duration = new Duration(TimeSpan.FromMilliseconds(120)), EnableDependentAnimation = false }; Storyboard.SetTarget(animation, PageBody); Storyboard.SetTargetProperty(animation, "Opacity"); _transition = new Storyboard(); _transition.Children.Add(animation); _transition.Begin(); }
    private void SystemMotionChanged(UISettings sender, object args) => DispatcherQueue.TryEnqueue(() => { if (!_disposed) MotionChanged(this, new RoutedEventArgs()); });
    private void SystemContrastChanged(AccessibilitySettings sender, object args) => DispatcherQueue.TryEnqueue(() => { if (!_disposed) { MotionChanged(this, new RoutedEventArgs()); UpdateThemeButtons(); Render(); } });
    internal void RefreshSystemPreferences(bool themeMessage = false)
    {
        if (_disposed) return;
        _preferenceChecks++;
        bool animations = _settings.AnimationsEnabled, contrast = _accessibility.HighContrast;
        if (themeMessage || animations != _lastAnimations || contrast != _lastContrast)
        { _lastAnimations = animations; _lastContrast = contrast; MotionChanged(this, new RoutedEventArgs()); UpdateThemeButtons(); Render(); }
        Changed?.Invoke();
    }
    private void CloseClicked(object sender, RoutedEventArgs e) => CloseRequested?.Invoke();
    public void Dispose() { _disposed = true; _viewer.Dispose(); Management.Dispose(); Files.Dispose(); LiveGallery.Dispose(); _managementState?.Dispose(); if (_engine is not null) _engine.Changed -= EngineChanged; _transition?.Stop(); if (_motionEventAttached) _settings.AnimationsEnabledChanged -= SystemMotionChanged; if (_contrastEventAttached) _accessibility.HighContrastChanged -= SystemContrastChanged; }
}
