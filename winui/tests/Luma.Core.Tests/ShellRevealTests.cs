using System.Runtime.InteropServices;
using Luma.Core.Gallery;
internal static class ShellRevealTests
{
 public static int Run()
 {
  int passed=0;void Check(bool ok,string name){if(!ok)throw new Exception(name);passed++;}
  string path=@"E:\合成 [文件]\模型.safetensors";int selected=0,opened=0;
  Check(ShellReveal.Execute(path,p=>{Check(p==path,"selection path preserved");selected++;},_=>opened++)=="select","normal selection");
  Check(selected==1&&opened==0,"successful selection has no extra launch");
  string? folder=null;
  Check(ShellReveal.Execute(path,_=>throw new COMException("synthetic shell failure"),p=>folder=p)=="folder","fallback reported");
  Check(folder==Path.GetDirectoryName(path),"fallback opens only parent folder");
  try{ShellReveal.Execute(path,_=>throw new COMException(),_=>throw new IOException("synthetic unavailable folder"));throw new Exception("failure hidden");}catch(IOException){passed++;}
  return passed;
 }
}
