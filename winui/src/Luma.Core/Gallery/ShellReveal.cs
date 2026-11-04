namespace Luma.Core.Gallery;
public static class ShellReveal
{
 public static string Execute(string path,Action<string> select,Action<string> openFolder)
 {
  try{select(path);return "select";}
  catch(Exception error) when(error is System.Runtime.InteropServices.ExternalException or EntryPointNotFoundException or DllNotFoundException)
  {
   openFolder(Path.GetDirectoryName(path)??throw new IOException("Missing parent folder"));return "folder";
  }
 }
}
