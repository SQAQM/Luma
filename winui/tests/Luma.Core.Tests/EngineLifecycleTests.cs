using System.Diagnostics;
using System.Reflection;
using Luma.Core.Engine;

internal static class EngineLifecycleTests
{
 public static async Task<int> Run()
 {
  string temporaryRoot=Path.GetFullPath(Path.GetTempPath());string directory=Path.GetFullPath(Path.Combine(temporaryRoot,"luma-stage7-closed-pipe-"+Guid.NewGuid().ToString("N")));if(Path.GetDirectoryName(directory)!=Path.TrimEndingDirectorySeparator(temporaryRoot))throw new Exception("synthetic temporary boundary");Directory.CreateDirectory(directory);
  File.WriteAllText(Path.Combine(directory,"fake-mode.txt"),"normal");
  var start=new ProcessStartInfo(Path.Combine(AppContext.BaseDirectory,"Luma.Core.Tests.exe")){UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true};start.ArgumentList.Add("--data-dir");start.ArgumentList.Add(directory);
  using var process=Process.Start(start)!;
  var owner=new EngineProcess();typeof(EngineProcess).GetMethod("TrackProcess",BindingFlags.NonPublic|BindingFlags.Instance)!.Invoke(owner,[process]);
  try
  {
   if(!owner.IsAlive||owner.ProcessId!=process.Id)throw new Exception("tracked child status unavailable");
   try{await owner.WaitForExitAsync(new CancellationToken(true));throw new Exception("cancelled wait completed for live child");}catch(OperationCanceledException){}
   if(!owner.IsAlive||owner.ProcessId!=process.Id)throw new Exception("cancelled wait falsely released live child");
   // Fault and shutdown may both observe an already closed underlying pipe.
   process.StandardInput.BaseStream.Dispose();
   await Task.WhenAll(Enumerable.Range(0,16).Select(_=>Task.Run(owner.CloseInput)));
   using var timeout=new CancellationTokenSource(TimeSpan.FromSeconds(10));await owner.WaitForExitAsync(timeout.Token);
   if(process.ExitCode!=0||owner.IsAlive)throw new Exception("synthetic pipe child did not exit cleanly");
   owner.CloseInput();return 7;
  }
  finally{if(!process.HasExited){process.StandardInput.Close();await process.WaitForExitAsync();}Directory.Delete(directory,true);}
 }
}
