using System.Numerics;
using Luma.Core.Design;
using Microsoft.UI.Composition;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;

namespace Luma.Preview.Services;

// Native presentation on Luma's owned template-content GlassRoot only. The
// Button facade (including translation/elevation) and native activation stay
// untouched. Unrecognized templates quietly retain their native feedback.
// No frame callbacks, layout animations or per-image work.
public static partial class GlassButtonEffects
{
    public static readonly DependencyProperty EnabledProperty = DependencyProperty.RegisterAttached(
        "Enabled", typeof(bool), typeof(GlassButtonEffects), new PropertyMetadata(false, EnableChanged));
    private static readonly DependencyProperty OwnerProperty = DependencyProperty.RegisterAttached(
        "Owner", typeof(object), typeof(GlassButtonEffects), new PropertyMetadata(null));
    private static readonly List<WeakReference<Motion>> Owners = [];
    private sealed class Policy { public bool Animations = true; }
    private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<XamlRoot, Policy> Policies = new();

    public static bool GetEnabled(DependencyObject value) => (bool)value.GetValue(EnabledProperty);
    public static void SetEnabled(DependencyObject value, bool enabled) => value.SetValue(EnabledProperty, enabled);
    public static bool IsMotionEnabled(XamlRoot? root) =>
        root is null || !Policies.TryGetValue(root, out var policy) || policy.Animations;

    public static void SetPolicy(XamlRoot root, bool animations)
    {
        var policy = Policies.GetOrCreateValue(root);
        if (policy.Animations == animations) return;
        policy.Animations = animations;
        for (int i = Owners.Count - 1; i >= 0; i--)
        {
            if (Owners[i].TryGetTarget(out var owner))
            {
                if (ReferenceEquals(owner.Root, root)) owner.Reset();
            }
            else Owners.RemoveAt(i);
        }
    }

    private static void EnableChanged(DependencyObject value, DependencyPropertyChangedEventArgs args)
    {
        if (value is not ButtonBase button) return;
        if (button.GetValue(OwnerProperty) is Motion old)
        {
            old.Dispose();
            button.ClearValue(OwnerProperty);
        }
        if (args.NewValue is true) button.SetValue(OwnerProperty, new Motion(button));
    }

    private sealed partial class Motion : IDisposable
    {
        private readonly ButtonBase _button;
        private readonly GlassPressMotion _input = new();
        private bool _loaded;
        private long _enabledToken;
        private FrameworkElement? _contentRoot;
        private Visual? _visual;
        private CompositionPropertySet? _motion;
        private SpringVector3NaturalMotionAnimation? _scaleSpring, _shiftSpring;
        public XamlRoot? Root => _button.XamlRoot;

        public Motion(ButtonBase button)
        {
            _button = button;
            button.Loaded += Loaded;
            button.Unloaded += Unloaded;
            if (button.IsLoaded) Attach();
        }

        private void Loaded(object sender, RoutedEventArgs e) => Attach();
        private void Unloaded(object sender, RoutedEventArgs e) => Detach();

        private void Attach()
        {
            if (_loaded) return;
            _loaded = true;
            Owners.RemoveAll(weak => !weak.TryGetTarget(out _));
            Owners.Add(new(this));
            _button.LostFocus += LostFocus;
            _button.SizeChanged += SizeChanged;
            _enabledToken = _button.RegisterPropertyChangedCallback(
                Microsoft.UI.Xaml.Controls.Control.IsEnabledProperty, (_, _) => Reset());
            _button.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler(Pressed), true);
            _button.AddHandler(UIElement.PointerMovedEvent, new PointerEventHandler(Moved), true);
            _button.AddHandler(UIElement.PointerReleasedEvent, new PointerEventHandler(Released), true);
            _button.PointerCanceled += Canceled;
            _button.PointerCaptureLost += Released;
            _button.AddHandler(UIElement.KeyDownEvent, new KeyEventHandler(KeyDown), true);
            _button.AddHandler(UIElement.KeyUpEvent, new KeyEventHandler(KeyUp), true);
        }

