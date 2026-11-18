using System.Text.Json;
using Luma.Core.Design;
using Luma.Core.Management;

internal static class MaterialTests
{
 public static int Run()
 {
  int count=0;void Check(bool value,string name){if(!value)throw new Exception("material "+name);count++;}
  Check(MaterialPolicy.Choose(true,true,false,true).UseAcrylic,"supported and allowed");
  Check(!MaterialPolicy.Choose(false,true,false,true).UseAcrylic,"user solid preference");
  Check(!MaterialPolicy.Choose(true,false,false,true).UseAcrylic,"system transparency disabled");
  Check(!MaterialPolicy.Choose(true,true,true,true).UseAcrylic,"high contrast");
  Check(!MaterialPolicy.Choose(true,true,false,false).UseAcrylic,"unsupported compositor");
  Check(MaterialPolicy.Choose(true,false,false,true).Reason=="system_transparency_off","explicit system reason");
  Check(new WindowPreferences(ReduceMotion:true).UseMaterials,"reduced motion is independent of transparency");
  var old=JsonSerializer.Deserialize<WindowPreferences>("{\"Width\":1000,\"Height\":650,\"Theme\":\"Dark\",\"ReduceMotion\":true}")!;
  Check(old.UseMaterials&&old.Theme=="Dark"&&old.ReduceMotion,"old preference compatibility");
  var disabled=new WindowPreferences(UseMaterials:false);var roundTrip=JsonSerializer.Deserialize<WindowPreferences>(JsonSerializer.Serialize(disabled));
  Check(roundTrip is{UseMaterials:false},"solid preference round trip");
  Check(old.SelectedMaterial==BackgroundMaterial.Acrylic&&old.BackgroundTransparency==85,"existing glass profile keeps its default look");
  Check(roundTrip?.SelectedMaterial==BackgroundMaterial.Solid,"existing solid profile maps to solid");
  var customized=new WindowPreferences(Material:"Liquid",BackgroundTransparency:72);
  Check(JsonSerializer.Deserialize<WindowPreferences>(JsonSerializer.Serialize(customized)) is
   {SelectedMaterial:BackgroundMaterial.Liquid,BackgroundTransparency:72},"material and transparency round trip");
  Check(new WindowPreferences(UseMaterials:false,Material:"future").SelectedMaterial==BackgroundMaterial.Solid,"unknown material preserves last compatible preference");
  Check(MaterialPolicy.BackgroundWash(-4)==1&&MaterialPolicy.BackgroundWash(120)==0&&MaterialPolicy.BackgroundWash(50)==.5,"slider boundaries and direction");
  var acrylic=BackgroundLook.For(BackgroundMaterial.Acrylic);
  var frost=BackgroundLook.For(BackgroundMaterial.Frosted);
  var liquid=BackgroundLook.For(BackgroundMaterial.Liquid);
  Check(frost.ControlTint>acrylic.ControlTint&&acrylic.ControlTint>liquid.ControlTint&&frost.Coat>liquid.Coat,"three distinct acrylic treatments");
  Check(liquid.Highlight>acrylic.Highlight&&acrylic.Highlight>frost.Highlight,"liquid edge and frosted surface hierarchy");
  return count+GlassPressMotionTests.Run();
 }
}
