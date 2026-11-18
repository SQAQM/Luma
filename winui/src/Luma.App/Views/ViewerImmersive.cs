using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Views;

public sealed partial class ViewerView
{
    private DispatcherQueueTimer _idle = null!;
    private bool _controlsVisible = true, _film, _savedInfo, _savedFilm, _manipulating;
    private readonly HashSet<uint> _pressedPointers = [];
    private MenuFlyout? _contextMenu;
    private bool? _appliedFullscreen;
    private bool? _appliedOverlayLayout;
    private bool _applyingImmersiveLayout;
    private DispatcherQueueTimer _copyNoticeTimer = null!;
    private bool CanSplitInfo => ActualWidth >= 740;

    private void InitializeViewerFeedback()
    {
        _copyNoticeTimer = DispatcherQueue.CreateTimer();
        _copyNoticeTimer.Interval = TimeSpan.FromSeconds(2);
        _copyNoticeTimer.IsRepeating = false;
        _copyNoticeTimer.Tick += (_, _) =>
        {
            if (Feedback.Text is "已复制" or "已复制路径") Feedback.Text = "";
        };
        Feedback.RegisterPropertyChangedCallback(TextBlock.TextProperty, (_, _) => UpdateFeedbackVisibility());
    }

    private void ShowCopyNotice(string text)
    {
        Feedback.Text = text;
        _copyNoticeTimer.Stop();
        _copyNoticeTimer.Start();
    }

    private void UpdateFeedbackVisibility() => FeedbackSurface.Visibility =
        IsOpen && _controlsVisible && Feedback.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;

