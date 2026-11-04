param([Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$SyntheticOverlay)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if($SyntheticOverlay){
 # Only a fresh parent-created test bridge is accepted, never an arbitrary HWND.
 $bridge=[IO.Path]::GetFullPath($OutputDirectory)
 if(-not (Test-Path -LiteralPath (Join-Path $bridge 'owned-guard-parent.txt'))){throw 'Own guard bridge required'}
 Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class ParentForeground{[DllImport("user32.dll")]public static extern bool AllowSetForegroundWindow(uint process);}'
 [ParentForeground]::AllowSetForegroundWindow([uint32]([IO.File]::ReadAllText((Join-Path $bridge 'owned-guard-parent.txt'))))|Out-Null
 $overlay=[Windows.Forms.Form]::new();$overlay.FormBorderStyle='None';$overlay.ShowInTaskbar=$false;$overlay.TopMost=$true;$overlay.StartPosition='Manual';$overlay.Bounds=[Drawing.Rectangle]::new(-64,-64,1400,1000);$overlay.BackColor=[Drawing.Color]::Magenta
 $overlay.Add_Shown({[IO.File]::WriteAllText((Join-Path $bridge 'child-hwnd.txt'),$overlay.Handle.ToInt64().ToString())})
 [Windows.Forms.Application]::Run($overlay);$overlay.Dispose();exit
}
$output=[IO.Path]::GetFullPath($OutputDirectory);if(Test-Path -LiteralPath $output){throw 'Fresh guard output required'}
[IO.Directory]::CreateDirectory($output)|Out-Null
[IO.File]::WriteAllText((Join-Path $output 'owned-guard-parent.txt'),$PID.ToString())
$refs=@(Get-ChildItem -LiteralPath (Join-Path $PSHOME 'ref') -File -Filter '*.dll'|ForEach-Object FullName)
$forms=[Windows.Forms.Form].Assembly.Location;$refs+=@($forms,[Drawing.Bitmap].Assembly.Location,(Join-Path (Split-Path -Parent $forms) 'System.Windows.Forms.Primitives.dll'),(Join-Path (Split-Path -Parent $forms) 'System.Private.Windows.GdiPlus.dll'),(Join-Path (Split-Path -Parent $forms) 'System.Private.Windows.Core.dll'))
Add-Type -Path (Join-Path $PSScriptRoot 'MaterialProbeSupport.cs') -ReferencedAssemblies $refs
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class GuardClose{[DllImport("user32.dll")]public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);}'
$target=$null;$hostWindow=$null;$child=$null;$childHwnd=[IntPtr]::Zero;$checks=0
function Check([bool]$ok,[string]$name){if(-not $ok){throw ('CAPTURE_GUARD_FAIL '+$name)};$script:checks++}
function Pump{[Windows.Forms.Application]::DoEvents();Start-Sleep -Milliseconds 60}
function GuardResult{
 [LumaOwnedCapture].GetMethod('FocusOwned',[Reflection.BindingFlags]'NonPublic,Static').Invoke($null,@($target.Handle,[uint32]$PID))|Out-Null
 try{[LumaOwnedCapture].GetMethod('Verify',[Reflection.BindingFlags]'NonPublic,Static').Invoke($null,@($target.Handle,[uint32]$PID,$hostWindow.Handle))|Out-Null;return 'allowed'}
 catch{if($_.Exception.ToString() -match 'foreign overlay|foreign backdrop'){return 'rejected_foreign'};throw}
}
try{
 $hostWindow=[LumaPatternHost]::new();$hostWindow.Show();Pump
 $start=[Diagnostics.ProcessStartInfo]::new((Get-Process -Id $PID).Path);$start.UseShellExecute=$false;$start.WindowStyle='Hidden'
 foreach($arg in @('-NoProfile','-STA','-File',$PSCommandPath,'-OutputDirectory',$output,'-SyntheticOverlay')){$start.ArgumentList.Add($arg)}
 $child=[Diagnostics.Process]::Start($start);$until=[DateTime]::UtcNow.AddSeconds(20)
 while(-not (Test-Path -LiteralPath (Join-Path $output 'child-hwnd.txt'))){if($child.HasExited -or [DateTime]::UtcNow -ge $until){throw 'Own synthetic overlay startup failed'};Pump}
 $childHwnd=[IntPtr][long]([IO.File]::ReadAllText((Join-Path $output 'child-hwnd.txt')))
 $target=[Windows.Forms.Form]::new();$target.Text='Luma own guard target';$target.StartPosition='Manual';$target.Bounds=[Drawing.Rectangle]::new(0,0,1000,650);$target.TopMost=$true;$target.Show();[LumaOwnedCapture]::Place($target.Handle);Pump
 # Target > self-created second process > pattern host. No pixel capture is made.
 [LumaOwnedCapture]::SetWindowPos($childHwnd,$target.Handle,-64,-64,1400,1000,0x10)|Out-Null;[LumaOwnedCapture]::SetForegroundWindow($target.Handle)|Out-Null;Pump
 Check ((GuardResult) -eq 'rejected_foreign') 'reject a foreign PID between target and synthetic host'
 # The second window is outside the screenshot but still inside the blur margin.
 [LumaOwnedCapture]::SetWindowPos($childHwnd,$target.Handle,1008,140,20,180,0x10)|Out-Null;Pump
 Check ((GuardResult) -eq 'rejected_foreign') 'reject a foreign PID in blur sampling margin'
 [GuardClose]::PostMessage($childHwnd,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null
 Check ($child.WaitForExit(15000)) 'own child closed'
 [LumaOwnedCapture]::Place($target.Handle);Pump
 Check ((GuardResult) -eq 'allowed') 'unobstructed own host accepted'
 @{result='passed';count=$checks;pixelCaptureUsed=$false;externalContentRead=$false;secondProcessSyntheticOnly=$true}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $output 'capture-guard-report.json')
 Write-Output ('MATERIAL_CAPTURE_GUARD_PASSED '+$checks)
}finally{
 if($child -and -not $child.HasExited){if($childHwnd -ne [IntPtr]::Zero){[GuardClose]::PostMessage($childHwnd,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null};if(-not $child.WaitForExit(15000)){$child.Kill();$child.WaitForExit()}}
 if($target){$target.Close();$target.Dispose()};if($hostWindow){$hostWindow.Close();$hostWindow.Dispose()}
}
