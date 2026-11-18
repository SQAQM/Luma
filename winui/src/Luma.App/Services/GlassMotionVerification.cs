using System.Numerics;
using Luma.Core.Design;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Services;

// Strictly opt-in native diagnostics. The factory creates an inert visible
// fixture; this probe drives controller state, never OS input or activation.
public static partial class GlassButtonEffects
{
    private static readonly object NativeMotionProbeTag = new();

    internal static Button CreateNativeMotionVerificationButton()
    {
        if (Environment.GetEnvironmentVariable("LUMA_VIEWER_GLASS_UI_TEST") != "1")
            throw new InvalidOperationException("Native glass motion diagnostics are disabled.");
        var button = new Button
        {
            Name = "LumaGlassMotionProbe", Tag = NativeMotionProbeTag, Content = "反馈测试",
            Style = (Style)Application.Current.Resources["LumaGlassGroupIcon"],
            Width = 160, Height = 36, IsTabStop = false, Translation = new Vector3(4, 2, 6),
            HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top
        };
        SetEnabled(button, true);
        return button;
    }

    internal static async Task VerifyNativeMotion(ButtonBase button, List<object> checks, Func<string, Task> capture)
    {
        if (Environment.GetEnvironmentVariable("LUMA_VIEWER_GLASS_UI_TEST") != "1") return;
        // Do not accept an application button: driving its state would give a
        // misleading interaction claim and could disturb actual operations.
        if (!ReferenceEquals(button.Tag, NativeMotionProbeTag))
            throw new InvalidOperationException("Native motion probe requires its own factory-created button.");
        if (!button.IsLoaded || button.XamlRoot is null || button.ActualWidth <= 0 || button.ActualHeight <= 0)
            throw new InvalidOperationException("Mount and lay out the native motion fixture before probing it.");
        for (DependencyObject? ancestor = button; ancestor is not null; ancestor = VisualTreeHelper.GetParent(ancestor))
            if (ancestor is UIElement element && (element.Visibility != Visibility.Visible || element.Opacity <= 0))
                throw new InvalidOperationException("Native motion fixture must be visible.");
        var bounds = button.TransformToVisual(button.XamlRoot.Content).TransformBounds(
            new(0, 0, button.ActualWidth, button.ActualHeight));
        if (bounds.Right <= 0 || bounds.Bottom <= 0 || bounds.Left >= button.XamlRoot.Size.Width || bounds.Top >= button.XamlRoot.Size.Height)
            throw new InvalidOperationException("Native motion fixture must be inside the visible XAML root.");
        SetEnabled(button, true);
        if (button.GetValue(OwnerProperty) is not Motion owner)
            throw new InvalidOperationException("Native motion fixture has no production adapter.");
        await owner.VerifyNative(checks, capture);
    }

