namespace Luma.Core.Design;

public sealed record MaterialPlan(bool UseAcrylic,string Reason);

public static class MaterialPolicy
{
 public static MaterialPlan Choose(bool requested,bool advancedEffects,bool highContrast,bool supported)
 {
  if(highContrast)return new(false,"high_contrast");
  if(!requested)return new(false,"user_solid");
  if(!advancedEffects)return new(false,"system_transparency_off");
  return supported?new(true,"system_managed"):new(false,"unsupported");
 }
}
