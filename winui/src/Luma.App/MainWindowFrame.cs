using System.Runtime.InteropServices;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;

namespace Luma.Preview;

public sealed partial class MainWindow
{
    private const uint WmNcCalcSize = 0x0083;
    private const int SizingFrameWidth = 32, SizingFrameHeight = 33, PaddedBorder = 92;
    private const int MinimumTrackWidth = 34, MinimumTrackHeight = 35;
    private static readonly bool NeedsWindows10Frame = !OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22000);
    private FrameSizing? _frameSizing;

    private bool UsesExtendedFrame => NeedsWindows10Frame && !_closed &&
        _chrome is { Supported: true, HighContrast: false } && !IsZoomed(_hwnd) &&
        AppWindow.Presenter is OverlappedPresenter;
    private bool CanSizeExtendedFrame => UsesExtendedFrame && AppWindow.Presenter is OverlappedPresenter { IsResizable: true };

    private void ConfigureFrameSizing()
    {
        if (!NeedsWindows10Frame || !_settingsHook || !_chrome.Supported) return;
        Surface.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler(StartFrameSizing), true);
        Surface.AddHandler(UIElement.PointerMovedEvent, new PointerEventHandler(MoveFrameSizing), true);
        Surface.PointerReleased += EndFrameSizing;
        Surface.PointerCanceled += EndFrameSizing;
        Surface.PointerCaptureLost += (_, args) =>
        {
            if (_frameSizing?.PointerId != args.Pointer.PointerId) return;
            _frameSizing = null;
            Surface.SetResizeCursor(0, 0);
        };
        Surface.PointerExited += (_, _) =>
        {
            if (_frameSizing is null) Surface.SetResizeCursor(0, 0);
        };
    }

    private void StartFrameSizing(object sender, PointerRoutedEventArgs args)
    {
        if (!CanSizeExtendedFrame || _frameSizing is not null ||
            !args.GetCurrentPoint(Surface).Properties.IsLeftButtonPressed ||
            !GetCursorPos(out var cursor) || !GetWindowRect(_hwnd, out var window)) return;

        uint dpi = GetDpiForWindow(_hwnd);
        var (horizontal, vertical) = GetFrameEdges(cursor, window, dpi);
        if ((horizontal == 0 && vertical == 0) || !Surface.CapturePointer(args.Pointer)) return;

        _frameSizing = new(args.Pointer.PointerId, cursor, window, horizontal, vertical,
            GetSystemMetricsForDpi(MinimumTrackWidth, dpi), GetSystemMetricsForDpi(MinimumTrackHeight, dpi));
        Surface.SetResizeCursor(horizontal, vertical);
        args.Handled = true;
    }

    private void MoveFrameSizing(object sender, PointerRoutedEventArgs args)
    {
        if (_frameSizing is not { } sizing)
        {
            var edges = CanSizeExtendedFrame && GetCursorPos(out var position) && GetWindowRect(_hwnd, out var current)
                ? GetFrameEdges(position, current, GetDpiForWindow(_hwnd)) : (0, 0);
            Surface.SetResizeCursor(edges.Item1, edges.Item2);
            return;
        }
        if (sizing.PointerId != args.Pointer.PointerId) return;
        if (!CanSizeExtendedFrame || !args.GetCurrentPoint(Surface).Properties.IsLeftButtonPressed)
        {
            EndFrameSizing(sender, args);
            return;
        }
        if (!GetCursorPos(out var cursor)) return;

        var window = sizing.Start;
        int dx = cursor.X - sizing.Cursor.X, dy = cursor.Y - sizing.Cursor.Y;
        if (sizing.Horizontal < 0) window.Left = Math.Min(window.Right - sizing.MinWidth, window.Left + dx);
        if (sizing.Horizontal > 0) window.Right = Math.Max(window.Left + sizing.MinWidth, window.Right + dx);
        if (sizing.Vertical < 0) window.Top = Math.Min(window.Bottom - sizing.MinHeight, window.Top + dy);
        if (sizing.Vertical > 0) window.Bottom = Math.Max(window.Top + sizing.MinHeight, window.Bottom + dy);
        const uint keepZOrderAndActivation = 0x14;
        _ = SetWindowPos(_hwnd, 0, window.Left, window.Top, window.Right - window.Left, window.Bottom - window.Top, keepZOrderAndActivation);
        args.Handled = true;
    }

    private void EndFrameSizing(object sender, PointerRoutedEventArgs args)
    {
        if (_frameSizing?.PointerId != args.Pointer.PointerId) return;
        _frameSizing = null;
        Surface.ReleasePointerCapture(args.Pointer);
        Surface.SetResizeCursor(0, 0);
        args.Handled = true;
    }

    private static (int Horizontal, int Vertical) GetFrameEdges(CursorPoint point, Rectangle window, uint dpi)
    {
        if (point.X < window.Left || point.X >= window.Right || point.Y < window.Top || point.Y >= window.Bottom)
            return (0, 0);
        int padding = GetSystemMetricsForDpi(PaddedBorder, dpi);
        return (FindEdge(point.X, window.Left, window.Right, GetSystemMetricsForDpi(SizingFrameWidth, dpi) + padding),
            FindEdge(point.Y, window.Top, window.Bottom, GetSystemMetricsForDpi(SizingFrameHeight, dpi) + padding));
    }

    private static int FindEdge(int position, int start, int end, int thickness) =>
        position < start + thickness ? -1 : position >= end - thickness ? 1 : 0;

    private readonly record struct FrameSizing(uint PointerId, CursorPoint Cursor, Rectangle Start,
        int Horizontal, int Vertical, int MinWidth, int MinHeight);

    [StructLayout(LayoutKind.Sequential)] private struct CursorPoint { public int X, Y; }
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetCursorPos(out CursorPoint point);
    [DllImport("user32.dll")] private static extern int GetSystemMetricsForDpi(int index, uint dpi);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(nint handle, nint after, int x, int y, int width, int height, uint flags);
}

public sealed class FrameSurface : Grid
{
    private InputSystemCursorShape? _resizeCursor;

    internal void SetResizeCursor(int horizontal, int vertical)
    {
        InputSystemCursorShape? shape = (horizontal, vertical) switch
        {
            (0, 0) => null,
            (_, 0) => InputSystemCursorShape.SizeWestEast,
            (0, _) => InputSystemCursorShape.SizeNorthSouth,
            _ when horizontal == vertical => InputSystemCursorShape.SizeNorthwestSoutheast,
            _ => InputSystemCursorShape.SizeNortheastSouthwest
        };
        if (_resizeCursor == shape) return;
        _resizeCursor = shape;
        ProtectedCursor = shape is { } selected ? InputSystemCursor.Create(selected) : null;
    }
}