    private sealed partial class Motion
    {
        internal async Task VerifyNative(List<object> checks, Func<string, Task> capture)
        {
            const uint pointer = 0x4C554D41;
            var root = Root!;
            var parentTranslation = _button.Translation;
            FrameworkElement? targetRoot = null;
            bool originalPolicy = IsMotionEnabled(root), originalEnabled = _button.IsEnabled;
            void Check(bool passed, string name) => checks.Add(new
            {
                passed, name = "native motion controller/compositor: " + name,
                scope = "factory-owned synthetic button; direct controller state; no physical pointer or keyboard routing claim"
            });
            bool Resting()
            {
                if (targetRoot is null) return false;
                var visual = ElementCompositionPreview.GetElementVisual(targetRoot);
                return !_input.IsPressed && _motion is null && _scaleSpring is null && _shiftSpring is null &&
                    _button.Translation == parentTranslation &&
                    visual.Scale == Vector3.One &&
                    visual.Properties.TryGetVector3("Translation", out var shift) == CompositionGetValueStatus.Succeeded && shift == Vector3.Zero;
            }
            bool Press(bool touch = false)
            {
                bool started = _input.TryPressPointer(pointer, _button.ActualWidth, _button.ActualHeight, 80, 18, touch);
                Animate();
                return started;
            }
            try
            {
                _button.IsEnabled = true;
                SetPolicy(root, true);
                Reset();
                Check(parentTranslation == new Vector3(4, 2, 6), "fixture starts with nonzero parent translation and elevation");
                Check(Press() && _motion is not null && _visual is not null && _scaleSpring is not null && _shiftSpring is not null,
                    "production expressions property set and spring animations initialize without exception");
                targetRoot = _contentRoot;
                Check(targetRoot is { Name: "GlassRoot" } && !ReferenceEquals(targetRoot, _button) &&
                    _button.Translation == parentTranslation,
                    "native handout targets template content while preserving parent translation and elevation");
                var properties = _motion;
                var pointerPose = _input.Pose;
                Check(properties is not null && properties.TryGetVector3("Bounds", out var size) == CompositionGetValueStatus.Succeeded &&
                    size.X == (float)_button.ActualWidth && size.Y == (float)_button.ActualHeight,
                    "native bounds use the resting layout size");
                await Task.Delay(100);
                Check(_input.MovePointer(pointer, 140, 18), "held pointer controller retargets drag");
                Animate();
                Check(ReferenceEquals(properties, _motion) && _input.Pose.ScaleX > _input.Pose.ScaleY &&
                    _scaleSpring!.FinalValue == new Vector3((float)_input.Pose.ScaleX, (float)_input.Pose.ScaleY, 1) &&
                    _shiftSpring!.FinalValue == new Vector3((float)_input.Pose.ShiftX, (float)_input.Pose.ShiftY, 0),
                    "same native property set receives anisotropic scale and translation targets");
                await capture("motion-pointer-drag");

                Check(_input.ReleasePointer(pointer), "pointer controller releases");
                Animate();
                await Task.Delay(35);
                Check(Press() && ReferenceEquals(properties, _motion) && _input.Pose == pointerPose &&
                    _button.Translation == parentTranslation,
                    "repress during native return reuses the adapter without compounding its target or changing parent translation");
                Reset();
                Check(Resting(), "explicit cancel reset removes expressions and disposes native resources");

                Check(_input.TryPressKey(GlassPressKey.Space, _button.ActualWidth, _button.ActualHeight), "keyboard controller starts");
                Animate();
                LostFocus(_button, new RoutedEventArgs());
                Check(Resting(), "production lost-focus handler restores native rest");

                Press();
                _button.IsEnabled = false;
                Check(Resting(), "native IsEnabled property callback resets a held press");
                _button.IsEnabled = true;
                Press(true);
                Check(_input.Pose.ScaleX < pointerPose.ScaleX && _scaleSpring!.DampingRatio < 1,
                    "touch controller initializes its distinct native spring");
                await capture("motion-touch-press");
                SetPolicy(root, false);
                Check(!IsMotionEnabled(root) && Resting(), "root reduce-motion policy resets native scale translation and resources");
                Press();
                Check(Resting(), "production Animate allocates no native resources under reduced motion");
                await capture("motion-reduced");

                SetPolicy(root, true);
                Press();
                SetEnabled(_button, false);
                Check(!_loaded && _button.GetValue(OwnerProperty) is null && Resting() &&
                    !Owners.Any(weak => weak.TryGetTarget(out var current) && ReferenceEquals(current, this)),
                    "attached-property removal disposes native resources and detaches weak ownership");
            }
            catch (Exception error)
            {
                checks.Add(new
                {
                    passed = false, name = "native motion controller/compositor probe completed", error = error.ToString(),
                    scope = "factory-owned synthetic button; no physical input routing claim"
                });
            }
            finally
            {
                Reset();
                SetEnabled(_button, false);
                _button.IsEnabled = originalEnabled;
                SetPolicy(root, originalPolicy);
            }
        }
    }
}
