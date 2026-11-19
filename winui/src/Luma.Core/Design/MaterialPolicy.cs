namespace Luma.Core.Design;

public enum BackgroundMaterial { Acrylic, Frosted, Liquid, Solid }

public sealed record MaterialPlan(bool UseAcrylic,string Reason);

public readonly record struct BackgroundLook(float Blur,float Saturation,double Coat,double ControlTint,double PaneWeight,double Highlight,double Rim)
{
 public static BackgroundLook For(BackgroundMaterial material)=>material switch
 {
  BackgroundMaterial.Frosted=>new(48,.43f,.26,.43,1.4,.26,.42),
  BackgroundMaterial.Liquid=>new(0,1,.02,.11,.62,1,.92),
  _=>new(13,1.06f,.07,.23,1,.55,.62)
 };
}

public static class MaterialPolicy
{
 public const int DefaultTransparency=85;
 public static int NormalizeTransparency(int value)=>Math.Clamp(value,0,100);
 public static double BackgroundWash(int transparency)=>1-NormalizeTransparency(transparency)/100d;
 public static float EffectWeight(int transparency)
 {
  double fraction=NormalizeTransparency(transparency)/100d;
  return (float)(1-Math.Pow(fraction,5));
 }

 public static MaterialPlan Choose(bool requested,bool advancedEffects,bool highContrast,bool supported)
 {
  if(highContrast)return new(false,"high_contrast");
  if(!requested)return new(false,"user_solid");
  if(!advancedEffects)return new(false,"system_transparency_off");
  return supported?new(true,"system_managed"):new(false,"unsupported");
 }
}
