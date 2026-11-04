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
  return count;
 }
}
