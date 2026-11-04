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
        GotFocus += (_, _) => RestoreControls();
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
                // A virtualizing ListView must not remain visible while its grid
                // row/extent switches between a normal row and a viewport overlay.
                Filmstrip.Visibility = Visibility.Collapsed;
                ViewerRoot.Background = new SolidColorBrush(immersive ? Microsoft.UI.Colors.Black : Microsoft.UI.Colors.Transparent);
                HeaderRow.Height = ReactionsRow.Height = FilmRow.Height = immersive ? new GridLength(0) : GridLength.Auto;
                Grid.SetRow(Body, immersive ? 0 : 1);
                Grid.SetRowSpan(Body, immersive ? 4 : 1);
                Grid.SetColumnSpan(StageContainer, immersive ? 2 : 1);
                Grid.SetRow(Header, 0);
                Grid.SetRowSpan(Header, immersive ? 4 : 1);
                Header.VerticalAlignment = immersive ? VerticalAlignment.Top : VerticalAlignment.Stretch;
                HeaderBackdrop.Visibility = immersive ? Visibility.Visible : Visibility.Collapsed;
                Grid.SetRow(ReactionsHost, immersive ? 0 : 2);
                Grid.SetRowSpan(ReactionsHost, immersive ? 4 : 1);
                ReactionsHost.VerticalAlignment = immersive ? VerticalAlignment.Bottom : VerticalAlignment.Stretch;
                Grid.SetRow(Filmstrip, immersive ? 0 : 3);
                Grid.SetRowSpan(Filmstrip, immersive ? 4 : 1);
                Filmstrip.VerticalAlignment = immersive ? VerticalAlignment.Bottom : VerticalAlignment.Stretch;
                Grid.SetRow(Feedback, immersive ? 0 : 1);
                Grid.SetRowSpan(Feedback, immersive ? 4 : 1);
                _appliedFullscreen = immersive;
            }
            // These offsets depend on currently expanded panels and window width,
            // but the mode-specific grid topology/background above does not.
            ReactionsHost.Margin = immersive ? new Thickness(8, 0, 8, _film ? 88 : 8) : new Thickness(0, 0, _info && ActualWidth >= 1000 ? 340 : 0, 0);
            ToolDock.Margin = new Thickness(8, 0, 8, immersive ? (_film ? 128 : 48) : 12);
            Info.Margin = immersive ? new Thickness(0, 52, 8, _film ? 170 : 90) : new Thickness(0);
            Header.Visibility = ToolDock.Visibility = ReactionsHost.Visibility = _controlsVisible ? Visibility.Visible : Visibility.Collapsed;
            Feedback.Margin = new Thickness(16, 8, 16, immersive ? (_film ? 184 : 104) : 66);
            Feedback.Visibility = _controlsVisible ? Visibility.Visible : Visibility.Collapsed;
            var filmVisibility = _film && _controlsVisible ? Visibility.Visible : Visibility.Collapsed;
            if (Filmstrip.Visibility != filmVisibility) Filmstrip.Visibility = filmVisibility;
        }
        finally { _applyingImmersiveLayout = false; }
    }
}
