param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class ProbeEntry{[DllImport("user32.dll")]public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);}'
$refs=@(Get-ChildItem -LiteralPath (Join-Path $PSHOME 'ref') -File -Filter '*.dll' | ForEach-Object FullName)
$forms=[System.Windows.Forms.Form].Assembly.Location;$drawing=[System.Drawing.Bitmap].Assembly.Location
$refs+=@($forms,$drawing,(Join-Path (Split-Path -Parent $forms) 'System.Windows.Forms.Primitives.dll'),(Join-Path (Split-Path -Parent $forms) 'System.Private.Windows.GdiPlus.dll'),(Join-Path (Split-Path -Parent $forms) 'System.Private.Windows.Core.dll'))
Add-Type -Path (Join-Path $PSScriptRoot 'MaterialProbeSupport.cs') -ReferencedAssemblies $refs
$out=[IO.Path]::GetFullPath($OutputDirectory);if(Test-Path -LiteralPath $out){throw 'Fresh probe output required'};[IO.Directory]::CreateDirectory($out)|Out-Null
$data=Join-Path $out 'isolated-data';$statePath=Join-Path $data 'window-state.json';$process=$null;$hostWindow=$null
function State{try{$reader=[IO.StreamReader]::new([IO.File]::Open($statePath,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));try{return $reader.ReadToEnd()|ConvertFrom-Json}finally{$reader.Dispose()}}catch [IO.IOException]{return $null}}
function Await([scriptblock]$test,[string]$name){$until=[DateTime]::UtcNow.AddSeconds(30);do{[LumaOwnedCapture]::Pump();if(&$test){return};if($process -and $process.HasExited){throw ('EXIT '+$name)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $until);throw ('TIMEOUT '+$name)}
function OpenGeneratedPhoto{$item=$root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,'GalleryImage1'));$item.SetFocus();[ProbeEntry]::PostMessage($hwnd,0x100,[IntPtr]13,[IntPtr]0)|Out-Null;[ProbeEntry]::PostMessage($hwnd,0x101,[IntPtr]13,[IntPtr]0)|Out-Null}
function Settle{for($i=0;$i -lt 12;$i++){[LumaOwnedCapture]::Pump();Start-Sleep -Milliseconds 50}}
try{
 $hostWindow=[LumaPatternHost]::new();$hostWindow.Show();$hostWindow.Update();[LumaOwnedCapture]::Pump()
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --diagnostics';$process=[Diagnostics.Process]::Start($start);Await {(State).ready} 'own ready';$hwnd=[IntPtr][long](State).hwnd
 [LumaOwnedCapture]::Place($hwnd);Settle;$root=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
 function Click([string]$id){$returnPage=$null;if($id -in @('ThemeLight','ThemeDark','ThemeSystem','MaterialToggle')){$returnPage=(State).page;if($returnPage -ne 'Settings'){Click 'NavSettings'}};Await {$e=$root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id));$p=$null;if($e -and $e.Current.IsEnabled -and $e.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$p)){$script:invoke=$p;return $true};return $false} $id;$script:invoke.Invoke();Settle;if($returnPage -and $returnPage -ne 'Settings'){Click ('Nav'+$returnPage)}}
 Click 'GalleryOpen';Await {$g=(State).workspace.gallery;return $g.total -eq 131 -and -not $g.loading -and $g.thumbnailActive -eq 0 -and $g.thumbnailQueue -eq 0} 'generated gallery';[System.Windows.Forms.Cursor]::Position=[System.Drawing.Point]::new(900,500);Settle;Click 'ThemeLight';$initial=(State).material
 $hostWindow.SetWarm($true);Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'acrylic-warm.png'))
 $hostWindow.SetWarm($false);Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'acrylic-cool.png'))
 Click 'MaterialToggle';Await {-not (State).material.requested} 'solid';$hostWindow.SetWarm($true);Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'solid-warm.png'))
 $hostWindow.SetWarm($false);Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'solid-cool.png'))
 $glass=[LumaOwnedCapture]::Difference((Join-Path $out 'acrylic-warm.png'),(Join-Path $out 'acrylic-cool.png'),16,140,12,220)
 $solid=[LumaOwnedCapture]::Difference((Join-Path $out 'solid-warm.png'),(Join-Path $out 'solid-cool.png'),16,140,12,220)
 $sheet=[LumaOwnedCapture]::Difference((Join-Path $out 'acrylic-warm.png'),(Join-Path $out 'acrylic-cool.png'),450,585,240,10)
 Click 'MaterialToggle';Click 'ThemeDark';$hostWindow.SetWarm($true);Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'dark-warm.png'));$hostWindow.SetWarm($false);Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'dark-cool.png'))
 $dark=[LumaOwnedCapture]::Difference((Join-Path $out 'dark-warm.png'),(Join-Path $out 'dark-cool.png'),16,140,12,220)
 $titleGlass=[LumaOwnedCapture]::Difference((Join-Path $out 'acrylic-warm.png'),(Join-Path $out 'acrylic-cool.png'),300,18,100,8)
 $titleSolid=[LumaOwnedCapture]::Difference((Join-Path $out 'solid-warm.png'),(Join-Path $out 'solid-cool.png'),300,18,100,8)
 $titleDark=[LumaOwnedCapture]::Difference((Join-Path $out 'dark-warm.png'),(Join-Path $out 'dark-cool.png'),300,18,100,8)
 $topEdge=[LumaOwnedCapture]::TopEdgeDifference((Join-Path $out 'dark-cool.png'))
 $darkMean=[LumaOwnedCapture]::Mean((Join-Path $out 'dark-cool.png'),16,140,12,220)
 $item=$root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,'GalleryImage1'));$item.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select();OpenGeneratedPhoto;Await {(State).workspace.viewer.displayedId -eq 1} 'generated viewer';Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'viewer-dark.png'));Click 'ViewerInfo';Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'viewer-info-dark.png'));Click 'ViewerBack';Click 'ThemeLight';OpenGeneratedPhoto;Await {(State).workspace.viewer.displayedId -eq 1} 'generated light viewer';Settle;[LumaOwnedCapture]::Capture($hwnd,$process.Id,$hostWindow.Handle,(Join-Path $out 'viewer-light.png'));Click 'ViewerBack'
 $report=@{topEdgeDelta=$topEdge;titleGlassMean=$titleGlass;titleSolidMean=$titleSolid;titleDarkMean=$titleDark;darkRailMean=$darkMean;darkDesktopResponseMean=$dark;result=$(if($topEdge -le 3 -and $titleGlass -ge 2 -and $titleDark -ge 2 -and $titleSolid -le .5 -and $glass -ge 2 -and $dark -ge 2 -and $darkMean -lt 120 -and $solid -le 0.5 -and $sheet -ge 2){'passed'}else{'appearance_not_verified'});nativeDesktopResponseMean=$glass;solidResponseMean=$solid;contentResponseMean=$sheet;initial=$initial;scope='only own HWND rectangle above a self-owned generated backdrop; foreground/z-order/kernel coverage checked before and after; fixed 1000x650 light/dark active window only';privateDataAccess=$false;networkUsed=$false}
 $report|ConvertTo-Json -Depth 8|Set-Content -LiteralPath (Join-Path $out 'material-compositor-report.json');$report|ConvertTo-Json -Depth 4
 Click 'ClosePreview';if(-not $process.WaitForExit(30000)){throw 'Own app did not safely close'}
}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null};if($hostWindow){$hostWindow.Close();$hostWindow.Dispose()}}
