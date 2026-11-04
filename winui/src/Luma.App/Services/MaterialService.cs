using Luma.Core.Design;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.UI.ViewManagement;

namespace Luma.Preview.Services;

// Owns a fixed number of compositor layers; never creates effects for image tiles.
internal sealed class MaterialService:IDisposable
{
 internal static string InitializationStep{get;private set;}="new";
 private readonly UISettings _settings=new();
 private readonly AccessibilitySettings _accessibility=new();
 private readonly DispatcherQueue _dispatcher;
 private readonly Window _window;
 private readonly FrameworkElement _root;
 private readonly Action<MaterialPlan> _applyTint;
 private bool _requested=true,_disposed,_failed,_supported,_effectsHook,_contrastHook,_reduceMotion;
 private ElementTheme _theme;
 private MaterialPlan? _plan;
 public event Action? Changed;
 public MaterialService(DispatcherQueue dispatcher,Window window,FrameworkElement root,Action<MaterialPlan> applyTint)
 {
  InitializationStep="targets";_dispatcher=dispatcher;_window=window;_root=root;_applyTint=applyTint;
  InitializationStep="capability";try{_supported=DesktopAcrylicController.IsSupported();}catch(Exception){_supported=false;}
  InitializationStep="effects_event";try{_settings.AdvancedEffectsEnabledChanged+=EffectsChanged;_effectsHook=true;}catch(System.Runtime.InteropServices.COMException e) when(e.HResult==unchecked((int)0x80070490)){}
  InitializationStep="contrast_event";try{_accessibility.HighContrastChanged+=ContrastChanged;_contrastHook=true;}catch(System.Runtime.InteropServices.COMException e) when(e.HResult==unchecked((int)0x80070490)){}InitializationStep="ready";
 }
 public object Snapshot=>new{requested=_requested,supported=_supported,advancedEffects=_settings.AdvancedEffectsEnabled,highContrast=_accessibility.HighContrast,acrylicRequested=_plan?.UseAcrylic??false,reason=_plan?.Reason??"initializing",nativeLayers=_window.SystemBackdrop is not null?1:0,appearance="system_managed",failed=_failed,effectsEventHooked=_effectsHook,contrastEventHooked=_contrastHook,disposed=_disposed};
 public void Apply(bool requested,ElementTheme theme,bool reduceMotion=false)
 {
  if(_disposed)return;
  _requested=requested;
  _reduceMotion=reduceMotion;
  if(_root.XamlRoot is{} xamlRoot)GlassButtonEffects.SetPolicy(xamlRoot,!reduceMotion&&_settings.AnimationsEnabled&&!_accessibility.HighContrast);
  var plan=MaterialPolicy.Choose(requested,_settings.AdvancedEffectsEnabled,_accessibility.HighContrast,_supported&&!_failed);
  bool themeChanged=_theme!=theme;_theme=theme;
  if(plan==_plan&&!themeChanged)return;
  bool layersChanged=_plan?.UseAcrylic!=plan.UseAcrylic;
  if(layersChanged)
  {
   try
   {
    _window.SystemBackdrop=plan.UseAcrylic?new DesktopAcrylicBackdrop():null;
   }
   catch(Exception)
   {
    _failed=true;plan=new(false,"unsupported");_window.SystemBackdrop=null;
   }
  }
  _plan=plan;
  foreach(var dictionary in _root.Resources.MergedDictionaries)
   foreach(var value in dictionary.ThemeDictionaries.Values.OfType<ResourceDictionary>())
   {
    foreach(var brush in value.Values.OfType<AcrylicBrush>())brush.AlwaysUseFallback=!plan.UseAcrylic;
    if(!ReferenceEquals(value,dictionary.ThemeDictionaries["HighContrast"])&&value.TryGetValue("LumaGlassBase",out var glassBase)&&glassBase is SolidColorBrush fill)
    {
     bool dark=ReferenceEquals(value,dictionary.ThemeDictionaries["Default"]);
     // High contrast retains system brushes. Other windows never mutate this root's dictionary.
     fill.Color=plan.UseAcrylic?Windows.UI.Color.FromArgb(dark?(byte)20:(byte)48,255,255,255):dark?Windows.UI.Color.FromArgb(255,40,49,61):Windows.UI.Color.FromArgb(255,247,249,252);
    }
    foreach(string key in new[]{"LumaGlassSpecular","LumaGlassUnderEdge","LumaGlassRim","LumaGlassInnerRim"})if(value.TryGetValue(key,out var glass)&&glass is Brush brush)brush.Opacity=plan.UseAcrylic?1:0;
    foreach(string key in new[]{"LumaCanvas","LumaSidebar","LumaSurface"})
     if(value.TryGetValue(key,out var resource)&&resource is SolidColorBrush brush)brush.Opacity=plan.UseAcrylic?(key=="LumaSurface"?.38:0):1;
   }
  _applyTint(plan);
 }
 private void EffectsChanged(UISettings sender,object args)=>QueueRefresh();
 private void ContrastChanged(AccessibilitySettings sender,object args)=>QueueRefresh();
 private void QueueRefresh()=>_dispatcher.TryEnqueue(()=>{if(!_disposed){Apply(_requested,_theme,_reduceMotion);Changed?.Invoke();}});
 public void Dispose()
 {
  if(_disposed)return;_disposed=true;
  if(_effectsHook)_settings.AdvancedEffectsEnabledChanged-=EffectsChanged;if(_contrastHook)_accessibility.HighContrastChanged-=ContrastChanged;
  _window.SystemBackdrop=null;
 }
}
