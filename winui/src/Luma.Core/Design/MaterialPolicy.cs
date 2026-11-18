namespace Luma.Core.Design;

public enum BackgroundMaterial { Acrylic, Frosted, Liquid, Solid }

public sealed record MaterialPlan(bool UseAcrylic,string Reason);

public readonly record struct BackgroundLook(double Coat,double ControlTint,double PaneWeight,double Highlight,double Rim)
{
 public static BackgroundLook For(BackgroundMaterial material)=>material switch
 {
  BackgroundMaterial.Frosted=>new(.30,.43,1.55,.28,.46),
  BackgroundMaterial.Liquid=>new(0,.11,.68,1,.85),
  _=>new(0,.23,1,.55,.58)
 };
}

public static class MaterialPolicy
{
 public const int DefaultTransparency=85;
 public static int NormalizeTransparency(int value)=>Math.Clamp(value,0,100);
 public static double BackgroundWash(int transparency)=>1-NormalizeTransparency(transparency)/100d;

 public static MaterialPlan Choose(bool requested,bool advancedEffects,bool highContrast,bool supported)
 {
  if(highContrast)return new(false,"high_contrast");
  if(!requested)return new(false,"user_solid");
  if(!advancedEffects)return new(false,"system_transparency_off");
  return supported?new(true,"system_managed"):new(false,"unsupported");
 }
}