        private void Detach()
        {
            if (!_loaded) return;
            Reset();
            _loaded = false;
            Owners.RemoveAll(weak => !weak.TryGetTarget(out var owner) || ReferenceEquals(owner, this));
            _button.LostFocus -= LostFocus;
            _button.SizeChanged -= SizeChanged;
            _button.UnregisterPropertyChangedCallback(Microsoft.UI.Xaml.Controls.Control.IsEnabledProperty, _enabledToken);
            _button.RemoveHandler(UIElement.PointerPressedEvent, new PointerEventHandler(Pressed));
            _button.RemoveHandler(UIElement.PointerMovedEvent, new PointerEventHandler(Moved));
            _button.RemoveHandler(UIElement.PointerReleasedEvent, new PointerEventHandler(Released));
            _button.PointerCanceled -= Canceled;
            _button.PointerCaptureLost -= Released;
            _button.RemoveHandler(UIElement.KeyDownEvent, new KeyEventHandler(KeyDown));
            _button.RemoveHandler(UIElement.KeyUpEvent, new KeyEventHandler(KeyUp));
        }

        private bool CanAnimate => _loaded && _button.IsEnabled && IsMotionEnabled(Root);
        private void LostFocus(object sender, RoutedEventArgs e) => Reset();
        private void SizeChanged(object sender, SizeChangedEventArgs e) => Reset();

        private void Pressed(object sender, PointerRoutedEventArgs e)
        {
            if (!CanAnimate) return;
            var point = e.GetCurrentPoint(null);
            if (e.Pointer.PointerDeviceType == PointerDeviceType.Mouse && !point.Properties.IsLeftButtonPressed) return;
            // Root coordinates and untransformed layout size prevent a rapid
            // repress or a moving visual from compounding the deformation.
            if (_input.TryPressPointer(e.Pointer.PointerId, _button.ActualWidth, _button.ActualHeight,
                    point.Position.X, point.Position.Y, e.Pointer.PointerDeviceType != PointerDeviceType.Mouse))
                Animate();
        }

        private void Moved(object sender, PointerRoutedEventArgs e)
        {
            if (!_input.IsPressed) return;
            if (!CanAnimate) { Reset(); return; }
            var point = e.GetCurrentPoint(null);
            if (e.Pointer.PointerDeviceType == PointerDeviceType.Mouse && !point.Properties.IsLeftButtonPressed)
            {
                Released(sender, e);
                return;
            }
            if (_input.MovePointer(e.Pointer.PointerId, point.Position.X, point.Position.Y)) Animate();
        }

        private void Released(object sender, PointerRoutedEventArgs e)
        {
            if (_input.ReleasePointer(e.Pointer.PointerId)) Animate();
        }

        private void Canceled(object sender, PointerRoutedEventArgs e)
        {
            if (_input.ReleasePointer(e.Pointer.PointerId)) Reset();
        }

        private static GlassPressKey? ActivationKey(VirtualKey key) => key switch
        {
            VirtualKey.Space => GlassPressKey.Space,
            VirtualKey.Enter => GlassPressKey.Enter,
            _ => null
        };

        private void KeyDown(object sender, KeyRoutedEventArgs e)
        {
            if (CanAnimate && ActivationKey(e.Key) is { } key &&
                _input.TryPressKey(key, _button.ActualWidth, _button.ActualHeight)) Animate();
        }

        private void KeyUp(object sender, KeyRoutedEventArgs e)
        {
            if (ActivationKey(e.Key) is { } key && _input.ReleaseKey(key)) Animate();
        }

