using System.Numerics;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Services;

// A small set of chrome surfaces, never image/model tiles. Visibility remains
// owned by XAML; the compositor handles transitions without timers or input locks.
public static class GlassSurfaceEffects
{
 public static readonly DependencyProperty EnabledProperty=DependencyProperty.RegisterAttached("Enabled",typeof(bool),typeof(GlassSurfaceEffects),new PropertyMetadata(false,Changed));
 public static readonly DependencyProperty ReceiverProperty=DependencyProperty.RegisterAttached("Receiver",typeof(UIElement),typeof(GlassSurfaceEffects),new PropertyMetadata(null,ReceiverChanged));
 private static readonly DependencyProperty OwnerProperty=DependencyProperty.RegisterAttached("Owner",typeof(object),typeof(GlassSurfaceEffects),new PropertyMetadata(null));
 private sealed class Policy{public bool Motion=true,Shadows=true;}
 private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<XamlRoot,Policy> Policies=new();
 private static readonly List<WeakReference<Surface>> Owners=[];
 public static bool GetEnabled(DependencyObject target)=>(bool)target.GetValue(EnabledProperty);
 public static void SetEnabled(DependencyObject target,bool value)=>target.SetValue(EnabledProperty,value);
 public static UIElement? GetReceiver(DependencyObject target)=>target.GetValue(ReceiverProperty) as UIElement;
 public static void SetReceiver(DependencyObject target,UIElement? value)=>target.SetValue(ReceiverProperty,value);
 private static void ReceiverChanged(DependencyObject target,DependencyPropertyChangedEventArgs args){if(target.GetValue(OwnerProperty) is Surface surface)surface.Apply();}
 public static void SetPolicy(XamlRoot root,bool motion,bool shadows)
 {
  var policy=Policies.GetOrCreateValue(root);if(policy.Motion==motion&&policy.Shadows==shadows)return;
  policy.Motion=motion;policy.Shadows=shadows;
  for(int i=Owners.Count-1;i>=0;i--){if(Owners[i].TryGetTarget(out var owner)){if(ReferenceEquals(owner.Root,root))owner.Apply();}else Owners.RemoveAt(i);}
 }
 internal static object Snapshot(XamlRoot root)=>new{surfaces=Owners.Count(w=>w.TryGetTarget(out var owner)&&owner.Attached&&ReferenceEquals(owner.Root,root)),motion=Policies.GetOrCreateValue(root).Motion,shadows=Policies.GetOrCreateValue(root).Shadows};
 internal static bool HasTransitions(FrameworkElement element)=>element.GetValue(OwnerProperty) is Surface{Transitions:true};
 private static void Changed(DependencyObject target,DependencyPropertyChangedEventArgs args)
 {
  if(target is not FrameworkElement element)return;
  if(element.GetValue(OwnerProperty) is Surface previous){previous.Dispose();element.ClearValue(OwnerProperty);}
  if(args.NewValue is true)element.SetValue(OwnerProperty,new Surface(element));
 }
 private sealed class Surface:IDisposable
 {
  private readonly FrameworkElement _element;
  private ThemeShadow? _shadow;private Shadow? _originalShadow;private Vector3 _translation;
  private Visual? _visual;
  private CompositionAnimationGroup? _show,_hide;
  public bool Attached{get;private set;}
  public bool Transitions=>_show is not null||_hide is not null;
  public XamlRoot? Root=>_element.XamlRoot;
  public Surface(FrameworkElement element){_element=element;element.Loaded+=Loaded;element.Unloaded+=Unloaded;if(element.IsLoaded)Attach();}
  private void Loaded(object sender,RoutedEventArgs e)=>Attach();
  private void Unloaded(object sender,RoutedEventArgs e)=>Detach();
  private void Attach()
  {
   if(Attached||_element.Translation!=Vector3.Zero)return;
   Attached=true;_originalShadow=_element.Shadow;
   _visual=ElementCompositionPreview.GetElementVisual(_element);ElementCompositionPreview.SetIsTranslationEnabled(_element,true);
   _translation=_visual.Properties.TryGetVector3("Translation",out var previous)==CompositionGetValueStatus.Succeeded?previous:Vector3.Zero;
   Owners.RemoveAll(w=>!w.TryGetTarget(out _));Owners.Add(new(this));Apply();
  }
  public void Apply()
  {
   if(!Attached||Root is not{} root)return;
   ClearAnimations();var policy=Policies.GetOrCreateValue(root);
   if(policy.Shadows){_shadow??=new ThemeShadow();_shadow.Receivers.Clear();if(GetReceiver(_element) is{} receiver)_shadow.Receivers.Add(receiver);}
   _element.Shadow=policy.Shadows?_shadow:_originalShadow;
   var resting=_translation+new Vector3(0,0,policy.Shadows?12:0);_visual!.Properties.InsertVector3("Translation",resting);
   if(!policy.Motion)return;
   var visual=_visual!;var compositor=visual.Compositor;
   CompositionAnimationGroup Transition(bool show)
   {
    var group=compositor.CreateAnimationGroup();
    var ease=compositor.CreateCubicBezierEasingFunction(new(.16f,1),new(.3f,1));
    var opacity=compositor.CreateScalarKeyFrameAnimation();opacity.Target="Opacity";opacity.Duration=TimeSpan.FromMilliseconds(show?180:110);
    opacity.InsertExpressionKeyFrame(0,"this.StartingValue");opacity.InsertKeyFrame(1,show?(float)_element.Opacity:0,ease);group.Add(opacity);
    var shift=compositor.CreateVector3KeyFrameAnimation();shift.Target="Translation";shift.Duration=opacity.Duration;
    shift.InsertExpressionKeyFrame(0,"this.StartingValue");shift.InsertKeyFrame(1,resting+new Vector3(0,show?0:4,0),ease);group.Add(shift);
    return group;
   }
   if(_element.Visibility==Visibility.Collapsed)visual.Opacity=0;
   _show=Transition(true);_hide=Transition(false);
   ElementCompositionPreview.SetImplicitShowAnimation(_element,_show);ElementCompositionPreview.SetImplicitHideAnimation(_element,_hide);
  }
  private void ClearAnimations()
  {
   ElementCompositionPreview.SetImplicitShowAnimation(_element,null);ElementCompositionPreview.SetImplicitHideAnimation(_element,null);
   if(_visual is{} visual){visual.StopAnimation("Opacity");visual.StopAnimation("Translation");visual.Opacity=(float)_element.Opacity;}
   _show?.Dispose();_hide?.Dispose();_show=_hide=null;
  }
  private void Detach()
  {
   if(!Attached)return;ClearAnimations();_element.Shadow=_originalShadow;_visual!.Properties.InsertVector3("Translation",_translation);_visual=null;Attached=false;
   Owners.RemoveAll(w=>!w.TryGetTarget(out var owner)||ReferenceEquals(owner,this));
  }
  public void Dispose(){Detach();_element.Loaded-=Loaded;_element.Unloaded-=Unloaded;}
 }
}
