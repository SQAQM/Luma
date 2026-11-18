// Adapted from Lenso UI motion/press-model.ts, press.ts and spring.ts at
// 81b06bf475d02c6fefd373ef04d0b942e46d82f2 (MIT).
// Copyright (c) 2026 Kelvin Zhang and Moein Saharkhiz.
// Copyright (c) 2026 Kelvin Zhang. SPDX-License-Identifier: MIT.
// Upstream touch response adapted from onlymoein/liquidglass 89f8b736.
// Native adaptation: bounded inward deformation for compact rectangular buttons;
// clocks and presentation stay in Windows Composition, outside this input model.
namespace Luma.Core.Design;

public readonly record struct GlassPressPose(double ScaleX, double ScaleY, double ShiftX, double ShiftY);
public readonly record struct GlassPressSpring(double DampingRatio, TimeSpan Period);
public enum GlassPressKey { Space, Enter }

public sealed class GlassPressMotion
{
    private static readonly GlassPressPose Rest = new(1, 1, 0, 0);
    private uint? _pointer;
    private GlassPressKey? _key;
    private double _width, _height, _x, _y;
    private bool _touch;

    public GlassPressPose Pose { get; private set; } = Rest;
    public GlassPressSpring Spring { get; private set; } = Tune(false, false);
    public bool IsPressed => _pointer.HasValue || _key.HasValue;

    public bool TryPressPointer(uint id, double width, double height, double x, double y, bool touch)
    {
        if (IsPressed || !ValidSize(width, height) || !double.IsFinite(x) || !double.IsFinite(y)) return false;
        _pointer = id;
        _width = width; _height = height; _x = x; _y = y; _touch = touch;
        Pose = Calculate(width, height, 0, 0, touch);
        Spring = Tune(touch, true);
        return true;
    }

    public bool MovePointer(uint id, double x, double y)
    {
        if (_pointer != id || !double.IsFinite(x) || !double.IsFinite(y)) return false;
        var pose = Calculate(_width, _height, x - _x, y - _y, _touch);
        if (pose == Pose) return false;
        Pose = pose;
        return true;
    }

    public bool ReleasePointer(uint id)
    {
        if (_pointer != id) return false;
        Reset();
        return true;
    }

    public bool TryPressKey(GlassPressKey key, double width, double height)
    {
        if (IsPressed || !ValidSize(width, height)) return false;
        _key = key; _touch = false;
        Pose = Calculate(width, height, 0, 0, false);
        Spring = Tune(false, true);
        return true;
    }

    public bool ReleaseKey(GlassPressKey key)
    {
        if (_key != key) return false;
        Reset();
        return true;
    }

    public void Reset()
    {
        _pointer = null; _key = null;
        Pose = Rest;
        Spring = Tune(_touch, false);
    }

    public static GlassPressPose Calculate(double width, double height, double dx, double dy, bool touch)
    {
        if (!ValidSize(width, height) || !double.IsFinite(dx) || !double.IsFinite(dy)) return Rest;
        double contraction = Math.Min((touch ? 3.6 : 2.4) / Math.Max(width, height), touch ? .05 : .04);
        double baseline = 1 - contraction;
        // Aspect normalization and rubber resistance follow Lenso's press model.
        // Bound input before normalization so remote captured pointers stay finite.
        double aspect = width / height;
        double x = Math.Clamp(dx / width, -100, 100);
        double y = Math.Clamp(dy / height, -100, 100);
        double length = Math.Sqrt(x * x + y * y);
        if (length == 0) return new(baseline, baseline, 0, 0);
        double nx = x / length, ny = y / length;
        double rubber = 1 - 1 / (1 + length / (5 * aspect));
        // Keep the stretch smaller than the inward press: no outward enlargement.
        double stretch = 1 + contraction * .45 * rubber * Math.Abs(Math.Abs(nx) - Math.Abs(ny));
        bool horizontal = Math.Abs(nx) > Math.Abs(ny);
        double sx = Math.Clamp(baseline * (horizontal ? stretch : 1 / stretch), .94, 1);
        double sy = Math.Clamp(baseline * (horizontal ? 1 / stretch : stretch), .94, 1);
        double shiftX = nx * 1.25 * rubber, shiftY = ny * 1.25 * rubber;
        return new(sx, sy,
            Math.Clamp(shiftX, -width * (1 - sx) / 2, width * (1 - sx) / 2),
            Math.Clamp(shiftY, -height * (1 - sy) / 2, height * (1 - sy) / 2));
    }

    private static bool ValidSize(double width, double height) =>
        double.IsFinite(width) && double.IsFinite(height) && width > 0 && height > 0;

    private static GlassPressSpring Tune(bool touch, bool pressed)
    {
        // Unit-mass stiffness/damping -> native natural period and damping ratio.
        var (stiffness, damping) = (touch, pressed) switch
        {
            (false, true) => (417.6, 29.2),
            (false, false) => (230, 23.7),
            (true, true) => (795, 38),
            (true, false) => (342, 13.5)
        };
        return new(damping / (2 * Math.Sqrt(stiffness)), TimeSpan.FromSeconds(2 * Math.PI / Math.Sqrt(stiffness)));
    }
}
