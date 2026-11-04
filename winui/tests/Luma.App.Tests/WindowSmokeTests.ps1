# Historical Stage1 runner for archived0.8.0.1; Stage2 uses ShellLayoutTests.ps1.
param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [ValidateSet(100,125,150)][int]$TextScale=100
)
$ErrorActionPreference='Stop'
$taskExe=[IO.Path]::GetFullPath($Executable)
if (-not (Test-Path -LiteralPath $taskExe -PathType Leaf)) { throw 'STAGE1_RED: WinUI executable does not exist; real-window contract cannot pass.' }
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class StageWindowNative {
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int Left,Top,Right,Bottom;}
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int command);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint message,IntPtr w,IntPtr l);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,System.Text.StringBuilder b,int max);
 [DllImport("user32.dll",EntryPoint="GetWindowLongPtrW")] public static extern IntPtr GetWindowLongPtr(IntPtr h,int index);
 [DllImport("dwmapi.dll")] public static extern int DwmIsCompositionEnabled(out bool enabled);
 [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h,int attribute,out int value,int size);
}
'@
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
$taskData=Join-Path $taskOutput 'isolated-data'
$existingData=Join-Path $taskOutput 'existing-output'
if ((Test-Path -LiteralPath $taskData) -or (Test-Path -LiteralPath $existingData)) { throw 'Use a fresh output directory; existing data and fixtures must not be reused.' }
$taskState=Join-Path $taskData 'window-state.json'
$checks=New-Object 'System.Collections.Generic.List[object]'
function Assert-Stage([bool]$Condition,[string]$Name) {
    if (-not $Condition) { throw ('STAGE1_FAIL: '+$Name) }
    $checks.Add(@{name=$Name;passed=$true})
    Write-Output ('PASS '+$Name)
}
function Wait-Stage([scriptblock]$Predicate,[string]$Name,[int]$Seconds=15) {
    $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
    do {if (& $Predicate) {return};Start-Sleep -Milliseconds 50} while ([DateTime]::UtcNow -lt $deadline)
    throw ('STAGE1_TIMEOUT: '+$Name)
}
function Read-State {
    if (Test-Path -LiteralPath $taskState) {try {return Get-Content -LiteralPath $taskState -Raw -Encoding UTF8 | ConvertFrom-Json} catch {return $null}}
    return $null
}
function New-StageProcess([string]$Arguments) {
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$taskExe;$start.Arguments=$Arguments;$start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $start.WorkingDirectory=Split-Path -Parent $taskExe
    $start.EnvironmentVariables['PATH']=$env:SystemRoot+'\System32;'+$env:SystemRoot
    $start.EnvironmentVariables['DOTNET_ROOT']=Join-Path $taskOutput 'absent-dotnet'
    $start.EnvironmentVariables['DOTNET_ROOT_X64']=Join-Path $taskOutput 'absent-dotnet'
    $start.EnvironmentVariables['DOTNET_MULTILEVEL_LOOKUP']='0'
    return [Diagnostics.Process]::Start($start)
}
$process=$null
try {
    $legacyData=Join-Path $env:LOCALAPPDATA 'Luma'
    $rejected=New-StageProcess ('--data-dir "'+$legacyData+'"')
    Assert-Stage ($rejected.WaitForExit(10000)) 'protected data path is rejected promptly'
    Assert-Stage ($rejected.ExitCode -eq 2) 'protected data path exits with validation error'
    [IO.Directory]::CreateDirectory($existingData) | Out-Null
    $sentinel=Join-Path $existingData 'capture-system-984x611-100.png'
    [IO.File]::WriteAllBytes($sentinel,[byte[]](1,3,5,7))
    $rejectedExisting=New-StageProcess ('--data-dir "'+$existingData+'" --diagnostics --capture')
    Assert-Stage ($rejectedExisting.WaitForExit(10000)) 'existing diagnostic directory is rejected promptly'
    Assert-Stage ($rejectedExisting.ExitCode -eq 2) 'existing output cannot be reused for diagnostic capture'
    Assert-Stage (([BitConverter]::ToString([IO.File]::ReadAllBytes($sentinel))) -eq '01-03-05-07') 'existing synthetic image bytes are preserved'
    $process=New-StageProcess ('--data-dir "'+$taskData+'" --diagnostics --capture --text-scale '+$TextScale)
    Wait-Stage { $s=Read-State;return ($null -ne $s -and $s.ready -and $s.captureRevision -eq $s.revision) } 'native ready and first private-surface capture'
    $state=Read-State;$hwnd=[IntPtr][long]$state.hwnd
    Assert-Stage ($state.processId -eq $process.Id) 'diagnostics belong to launched process'
    Assert-Stage ($state.applicationTextScale -eq $TextScale) 'requested application text scale is active'
    $className=New-Object Text.StringBuilder 256
    [StageWindowNative]::GetClassName($hwnd,$className,256) | Out-Null
    Assert-Stage ($className.ToString() -like '*WinUI*') 'actual WinUI desktop HWND class'
    $bundleRoot=(Split-Path -Parent $taskExe).TrimEnd('\')+'\'
    $windowsRoot=$env:SystemRoot.TrimEnd('\')+'\'
    $provenance=@($process.Modules | ForEach-Object {
        $origin=if ($_.FileName.StartsWith($bundleRoot,[StringComparison]::OrdinalIgnoreCase)) {'bundle'} elseif ($_.FileName.StartsWith($windowsRoot,[StringComparison]::OrdinalIgnoreCase)) {'Windows'} else {'external'}
        @{name=$_.ModuleName;origin=$origin;version=$_.FileVersionInfo.FileVersion}
    })
    $modules=@($provenance | ForEach-Object {$_.name})
    Assert-Stage ($modules -contains 'Microsoft.UI.Xaml.dll') 'real Microsoft UI XAML runtime loaded'
    Assert-Stage ($modules -contains 'coreclr.dll') 'real .NET runtime loaded'
    foreach ($runtime in @('coreclr.dll','Microsoft.UI.Xaml.dll')) {
        Assert-Stage ([bool]($provenance | Where-Object {$_.name -ieq $runtime -and $_.origin -eq 'bundle'})) ('runtime loaded from portable bundle: '+$runtime)
    }
    foreach ($runtime in @('vcruntime140.dll','msvcp140.dll')) {
        $crtFile=Join-Path $bundleRoot $runtime
        Assert-Stage ((Test-Path -LiteralPath $crtFile -PathType Leaf) -and (Get-Item -LiteralPath $crtFile).VersionInfo.FileVersion -eq '14.44.35211.0') ('pinned app-local CRT available: '+$runtime)
        Assert-Stage (-not ($provenance | Where-Object {$_.name -ieq $runtime -and $_.origin -eq 'external'})) ('no external developer CRT dependency: '+$runtime)
    }
    Assert-Stage (-not ($modules | Where-Object {$_ -match 'Qt[56]|PySide|python3|WebView2|chrome|webkit'})) 'no Qt Python or web rendering modules loaded'
    Assert-Stage ($state.engineStarted -eq $false -and $state.catalogOpened -eq $false) 'stage1 engine and catalog remain disconnected'
    $styles=[StageWindowNative]::GetWindowLongPtr($hwnd,-16).ToInt64()
    Assert-Stage (($styles -band 0x00C00000) -eq 0x00C00000 -and ($styles -band 0x00040000) -ne 0) 'system caption and resizable native frame retained'
    $composition=$false;$nativeRendering=0
    [StageWindowNative]::DwmIsCompositionEnabled([ref]$composition) | Out-Null
    $dwmResult=[StageWindowNative]::DwmGetWindowAttribute($hwnd,1,[ref]$nativeRendering,4)
    Assert-Stage ($composition -and $dwmResult -eq 0 -and $nativeRendering -ne 0) 'DWM nonclient rendering enabled; shadow pixels not sampled'
    $element=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
    function Invoke-StageButton([string]$Id) {
        $condition=New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::AutomationIdProperty,$Id)
        $control=$element.FindFirst([Windows.Automation.TreeScope]::Descendants,$condition)
        # Native restore completes before XAML layout/UIA geometry catches up.
        # Keep the same containment contract, waiting for actual settled layout.
        Wait-Stage {
            if ($null -eq $control) {return $false}
            $nativeRect=New-Object StageWindowNative+RECT
            [StageWindowNative]::GetWindowRect($hwnd,[ref]$nativeRect) | Out-Null
            $box=$control.Current.BoundingRectangle
            $s=Read-State
            return ($control.Current.IsEnabled -and -not $control.Current.IsOffscreen -and $box.Width -gt 0 -and $box.Height -gt 0 -and $box.Left -ge $nativeRect.Left -and $box.Top -ge $nativeRect.Top -and $box.Right -le $nativeRect.Right -and $box.Bottom -le $nativeRect.Bottom -and $s.captureRevision -eq $s.revision)
        } ('settled native control geometry: '+$Id)
        Assert-Stage ($null -ne $control -and $control.Current.IsEnabled -and -not $control.Current.IsOffscreen) ('direct native control reachable: '+$Id)
        Assert-Stage ($control.Current.IsKeyboardFocusable) ('native control can receive keyboard focus: '+$Id)
        $rect=New-Object StageWindowNative+RECT
        [StageWindowNative]::GetWindowRect($hwnd,[ref]$rect) | Out-Null
        $bounds=$control.Current.BoundingRectangle
        Assert-Stage ($bounds.Left -ge $rect.Left -and $bounds.Top -ge $rect.Top -and $bounds.Right -le $rect.Right -and $bounds.Bottom -le $rect.Bottom) ('native control bounds inside own window: '+$Id)
        $pattern=$control.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
        $pattern.Invoke()
    }
    foreach ($mode in @('Dark','Light','System')) {
        Invoke-StageButton ('Theme'+$mode)
        Wait-Stage {$s=Read-State;return ($s.theme -eq $mode -and $s.captureRevision -eq $s.revision)} ('theme '+$mode)
        Assert-Stage ((Read-State).theme -eq $mode) ('native theme action: '+$mode)
        if ($mode -ne 'System') { Assert-Stage ((Read-State).actualTheme -eq $mode) ('actual XAML theme: '+$mode) }
    }
    foreach ($size in @(@{id='ResizeCompact';w=1000;h=650},@{id='ResizeWide';w=1480;h=920})) {
        Invoke-StageButton $size.id
        Wait-Stage {$rect=New-Object StageWindowNative+RECT;[StageWindowNative]::GetWindowRect($hwnd,[ref]$rect) | Out-Null;return (($rect.Right-$rect.Left) -eq $size.w -and ($rect.Bottom-$rect.Top) -eq $size.h)} $size.id
        Assert-Stage $true ('native outer-pixel resize: '+$size.id)
        Wait-Stage {$s=Read-State;return ($s.windowWidth -eq $size.w -and $s.windowHeight -eq $size.h -and $s.captureRevision -eq $s.revision)} ('settled own-surface capture '+$size.id)
        Assert-Stage $true ('settled own-surface capture after resize: '+$size.id)
    }
    [StageWindowNative]::ShowWindow($hwnd,3) | Out-Null
    Wait-Stage {[StageWindowNative]::IsZoomed($hwnd)} 'system maximize'
    Assert-Stage $true 'system maximize works'
    [StageWindowNative]::ShowWindow($hwnd,9) | Out-Null
    Wait-Stage {-not [StageWindowNative]::IsZoomed($hwnd)} 'system restore'
    Assert-Stage $true 'system restore works'
    [StageWindowNative]::ShowWindow($hwnd,6) | Out-Null
    Wait-Stage {[StageWindowNative]::IsIconic($hwnd)} 'system minimize'
    Assert-Stage $true 'system minimize works'
    [StageWindowNative]::ShowWindow($hwnd,9) | Out-Null
    Wait-Stage {-not [StageWindowNative]::IsIconic($hwnd)} 'restore from minimize'
    Invoke-StageButton 'ClosePreview'
    Assert-Stage ($process.WaitForExit(15000)) 'normal native close completes'
    Assert-Stage ($process.ExitCode -eq 0) 'native process exits cleanly'
    $final=Read-State
    Assert-Stage ($final.closed) 'close is recorded in isolated diagnostic directory'
    Assert-Stage (-not (Get-ChildItem -LiteralPath $taskData -Recurse -File | Where-Object {$_.Extension -match '^\.(db|sqlite|sqlite3|safetensors|ckpt)$'})) 'no library or model files created in stage1'
    $report=@{result='passed';checks=$checks.ToArray();count=$checks.Count;processModules=$modules;moduleProvenance=$provenance;restrictedPath=$true;dotnetRootsUnavailable=$true;hwndClass=$className.ToString();scope='own WinUI HWND only; explicit isolated data; no engine/catalog/images';systemHighContrastToggled=$false;physicalDpiChanged=$false;shadowPixelsCaptured=$false;state=$final}
    $report | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $taskOutput 'native-window-report.json') -Encoding UTF8
    Write-Output ('STAGE1_PASSED '+$checks.Count+' native checks')
} finally {
    if ($process -and -not $process.HasExited) {
        [StageWindowNative]::PostMessage($process.MainWindowHandle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero) | Out-Null
        $process.WaitForExit(10000) | Out-Null
    }
}
