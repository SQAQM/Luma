param(
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
. (Join-Path $PSScriptRoot 'MaterialAutomation.ps1')

$refs = @(Get-ChildItem -LiteralPath (Join-Path $PSHOME 'ref') -File -Filter '*.dll' | ForEach-Object FullName)
$forms = [Windows.Forms.Form].Assembly.Location
$desktop = Split-Path -Parent $forms
$refs += @($forms, [Drawing.Bitmap].Assembly.Location, (Join-Path $desktop 'System.Windows.Forms.Primitives.dll'),
    (Join-Path $desktop 'System.Private.Windows.GdiPlus.dll'), (Join-Path $desktop 'System.Private.Windows.Core.dll'))
Add-Type -LiteralPath (Join-Path $PSScriptRoot 'MaterialProbeSupport.cs') -ReferencedAssemblies $refs

$output = [IO.Path]::GetFullPath($OutputDirectory)
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$relative = [IO.Path]::GetRelativePath($repo, $output)
if ($relative -eq '.' -or $relative -eq '..' -or $relative -like '..\*' -or [IO.Path]::IsPathRooted($relative)) {
    throw 'Probe output must be inside the Luma repository'
}
if (Test-Path -LiteralPath $output) { throw 'Fresh transparency probe output required' }
[IO.Directory]::CreateDirectory($output) | Out-Null
$data = Join-Path $output 'isolated-data'
$statePath = Join-Path $data 'window-state.json'
$process = $null
$hostWindow = $null
$root = $null
$sample = [Drawing.Rectangle]::new(576, 470, 384, 30)
$levels = @(0, 70, 100)
$materials = @('Acrylic', 'Frosted', 'Liquid')
$pixels = @{}
$results = [ordered]@{}
$failures = [Collections.Generic.List[string]]::new()

function State {
    try {
        $sharing = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        $reader = [IO.StreamReader]::new([IO.File]::Open($statePath, 'Open', 'Read', $sharing))
        try { return $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
    } catch [IO.IOException] { return $null }
}

function Await([scriptblock]$test, [string]$name) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        [LumaOwnedCapture]::Pump()
        if (& $test) { return }
        if ($process.HasExited) { throw "Owned Luma exited while awaiting $name" }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timeout awaiting $name"
}

function Find-Owned([string]$id) {
    $element = [Windows.Automation.AutomationElement]
    $owned = [Windows.Automation.PropertyCondition]::new($element::ProcessIdProperty, $process.Id)
    $hasId = [Windows.Automation.PropertyCondition]::new($element::AutomationIdProperty, $id)
    return $root.FindFirst([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.AndCondition]::new($owned, $hasId))
}

function Click-Owned([string]$id) {
    $patterns = @{}
    Await {
        $control = Find-Owned $id
        $invoke = $null
        if ($control -and $control.Current.IsEnabled -and -not $control.Current.IsOffscreen -and
            $control.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) {
            $patterns.Invoke = $invoke
            return $true
        }
        return $false
    } $id
    $patterns.Invoke.Invoke()
}

function Set-Transparency([int]$value) {
    Await {
        try {
            $slider = Find-Owned 'BackgroundTransparency'
            $range = $null
            if ($slider -and $slider.Current.IsEnabled -and
                $slider.TryGetCurrentPattern([Windows.Automation.RangeValuePattern]::Pattern, [ref]$range)) {
                if ($range.Current.Value -ne $value) { $range.SetValue($value) }
                return $true
            }
        } catch [Windows.Automation.ElementNotAvailableException] {}
        return $false
    } "set transparency to $value"
    Await {(State).material.backgroundTransparency -eq $value} "transparency $value applied"
}

function Measure-Backdrop([string]$name) {
    foreach ($inverted in @($false, $true)) {
        $state = State
        if ($state.processId -ne $process.Id -or $state.page -ne 'Settings' -or $state.catalogOpened -or $state.engine.galleryOpened) {
            throw 'Screenshot sampling requires the synthetic Settings page'
        }
        $hostWindow.SetStripes($inverted)
        for ($i = 0; $i -lt 10; $i++) {
            [LumaOwnedCapture]::Pump()
            Start-Sleep -Milliseconds 40
        }
        $pixels["$name-$inverted"] = [LumaOwnedCapture]::CaptureStripes($hwnd, $process.Id, $hostWindow, $sample)
        if ($name -eq 'Acrylic-0') {
            $pixels["Reference-$inverted"] = [LumaOwnedCapture]::StripeReference($hwnd, $hostWindow, $sample)
        }
    }
    $normal = $pixels["$name-False"]
    $inverted = $pixels["$name-True"]
    return [ordered]@{
        responseMean = [LumaOwnedCapture]::Difference($normal, $inverted)
        lowFrequencyAmplitude = [LumaOwnedCapture]::StripeAmplitude($normal, $inverted, 192)
        highFrequencyAmplitude = [LumaOwnedCapture]::StripeAmplitude($normal, $inverted, 16)
        referenceRgbError = [Math]::Max(
            [LumaOwnedCapture]::Difference($normal, $pixels['Reference-False']),
            [LumaOwnedCapture]::Difference($inverted, $pixels['Reference-True']))
        materialState = $state.material
    }
}

function Check([bool]$condition, [string]$name) {
    if (-not $condition) { $failures.Add($name) }
}

try {
    $hostWindow = [LumaPatternHost]::new()
    $hostWindow.SetStripes($false)
    $hostWindow.Show()
    [LumaOwnedCapture]::Pump()
    $start = [Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable))
    $start.UseShellExecute = $false
    $start.WindowStyle = 'Hidden'
    foreach ($argument in @('--data-dir', $data, '--diagnostics', '--text-scale', '100')) {
        $start.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::Start($start)
    Await {$null -ne (State) -and (State).ready} 'own Luma ready'
    $state = State
    if ($state.processId -ne $process.Id -or -not $state.hwnd) { throw 'Owned diagnostics window required' }
    $hwnd = [IntPtr][long]$state.hwnd
    $root = [Windows.Automation.AutomationElement]::FromHandle($hwnd)
    if ($root.Current.ProcessId -ne $process.Id) { throw 'Owned automation window required' }
    [LumaOwnedCapture]::Place($hwnd)
    Click-Owned 'NavSettings'
    Await {(State).page -eq 'Settings'} 'Settings'
    Click-Owned 'ThemeLight'
    Await {(State).actualTheme -eq 'Light'} 'light theme'

    foreach ($material in $materials) {
        Select-LumaBackgroundMaterial $root $process.Id $material
        Await {(State).material.material -eq $material} $material
        foreach ($level in $levels) {
            Set-Transparency $level
            $name = "$material-$level"
            $results[$name] = Measure-Backdrop $name
        }
    }
    Select-LumaBackgroundMaterial $root $process.Id 'Solid'
    Await {(State).material.material -eq 'Solid'} 'Solid'
    $results['Solid'] = Measure-Backdrop 'Solid'

    $referenceLow = [LumaOwnedCapture]::StripeAmplitude($pixels['Reference-False'], $pixels['Reference-True'], 192)
    $referenceHigh = [LumaOwnedCapture]::StripeAmplitude($pixels['Reference-False'], $pixels['Reference-True'], 16)
    Check ($referenceLow -gt 50 -and $referenceHigh -gt 36) 'Generated reference contains both stripe frequencies'
    Check ($results['Solid'].responseMean -le .5) 'Solid preset is independent of the background'
    foreach ($material in $materials) {
        $zero = $results["$material-0"]
        $clear = $results["$material-100"]
        $zero.solidRgbError = [LumaOwnedCapture]::Difference($pixels["$material-0-False"], $pixels['Solid-False'])
        Check ($zero.responseMean -le .5 -and $zero.solidRgbError -le 1) "$material 0% matches Solid and ignores stripe changes"
        Check ($clear.referenceRgbError -le 2 -and $clear.lowFrequencyAmplitude -ge .98*$referenceLow -and
            $clear.highFrequencyAmplitude -ge .98*$referenceHigh) "$material 100% matches both unblurred stripe patterns"
    }

    $acrylic = $results['Acrylic-70']
    $frosted = $results['Frosted-70']
    $liquid = $results['Liquid-70']
    # Compare fine detail to the broad response of the same material. Tint opacity changes both amplitudes.
    $acrylicDetail = ($acrylic.highFrequencyAmplitude / $referenceHigh) / [Math]::Max(.001, ($acrylic.lowFrequencyAmplitude / $referenceLow))
    $frostedDetail = ($frosted.highFrequencyAmplitude / $referenceHigh) / [Math]::Max(.001, ($frosted.lowFrequencyAmplitude / $referenceLow))
    Check ($acrylic.lowFrequencyAmplitude -ge .05*$referenceLow -and $frosted.lowFrequencyAmplitude -ge .05*$referenceLow -and
        $acrylicDetail - $frostedDetail -ge .15) '70% Frosted suppresses fine stripes relative to broad stripes more than Acrylic'
    Check ($liquid.highFrequencyAmplitude - $acrylic.highFrequencyAmplitude -ge .05*$referenceHigh -and
        $liquid.highFrequencyAmplitude - $frosted.highFrequencyAmplitude -ge .05*$referenceHigh) '70% Liquid retains more fine stripe detail than Acrylic and Frosted'

    $report = [ordered]@{
        result = $(if ($failures.Count) {'appearance_not_verified'} else {'passed'})
        scope = 'Light Settings on a fresh synthetic profile, guarded Luma sampling rectangle, generated stripes'
        sample = @{ x = $sample.X; y = $sample.Y; width = $sample.Width; height = $sample.Height }
        reference = @{ lowFrequencyAmplitude = $referenceLow; highFrequencyAmplitude = $referenceHigh }
        samples = $results
        failures = @($failures)
    }
    $json = $report | ConvertTo-Json -Depth 8
    $json | Set-Content -LiteralPath (Join-Path $output 'window-transparency-report.json')
    Write-Output $json
    if ($failures.Count) { throw ('Window transparency pixel checks failed: ' + ($failures -join '; ')) }
} finally {
    if ($process -and -not $process.HasExited) {
        $process.CloseMainWindow() | Out-Null
        if (-not $process.WaitForExit(30000)) { throw 'Owned Luma process did not close' }
    }
    if ($hostWindow) { $hostWindow.Dispose() }
    foreach ($bitmap in $pixels.Values) { $bitmap.Dispose() }
}
