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
 private readonly WindowGlass _glass;
 private readonly FrameworkElement _root;
 private readonly Border _wash;
 private readonly Action<MaterialPlan> _applyChrome;
 private bool _disposed,_failed,_hostFailed,_clearFailed,_supported,_effectsHook,_contrastHook,_reduceMotion;
 private string _renderMode="Solid";
 private string? _effectError;
 internal string RenderMode=>_renderMode;
 private double _paneLevel=-1;
 private BackgroundMaterial _material=BackgroundMaterial.Acrylic;
 private int _transparency=MaterialPolicy.DefaultTransparency;
 private ElementTheme _theme;
 private MaterialPlan? _plan;
 private Brush? _customBackground;
 public event Action? Changed;
 public MaterialService(DispatcherQueue dispatcher,Window window,FrameworkElement root,Border wash,Action<MaterialPlan> applyChrome)
 {
  InitializationStep="targets";_dispatcher=dispatcher;_window=window;_glass=new(window);_root=root;_wash=wash;_applyChrome=applyChrome;
  InitializationStep="capability";try{_supported=DesktopAcrylicController.IsSupported();}catch(Exception){_supported=false;}
  InitializationStep="effects_event";try{_settings.AdvancedEffectsEnabledChanged+=EffectsChanged;_effectsHook=true;}catch(System.Runtime.InteropServices.COMException e) when(e.HResult==unchecked((int)0x80070490)){}
  InitializationStep="contrast_event";try{_accessibility.HighContrastChanged+=ContrastChanged;_contrastHook=true;}catch(System.Runtime.InteropServices.COMException e) when(e.HResult==unchecked((int)0x80070490)){}InitializationStep="ready";
 }
 public object Snapshot=>new{requested=_material!=BackgroundMaterial.Solid,material=_material.ToString(),backgroundTransparency=_transparency,
  backgroundWash=_wash.Opacity,supported=_supported,advancedEffects=_settings.AdvancedEffectsEnabled,highContrast=_accessibility.HighContrast,
  acrylicRequested=_plan?.UseAcrylic??false,reason=_plan?.Reason??"initializing",nativeLayers=_window.SystemBackdrop is not null?1:0,
  appearance="system_managed",renderMode=_renderMode,hostEffectFailed=_hostFailed,clearFailed=_clearFailed,effectError=_effectError,
  blurAmount=_renderMode=="HostEffect"?BackgroundLook.For(_material).Blur:0,
  failed=_failed,effectsEventHooked=_effectsHook,contrastEventHooked=_contrastHook,disposed=_disposed};
 public void Apply(BackgroundMaterial material,int transparency,ElementTheme theme,bool reduceMotion=false,Brush? customBackground=null)
 {
  if(_disposed)return;
  _reduceMotion=reduceMotion;
  if(_root.XamlRoot is{} xamlRoot){bool motion=!reduceMotion&&_settings.AnimationsEnabled&&!_accessibility.HighContrast;GlassButtonEffects.SetPolicy(xamlRoot,motion);GlassSurfaceEffects.SetPolicy(xamlRoot,motion,!_accessibility.HighContrast);}
  var plan=MaterialPolicy.Choose(customBackground is null&&material!=BackgroundMaterial.Solid,_settings.AdvancedEffectsEnabled,_accessibility.HighContrast,(_supported||!_clearFailed)&&!_failed);
  bool appearanceChanged=plan!=_plan||_theme!=theme||_material!=material||!ReferenceEquals(_customBackground,customBackground);
  bool transparencyChanged=_transparency!=transparency;
  _material=material;_transparency=transparency;_theme=theme;_customBackground=customBackground;
  if(!appearanceChanged&&!transparencyChanged)return;
  var look=BackgroundLook.For(material);
  string render=SelectBackdrop(plan,look,transparency);
  bool renderChanged=_renderMode!=render;
  _renderMode=render;
  bool transparentBackground=render!="Solid";
  bool paintedBackground=customBackground is not null&&plan.Reason!="high_contrast";
  bool glassDetails=plan.UseAcrylic&&transparency>0||paintedBackground&&_settings.AdvancedEffectsEnabled;
  if(renderChanged)appearanceChanged=true;
  if(appearanceChanged)
  {
   var colors=_root.Resources.MergedDictionaries[0].ThemeDictionaries[
    plan.Reason=="high_contrast"?"HighContrast":theme==ElementTheme.Dark?"Default":"Light"];
   var palette=(ResourceDictionary)colors;
   var canvas=(SolidColorBrush)palette["LumaCanvas"];
   _plan=plan;
   ApplyBrushes(look,glassDetails);
   _wash.Background=paintedBackground?customBackground:transparentBackground?(Brush)palette[material switch
   {
    BackgroundMaterial.Frosted=>"LumaFrostedTint",
    BackgroundMaterial.Liquid=>"LumaLiquidTint",
    _=>"LumaMaterialTint"
   }]:canvas;
   _applyChrome(plan);
  }
  double wash=MaterialPolicy.BackgroundWash(transparency);
  _wash.Opacity=transparentBackground?wash+(1-wash)*look.Coat*MaterialPolicy.EffectWeight(transparency):1;
  var paneLevel=transparentBackground?Math.Min(1,wash/.15)*look.PaneWeight:paintedBackground?1:-1;
  if(paneLevel!=_paneLevel)
  {
   _paneLevel=paneLevel;
   foreach(var palette in _root.Resources.MergedDictionaries.SelectMany(dict=>dict.ThemeDictionaries.Values.OfType<ResourceDictionary>()))
    foreach(var (key,opacity) in new[]{("LumaSidebar",.13),("LumaWorkspace",.12),("LumaViewerShell",.12),("LumaViewerMatte",.10)})
     if(palette.TryGetValue(key,out var value)&&value is SolidColorBrush brush)
      brush.Opacity=paneLevel<0?1:opacity*paneLevel;
  }
 }
 private string SelectBackdrop(MaterialPlan plan,BackgroundLook look,int transparency)
 {
  if(!plan.UseAcrylic||transparency==0)
  {
   _glass.Dispose();
   if(_window.SystemBackdrop is not null)_window.SystemBackdrop=null;
   return "Solid";
  }
  bool clear=transparency==100;
  bool clearGlass=_material==BackgroundMaterial.Liquid;
  if(!_clearFailed&&(clear||clearGlass||!_hostFailed))
  {
   try
   {
    if(_window.SystemBackdrop is DesktopAcrylicBackdrop)_window.SystemBackdrop=null;
    if(clear){_glass.ShowClear();return "Clear";}
    if(clearGlass){_glass.ShowClear();return "ClearGlass";}
    _glass.ShowEffect(look,MaterialPolicy.EffectWeight(transparency));
    return "HostEffect";
   }
   catch(Exception error) when(error is System.Runtime.InteropServices.COMException or InvalidOperationException or NotSupportedException or
    ArgumentException or InvalidCastException or TypeLoadException or DllNotFoundException)
   {
    _effectError=error.GetType().Name+" "+error.HResult.ToString("X8");
    if(clear||clearGlass||!_glass.IsOpen)_clearFailed=true;
    else _hostFailed=true;
    _glass.Dispose();
   }
  }
  if(clear||!_supported)return "Solid";
  try
  {
   _window.SystemBackdrop??=new DesktopAcrylicBackdrop();
   return "SystemAcrylic";
  }
  catch(Exception error) when(error is System.Runtime.InteropServices.COMException or InvalidOperationException or NotSupportedException)
  {
   _failed=true;
   return "Solid";
  }
 }
 private void ApplyBrushes(BackgroundLook look,bool acrylic)
 {
  foreach(var dictionary in _root.Resources.MergedDictionaries)
   foreach(var value in dictionary.ThemeDictionaries.Values.OfType<ResourceDictionary>())
   {
    foreach(var brush in value.Values.OfType<AcrylicBrush>())brush.AlwaysUseFallback=!acrylic;
    if(value.TryGetValue("LumaDockFill",out var dockFill)&&dockFill is AcrylicBrush dock)
     dock.TintOpacity=look.ControlTint;
    if(!ReferenceEquals(value,dictionary.ThemeDictionaries["HighContrast"])&&value.TryGetValue("LumaGlassBase",out var glassBase)&&glassBase is SolidColorBrush fill)
    {
     bool dark=ReferenceEquals(value,dictionary.ThemeDictionaries["Default"]);
     byte strength=(byte)Math.Round((dark?20:48)*look.PaneWeight);
     fill.Color=acrylic?Windows.UI.Color.FromArgb(strength,255,255,255):dark?Windows.UI.Color.FromArgb(255,40,49,61):Windows.UI.Color.FromArgb(255,247,249,252);
    }
    foreach(var (key,opacity) in new[]{("LumaGlassSpecular",look.Highlight),("LumaGlassUnderEdge",look.Rim),
     ("LumaGlassRim",look.Rim),("LumaGlassInnerRim",look.Rim)})
     if(value.TryGetValue(key,out var glass)&&glass is Brush brush)brush.Opacity=acrylic?opacity:0;
    if(value.TryGetValue("LumaSurface",out var surface)&&surface is SolidColorBrush surfaceBrush)
     surfaceBrush.Opacity=acrylic?.38*look.PaneWeight:1;
   }
 }
 private void EffectsChanged(UISettings sender,object args)=>QueueRefresh();
 private void ContrastChanged(AccessibilitySettings sender,object args)=>QueueRefresh();
 private void QueueRefresh()=>_dispatcher.TryEnqueue(()=>{if(!_disposed){Apply(_material,_transparency,_theme,_reduceMotion,_customBackground);Changed?.Invoke();}});
 public void Dispose()
 {
  if(_disposed)return;_disposed=true;
  if(_effectsHook)_settings.AdvancedEffectsEnabledChanged-=EffectsChanged;if(_contrastHook)_accessibility.HighContrastChanged-=ContrastChanged;
  _glass.Dispose();
  _window.SystemBackdrop=null;
 }
}
