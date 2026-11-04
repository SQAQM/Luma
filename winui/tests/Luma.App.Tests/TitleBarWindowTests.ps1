param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class ChromeNative {
 [StructLayout(LayoutKind.Sequential)]public struct Point{public int X,Y;public Point(int x,int y){X=x;Y=y;}}
 [DllImport("user32.dll")]static extern IntPtr WindowFromPoint(Point p);
 [DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll")]static extern int GetSystemMetrics(int i);
 [DllImport("user32.dll")]public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")]static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int w,int height,uint flags);
 [DllImport("user32.dll")]public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr i);
 [DllImport("user32.dll",EntryPoint="GetWindowLongPtrW")]public static extern IntPtr GetWindowLongPtr(IntPtr h,int i);
 public static bool OwnPoint(int pid,int x,int y){uint p;GetWindowThreadProcessId(WindowFromPoint(new Point(x,y)),out p);return p==pid;}
 public static bool OwnForeground(int pid){uint p;GetWindowThreadProcessId(GetForegroundWindow(),out p);return p==pid;}
 public static void Move(int x,int y){mouse_event(0x8001,(uint)Math.Round(x*65535.0/(GetSystemMetrics(0)-1)),(uint)Math.Round(y*65535.0/(GetSystemMetrics(1)-1)),0,UIntPtr.Zero);}
}
'@
$output=[IO.Path]::GetFullPath($OutputDirectory);if(Test-Path -LiteralPath $output){throw 'Fresh output required'};[IO.Directory]::CreateDirectory($output)|Out-Null
$data=Join-Path $output 'isolated-data';$state=Join-Path $data 'window-state.json';$process=$null;$checks=0
function State{try{$r=[IO.StreamReader]::new([IO.File]::Open($state,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));try{return $r.ReadToEnd()|ConvertFrom-Json}finally{$r.Dispose()}}catch [IO.IOException]{return $null}}
function Check([bool]$ok,[string]$name){if(-not $ok){throw ('CHROME_FAIL '+$name)};$script:checks++}
function Await([scriptblock]$test,[string]$name){$until=[DateTime]::UtcNow.AddSeconds(40);do{if(&$test){return};if($process.HasExited){throw ('CHROME_EXIT '+$name)};Start-Sleep -Milliseconds 40}while([DateTime]::UtcNow -lt $until);throw ('CHROME_TIMEOUT '+$name)}
try{
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --diagnostics --capture'
 $process=[Diagnostics.Process]::Start($start);Await {(State).ready -and (State).captureRevision -eq (State).revision} 'ready'
 $hwnd=[IntPtr][long](State).hwnd;$root=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
 function Find([string]$id){return $root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
 function Click([string]$id){$e=Find $id;Check ($e -and $e.Current.IsEnabled -and -not $e.Current.IsOffscreen) ('reachable '+$id);$e.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()}
 Check ($null -ne (State).titleBar) 'explicit title region diagnostics'
 Check ((State).titleBar.extended -and (State).titleBar.supported) 'native title extension supported'
 Check ((State).titleBar.height -ge 28 -and (State).titleBar.height -le 48) 'compact title height'
 Check ($null -ne (Find 'LumaTitleBar')) 'real title control'
 Check ((([ChromeNative]::GetWindowLongPtr($hwnd,-16).ToInt64()) -band 0xC0000) -eq 0xC0000) 'system menu and resize frame preserved'
 Click 'NavSettings';Click 'ThemeDark';Await {(State).actualTheme -eq 'Dark'} 'dark';Click 'ThemeLight';Await {(State).actualTheme -eq 'Light'} 'light'
 Click 'MaterialToggle';Await {-not (State).material.requested} 'solid';Check ((State).material.nativeLayers -eq 0) 'title and panes fall back together';Click 'MaterialToggle';Await {(State).material.requested} 'material';if((State).material.acrylicRequested){Check ((State).material.nativeLayers -eq 1) 'one continuous native window backdrop'}
 [ChromeNative]::SetWindowPos($hwnd,[IntPtr](-1),0,0,1000,650,0x10)|Out-Null;Await {(State).windowWidth -eq 1000 -and (State).windowHeight -eq 650} 'positioned'
 $rect=(Find 'LumaTitleBar').Current.BoundingRectangle;$x=[int]($rect.Left+$rect.Width*.5);$y=[int]($rect.Top+$rect.Height*.5)
 Check ([ChromeNative]::OwnPoint($process.Id,$x,$y)) 'drag starts in own title';[ChromeNative]::SetForegroundWindow($hwnd)|Out-Null;[ChromeNative]::Move($x,$y);Start-Sleep -Milliseconds 60
 Check ([ChromeNative]::OwnForeground($process.Id)) 'own foreground';[ChromeNative]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
 try{Start-Sleep -Milliseconds 60;[ChromeNative]::Move($x+60,$y+40);Start-Sleep -Milliseconds 100}finally{[ChromeNative]::mouse_event(4,0,0,0,[UIntPtr]::Zero)}
 Await {$b=$root.Current.BoundingRectangle;return $b.Left -ge 50 -and $b.Top -ge 30} 'real title drag';Check $true 'title drag moves native window'
 $window=$root.GetCurrentPattern([Windows.Automation.WindowPattern]::Pattern)
 $rect=(Find 'LumaTitleBar').Current.BoundingRectangle;$x=[int]($rect.Left+$rect.Width*.5);$y=[int]($rect.Top+$rect.Height*.5)
 Check ([ChromeNative]::OwnPoint($process.Id,$x,$y) -and [ChromeNative]::OwnForeground($process.Id)) 'double click stays in own title'
 [ChromeNative]::Move($x,$y);foreach($i in 1..2){[ChromeNative]::mouse_event(2,0,0,0,[UIntPtr]::Zero);[ChromeNative]::mouse_event(4,0,0,0,[UIntPtr]::Zero);Start-Sleep -Milliseconds 60}
 Await {$window.Current.WindowVisualState -eq [Windows.Automation.WindowVisualState]::Maximized} 'real title double click maximizes';Check $true 'title double click uses native maximize'
 $window.SetWindowVisualState([Windows.Automation.WindowVisualState]::Normal);Await {$window.Current.WindowVisualState -eq [Windows.Automation.WindowVisualState]::Normal} 'restore'
 $window.SetWindowVisualState([Windows.Automation.WindowVisualState]::Minimized);Await {$window.Current.WindowVisualState -eq [Windows.Automation.WindowVisualState]::Minimized} 'minimize';Check $true 'native minimize'
 $window.SetWindowVisualState([Windows.Automation.WindowVisualState]::Normal);Await {$window.Current.WindowVisualState -eq [Windows.Automation.WindowVisualState]::Normal} 'restore minimized';Check $true 'native restore minimized'
 Click 'ClosePreview';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'safe native close'
 @{result='passed';count=$checks;privateDataAccess=$false;scope='own HWND; synthetic empty library; app/native window actions; OS DPI96'}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $output 'titlebar-window-report.json')
 Write-Output ('TITLEBAR_WINDOW_PASSED '+$checks)
}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null}}
