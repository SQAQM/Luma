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
    private bool _applyingImmersiveLayout;

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
                ViewerRoot.Background = new SolidColorBrush(immersive ? Microsoft.UI.Colors.Black : Microsoft.UI.Colors.Transparent);
                Grid.SetColumnSpan(StageContainer, immersive ? 2 : 1);
                _appliedFullscreen = immersive;
            }
            // Only an explicit information-panel change allocates stage space.
            // Idle chrome and the filmstrip are overlays in both viewing modes;
            // hiding them must never resize/refit the decoded image.
            double infoInset = immersive && _info ? InfoColumn.Width.Value : 0;
            double dockWidth = ActualWidth - (immersive ? infoInset : _info && ActualWidth >= 1000 ? 340 : 0);
            DockCommands.Orientation = dockWidth < 480 ? Orientation.Vertical : Orientation.Horizontal;
            BottomChrome.Margin = new Thickness(12, 0, 12 + infoInset, 12);
            Info.Margin = new Thickness(0, Math.Max(68, Header.ActualHeight + 20), 12, 12);
            Header.Visibility = ToolDock.Visibility = ReactionsHost.Visibility = _controlsVisible ? Visibility.Visible : Visibility.Collapsed;
            Feedback.Margin = new Thickness(16, 8, 16 + infoInset, Math.Max(112, BottomChrome.ActualHeight + 22));
            Feedback.Visibility = _controlsVisible ? Visibility.Visible : Visibility.Collapsed;
            var filmVisibility = _film && _controlsVisible ? Visibility.Visible : Visibility.Collapsed;
            if (Filmstrip.Visibility != filmVisibility) Filmstrip.Visibility = filmVisibility;
            FilmstripSurface.Visibility = filmVisibility;
        }
        finally { _applyingImmersiveLayout = false; }
    }
}