    private void InitializeImmersive()
    {
        _idle = DispatcherQueue.CreateTimer();
        _idle.Interval = TimeSpan.FromSeconds(2);
        _idle.IsRepeating = false;
        _idle.Tick += (_, _) =>
        {
            if (!IsOpen || !Fullscreen || _disposed) return;
            if (KeepControls()) { RestartIdle(); return; }
            // A pointer-focused button may still own focus when its overlay disappears.
            // Keep keyboard accelerators and the next Tab within the visible viewer.
            if (FocusManager.GetFocusedElement(XamlRoot) is DependencyObject focused && IsOverlay(focused))
                Focus(FocusState.Programmatic);
            _idle.Stop();
            _controlsVisible = false;
            LayoutInfo();
            Changed?.Invoke();
        };
        AddHandler(PointerMovedEvent, new PointerEventHandler((_, _) => RestoreControls()), true);
        AddHandler(PointerPressedEvent, new PointerEventHandler((_, e) =>
        {
            if (!IsOpen) return;
            _pressedPointers.Add(e.Pointer.PointerId);
            RestoreControls();
        }), true);
        var release = new PointerEventHandler((_, e) =>
        {
            _pressedPointers.Remove(e.Pointer.PointerId);
            RestartIdle();
        });
        AddHandler(PointerReleasedEvent, release, true);
        AddHandler(PointerCanceledEvent, release, true);
        AddHandler(PointerCaptureLostEvent, release, true);
        AddHandler(KeyDownEvent, new KeyEventHandler((_, _) => RestoreControls()), true);
        GotFocus += (_, _) =>
        {
            // GotFocus is queued by WinUI. The idle callback returns focus to
            // this viewport before hiding chrome; its own queued event must
            // not immediately reopen that chrome. Real keys/pointers and
            // focus entering an overlay still restore the controls.
            if (!_controlsVisible && ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot), this)) return;
            RestoreControls();
        };
        LostFocus += (_, _) => RestartIdle();
        Stage.ManipulationStarted += (_, _) => { _manipulating = true; RestoreControls(); };
        Stage.ManipulationCompleted += (_, _) => { _manipulating = false; RestartIdle(); };
        Sort.DropDownOpened += (_, _) => RestoreControls();
        Sort.DropDownClosed += (_, _) => RestartIdle();
    }

    private bool KeepControls()
    {
        if (_drag || _manipulating || _pressedPointers.Count > 0 || Sort.IsDropDownOpen || _contextMenu is not null || _annotationWriting) return true;
        return FocusManager.GetFocusedElement(XamlRoot) is Control { FocusState: FocusState.Keyboard } focused && IsOverlay(focused);
    }

    private bool IsOverlay(DependencyObject focused)
    {
        for (DependencyObject? node = focused; node is not null; node = VisualTreeHelper.GetParent(node))
            if (ReferenceEquals(node, Header) || ReferenceEquals(node, ToolDock) || ReferenceEquals(node, ReactionsHost) || ReferenceEquals(node, Info) || ReferenceEquals(node, Filmstrip)) return true;
        return false;
    }

    private void RestartIdle()
    {
        _idle.Stop();
        if (!_disposed && IsOpen && Fullscreen && _controlsVisible) _idle.Start();
    }

    private void RestoreControls()
    {
        if (_disposed || !IsOpen) return;
        if (!_controlsVisible) { _controlsVisible = true; LayoutInfo(); Changed?.Invoke(); }
        RestartIdle();
    }

    private bool CloseOverlay()
    {
        if (Sort.IsDropDownOpen) { Sort.IsDropDownOpen = false; return true; }
        if (_contextMenu is not null) { _contextMenu.Hide(); return true; }
        return false;
    }

    private void LayoutImmersive()
    {
        if (_applyingImmersiveLayout) return;
        _applyingImmersiveLayout = true;
        try
        {
            bool immersive = Fullscreen;
            if (_appliedFullscreen != immersive)
            {
                ImmersiveBackdrop.Visibility = immersive ? Visibility.Visible : Visibility.Collapsed;
                StageMatte.Visibility = immersive ? Visibility.Collapsed : Visibility.Visible;
                Grid.SetColumnSpan(StageContainer, immersive ? 2 : 1);
                _appliedFullscreen = immersive;
            }
            bool overlaid = immersive || ActualHeight is > 0 and < 300;
            if(_appliedOverlayLayout != overlaid)
            {
                ViewerHeaderRow.Height = overlaid ? new GridLength(0) : GridLength.Auto;
                ViewerToolsRow.Height = overlaid ? new GridLength(0) : GridLength.Auto;
                Grid.SetRow(Header, overlaid ? 1 : 0);
                Grid.SetRow(BottomChrome, overlaid ? 0 : 1);
                _appliedOverlayLayout=overlaid;
            }
            // Normal windowed controls have their own space. In fullscreen
            // and very short windows, the stage keeps the whole height.
            bool splitInfo = _info && CanSplitInfo;
            bool showDock = _controlsVisible && (!_info || splitInfo);
            double infoInset = immersive && splitInfo ? InfoColumn.Width.Value : 0;
            double dockWidth = ActualWidth - (splitInfo ? InfoColumn.Width.Value : 0);
            bool inlineDock = dockWidth >= 652;
            Grid.SetRow(DockOptions, inlineDock ? 0 : 1);
            Grid.SetColumn(DockOptions, inlineDock ? 1 : 0);
            Grid.SetColumnSpan(DockOptions, inlineDock ? 1 : 2);
            Grid.SetColumnSpan(DockTools, inlineDock ? 1 : 2);
            DockOptions.Orientation = dockWidth >= 330 ? Orientation.Horizontal : Orientation.Vertical;
            Header.Margin = overlaid ? new Thickness(12,10,12,0) : new Thickness(10,0,10,4);
            BottomChrome.Margin = new Thickness(12, 0, 12 + infoInset, overlaid ? 12 : 4);
            BottomChrome.RowSpacing = overlaid ? 6 : 0;
            Info.Margin = overlaid ? new Thickness(0,Math.Max(68,Header.ActualHeight+20),12,12)
                : new Thickness(splitInfo?8:12, 8, 12, 12);
            Header.Visibility = _controlsVisible ? Visibility.Visible : Visibility.Collapsed;
            ToolDock.Visibility = showDock ? Visibility.Visible : Visibility.Collapsed;
            if (!showDock && Sort.IsDropDownOpen) Sort.IsDropDownOpen = false;
            FeedbackSurface.Margin = new Thickness(16, 8, 16, showDock ? BottomChrome.ActualHeight + 22 : 16);
            UpdateFeedbackVisibility();
            var filmVisibility = _film && showDock ? Visibility.Visible : Visibility.Collapsed;
            if (Filmstrip.Visibility != filmVisibility) Filmstrip.Visibility = filmVisibility;
            FilmstripSurface.Visibility = filmVisibility;
        }
        finally { _applyingImmersiveLayout = false; }
    }
}
