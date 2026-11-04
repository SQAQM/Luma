using System.Numerics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Luma.Preview.Services;

// Weak owners and compositor-only press feedback; no frame timers or image effects.
public static class GlassButtonEffects
{
    public static readonly DependencyProperty EnabledProperty=DependencyProperty.RegisterAttached("Enabled",typeof(bool),typeof(GlassButtonEffects),new PropertyMetadata(false,EnableChanged));
    private static readonly DependencyProperty OwnerProperty=DependencyProperty.RegisterAttached("Owner",typeof(object),typeof(GlassButtonEffects),new PropertyMetadata(null));
    private static readonly List<WeakReference<Motion>> Owners=[];
    private sealed class Policy{public bool Animations=true;}
    private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<XamlRoot,Policy> Policies=new();
    public static bool GetEnabled(DependencyObject value)=>(bool)value.GetValue(EnabledProperty);
    public static void SetEnabled(DependencyObject value,bool enabled)=>value.SetValue(EnabledProperty,enabled);
    public static void SetPolicy(XamlRoot root,bool animations)
    {
        var policy=Policies.GetOrCreateValue(root);if(policy.Animations==animations)return;policy.Animations=animations;
        for(int i=Owners.Count-1;i>=0;i--){if(Owners[i].TryGetTarget(out var owner)){if(ReferenceEquals(owner.Root,root))owner.Reset();}else Owners.RemoveAt(i);}
    }
    private static void EnableChanged(DependencyObject value,DependencyPropertyChangedEventArgs args)
    {
        if(value is not ButtonBase button)return;
        if(button.GetValue(OwnerProperty) is Motion old){old.Dispose();button.ClearValue(OwnerProperty);}
        if(args.NewValue is true){var motion=new Motion(button);button.SetValue(OwnerProperty,motion);}
    }
    private sealed class Motion:IDisposable
    {
        private readonly ButtonBase _button;private bool _down,_loaded;private long _enabledToken;
        public XamlRoot? Root=>_button.XamlRoot;
        public Motion(ButtonBase button){_button=button;button.Loaded+=Loaded;button.Unloaded+=Unloaded;if(button.IsLoaded)Attach();}
        private void Loaded(object sender,RoutedEventArgs e)=>Attach();
        private void Unloaded(object sender,RoutedEventArgs e)=>Detach();
        private void Attach(){if(_loaded)return;_loaded=true;Owners.RemoveAll(weak=>!weak.TryGetTarget(out _));Owners.Add(new(this));_button.LostFocus+=LostFocus;_enabledToken=_button.RegisterPropertyChangedCallback(Microsoft.UI.Xaml.Controls.Control.IsEnabledProperty,(_,_)=>Reset());_button.AddHandler(UIElement.PointerPressedEvent,new PointerEventHandler(Pressed),true);_button.AddHandler(UIElement.PointerReleasedEvent,new PointerEventHandler(Released),true);_button.PointerCanceled+=Released;_button.PointerCaptureLost+=Released;_button.AddHandler(UIElement.KeyDownEvent,new KeyEventHandler(KeyDown),true);_button.AddHandler(UIElement.KeyUpEvent,new KeyEventHandler(KeyUp),true);}
        private void Detach(){if(!_loaded)return;Reset();_loaded=false;Owners.RemoveAll(weak=>!weak.TryGetTarget(out var owner)||ReferenceEquals(owner,this));_button.LostFocus-=LostFocus;_button.UnregisterPropertyChangedCallback(Microsoft.UI.Xaml.Controls.Control.IsEnabledProperty,_enabledToken);_button.RemoveHandler(UIElement.PointerPressedEvent,new PointerEventHandler(Pressed));_button.RemoveHandler(UIElement.PointerReleasedEvent,new PointerEventHandler(Released));_button.PointerCanceled-=Released;_button.PointerCaptureLost-=Released;_button.RemoveHandler(UIElement.KeyDownEvent,new KeyEventHandler(KeyDown));_button.RemoveHandler(UIElement.KeyUpEvent,new KeyEventHandler(KeyUp));}
        private void LostFocus(object sender,RoutedEventArgs e)=>Reset();
        private void Pressed(object sender,PointerRoutedEventArgs e){if(_button.IsEnabled){_down=true;Scale(.975f);}}
        private void Released(object sender,PointerRoutedEventArgs e){if(_down){_down=false;Scale(1);}}
        private void KeyDown(object sender,KeyRoutedEventArgs e){if(e.Key is VirtualKey.Space or VirtualKey.Enter&&_button.IsEnabled){_down=true;Scale(.975f);}}
        private void KeyUp(object sender,KeyRoutedEventArgs e){if(e.Key is VirtualKey.Space or VirtualKey.Enter){_down=false;Scale(1);}}
        private void Scale(float amount){if(!_loaded)return;var visual=ElementCompositionPreview.GetElementVisual(_button);visual.CenterPoint=new((float)_button.ActualWidth/2,(float)_button.ActualHeight/2,0);if(Root is{} root&&!Policies.GetOrCreateValue(root).Animations){visual.StopAnimation("Scale");visual.Scale=Vector3.One;return;}var spring=visual.Compositor.CreateSpringVector3Animation();spring.FinalValue=new Vector3(amount,amount,1);spring.DampingRatio=1;spring.Period=TimeSpan.FromMilliseconds(100);visual.StartAnimation("Scale",spring);}
        public void Reset(){_down=false;if(_loaded){var visual=ElementCompositionPreview.GetElementVisual(_button);visual.StopAnimation("Scale");visual.Scale=Vector3.One;}}
        public void Dispose(){Detach();_button.Loaded-=Loaded;_button.Unloaded-=Unloaded;}
    }
}