        private bool Prepare()
        {
            if (_motion is not null) return true;
            _button.ApplyTemplate();
            FrameworkElement? content = null;
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(_button); i++)
            {
                if (VisualTreeHelper.GetChild(_button, i) is FrameworkElement { Name: "GlassRoot" } candidate)
                {
                    content = candidate;
                    break;
                }
            }
            // Only Luma's known template root belongs to this effect. Never
            // acquire the Button handout or mix it with its facade properties.
            if (content is null || content.ActualWidth <= 0 || content.ActualHeight <= 0 ||
                content.Translation != Vector3.Zero || content.Scale != Vector3.One) return false;
            _contentRoot = content;
            ElementCompositionPreview.SetIsTranslationEnabled(content, true);
            _visual = ElementCompositionPreview.GetElementVisual(content);
            _visual.CenterPoint = new((float)content.ActualWidth / 2, (float)content.ActualHeight / 2, 0);
            var compositor = _visual.Compositor;
            _motion = compositor.CreatePropertySet();
            _motion.InsertVector3("Scale", Vector3.One);
            _motion.InsertVector3("Shift", Vector3.Zero);
            _motion.InsertVector3("Bounds", new((float)content.ActualWidth, (float)content.ActualHeight, 0));
            using var scale = compositor.CreateExpressionAnimation(
                "Vector3(Clamp(motion.Scale.X, 0.94, 1), Clamp(motion.Scale.Y, 0.94, 1), 1)");
            scale.SetReferenceParameter("motion", _motion);
            _visual.StartAnimation("Scale", scale);
            // Clamp the presented translation against the scale every frame on
            // the compositor. Independent spring phases and release overshoot
            // cannot pull an edge into an adjacent compact button.
            using var shift = compositor.CreateExpressionAnimation(
                "Vector3(Clamp(Clamp(motion.Shift.X, -1.25, 1.25), -motion.Bounds.X * (1 - Clamp(motion.Scale.X, 0.94, 1)) / 2, motion.Bounds.X * (1 - Clamp(motion.Scale.X, 0.94, 1)) / 2), " +
                "Clamp(Clamp(motion.Shift.Y, -1.25, 1.25), -motion.Bounds.Y * (1 - Clamp(motion.Scale.Y, 0.94, 1)) / 2, motion.Bounds.Y * (1 - Clamp(motion.Scale.Y, 0.94, 1)) / 2), 0)");
            shift.SetReferenceParameter("motion", _motion);
            _visual.StartAnimation("Translation", shift);
            _scaleSpring = compositor.CreateSpringVector3Animation();
            _shiftSpring = compositor.CreateSpringVector3Animation();
            return true;
        }

        private void Animate()
        {
            if (!CanAnimate) { Reset(); return; }
            if (!Prepare()) return;
            var pose = _input.Pose;
            StartSpring("Scale", _scaleSpring!, new((float)pose.ScaleX, (float)pose.ScaleY, 1));
            StartSpring("Shift", _shiftSpring!, new((float)pose.ShiftX, (float)pose.ShiftY, 0));
        }

        private void StartSpring(string property, SpringVector3NaturalMotionAnimation spring, Vector3 target)
        {
            spring.FinalValue = target;
            spring.DampingRatio = (float)_input.Spring.DampingRatio;
            spring.Period = _input.Spring.Period;
            // Leave InitialValue unset: replacement animations start from the
            // current compositor presentation, including an interrupted return.
            _motion!.StartAnimation(property, spring);
        }

        public void Reset()
        {
            _input.Reset();
            if (_motion is null) return;
            _visual!.StopAnimation("Scale");
            _visual.StopAnimation("Translation");
            _visual.Scale = Vector3.One;
            _visual.Properties.InsertVector3("Translation", Vector3.Zero);
            _motion.StopAnimation("Scale");
            _motion.StopAnimation("Shift");
            _scaleSpring?.Dispose(); _shiftSpring?.Dispose(); _motion.Dispose();
            _scaleSpring = null; _shiftSpring = null; _motion = null; _visual = null; _contentRoot = null;
        }

        public void Dispose()
        {
            Detach();
            _button.Loaded -= Loaded;
            _button.Unloaded -= Unloaded;
        }
    }
}
