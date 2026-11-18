function Select-LumaBackgroundMaterial {
    param(
        [Windows.Automation.AutomationElement]$Window,
        [int]$ProcessId,
        [ValidateSet('Acrylic','Frosted','Liquid','Solid')][string]$Material
    )

    if ($ProcessId -le 0 -or $Window.Current.ProcessId -ne $ProcessId) {
        throw 'Material picker requires the test-owned Luma window'
    }

    $name = @{
        Acrylic = '亚克力'
        Frosted = '磨砂玻璃'
        Liquid = '液态玻璃'
        Solid = '实色'
    }[$Material]
    $descendants = [Windows.Automation.TreeScope]::Descendants
    $element = [Windows.Automation.AutomationElement]
    $owned = [Windows.Automation.PropertyCondition]::new($element::ProcessIdProperty, $ProcessId)
    $pickerId = [Windows.Automation.PropertyCondition]::new($element::AutomationIdProperty, 'BackgroundMaterial')
    $optionName = [Windows.Automation.PropertyCondition]::new($element::NameProperty, $name)
    $optionCondition = [Windows.Automation.AndCondition]::new($owned, $optionName)
    $patterns = @{ Expand = $null; Select = $null }

    Await {
        try {
            $picker = $Window.FindFirst($descendants, $pickerId)
            if ($null -eq $picker -or $picker.Current.ProcessId -ne $ProcessId -or -not $picker.Current.IsEnabled) {
                return $false
            }
            if ($picker.Current.IsOffscreen) {
                $picker.SetFocus()
                return $false
            }
            $pattern = $null
            if ($picker.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
                $patterns.Expand = $pattern
                return $true
            }
        } catch [Windows.Automation.ElementNotAvailableException] {}
        return $false
    } 'reachable background material picker'

    $patterns.Expand.Expand()
    try {
        Await {
            try {
                if ($patterns.Expand.Current.ExpandCollapseState -ne [Windows.Automation.ExpandCollapseState]::Expanded) {
                    return $false
                }
                # WinUI can expose the dropdown under another HWND in the same process.
                $windows = @($Window)
                $windows += $element::RootElement.FindAll([Windows.Automation.TreeScope]::Children, $owned)
                foreach ($ownWindow in $windows) {
                    foreach ($option in $ownWindow.FindAll($descendants, $optionCondition)) {
                        if ($option.Current.IsEnabled -and -not $option.Current.IsOffscreen) {
                            $pattern = $null
                            if ($option.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
                                $patterns.Select = $pattern
                                return $true
                            }
                        }
                    }
                }
            } catch [Windows.Automation.ElementNotAvailableException] {}
            return $false
        } ('own material option ' + $Material)
        $patterns.Select.Select()
    } finally {
        try {
            if ($patterns.Expand.Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Expanded) {
                $patterns.Expand.Collapse()
            }
        } catch [Windows.Automation.ElementNotAvailableException] {}
    }
}
