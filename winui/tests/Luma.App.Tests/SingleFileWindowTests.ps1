param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$workspace=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(-not $output.StartsWith((Join-Path $workspace 'build')+'\',[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $output)){throw 'Fresh owned build directory required'}
[IO.Directory]::CreateDirectory($output)|Out-Null
$source=Join-Path $output 'synthetic.png';$data=Join-Path $output 'isolated-data';$statePath=Join-Path $data 'window-state.json';$process=$null;$checks=0
& (Join-Path $workspace '.venv-build/Scripts/python.exe') -c 'from PIL import Image;from PIL.PngImagePlugin import PngInfo;import sys;m=PngInfo();m.add_text("parameters","synthetic geometry\nNegative prompt: synthetic blur\nSteps: 20, Model: synthetic-model");Image.new("RGB",(640,360),(30,90,170)).save(sys.argv[1],pnginfo=m)' $source
if($LASTEXITCODE -ne 0){throw 'Synthetic fixture failed'}
[IO.File]::WriteAllText([IO.Path]::ChangeExtension($source,'.txt'),'must not read adjacent sidecar')
$before=(Get-FileHash -LiteralPath $source).Hash
function State{try{$r=[IO.StreamReader]::new([IO.File]::Open($statePath,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)));try{return $r.ReadToEnd()|ConvertFrom-Json}finally{$r.Dispose()}}catch [IO.IOException]{return $null}}
function Viewer{return (State).workspace.viewer}
function Check([bool]$ok,[string]$name){if(-not $ok){throw ('SINGLE_UI_FAIL '+$name)};$script:checks++}
function Await([scriptblock]$test,[string]$name){$until=[DateTime]::UtcNow.AddSeconds(35);do{if(&$test){return};if($process.HasExited){throw ('EXIT '+$name)};Start-Sleep -Milliseconds 50}while([DateTime]::UtcNow -lt $until);throw ('TIMEOUT '+$name)}
function Find([string]$id){return $script:root.FindFirst([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty,$id))}
function Click([string]$id){Await {$e=Find $id;return $e -and $e.Current.IsEnabled -and -not $e.Current.IsOffscreen} $id;(Find $id).GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$script:checks++}
try{
 $start=[Diagnostics.ProcessStartInfo]::new([IO.Path]::GetFullPath($Executable));$start.UseShellExecute=$false;$start.WindowStyle='Hidden';$start.Arguments='--data-dir "'+$data+'" --diagnostics --capture "'+$source+'"'
 $process=[Diagnostics.Process]::Start($start);Await {(State).ready} 'ready';$script:root=[Windows.Automation.AutomationElement]::FromHandle([IntPtr][long](State).hwnd)
 Await {(Viewer).open -and (Viewer).displayedId -eq 1} 'standalone decoded image'
 Await {(Viewer).negativeLength -eq 14} 'embedded metadata'
 Check ((Viewer).total -eq 1 -and (Viewer).neighborCount -eq 1 -and $null -eq (Viewer).previousId -and $null -eq (Viewer).nextId) 'only explicitly selected image in scope'
 Check (-not (Test-Path -LiteralPath (Join-Path $data 'winui-library-v1.json'))) 'main profile library pointer never opened'
 Check (Test-Path -LiteralPath (Join-Path $data 'single-view/winui-library-v1.json')) 'separate single-image pointer'
 Check (-not (Find 'ViewerPrevious').Current.IsEnabled -and -not (Find 'ViewerNext').Current.IsEnabled) 'single image navigation disabled'
 Check (-not (Find 'ViewerHeart').Current.IsEnabled -and -not (Find 'ViewerStar_5').Current.IsEnabled) 'standalone reactions are read only'
 Click 'ViewerInfo';Click 'ViewerCopyPrompt';Await {(Viewer).lastCopyLength -eq 18} 'copy prompt diagnostic sink'
 Click 'ViewerCopyNegative';Await {(Viewer).lastCopyLength -eq 14} 'copy negative diagnostic sink'
 Check ((Find 'ViewerFeedback').Current.Name -like '*已复制*') 'visible copy acknowledgement'
 Click 'ViewerInfo'
 Click 'ViewerActual';Await {-not (Viewer).isFit} 'actual pixels';Click 'ViewerZoomIn';Await {(Viewer).zoom -gt 1} 'zoom';Click 'ViewerFit';Await {(Viewer).isFit} 'fit'
 Click 'ViewerFullscreen';Await {(Viewer).fullscreen} 'fullscreen';Click 'ViewerFullscreen';Await {-not (Viewer).fullscreen} 'restore window'
 Await {(State).revision -eq (State).captureRevision} 'final XAML capture'
 Copy-Item -LiteralPath (Join-Path $data (State).captures[-1]) -Destination (Join-Path $output 'single-file-viewer.png')
 Click 'ViewerBack';Check ($process.WaitForExit(30000) -and $process.ExitCode -eq 0) 'back safely closes standalone window'
 Check ((Get-FileHash -LiteralPath $source).Hash -eq $before) 'original selected bytes unchanged'
 @{result='passed';checks=$checks;mainLibraryUntouched=$true;singleScope=$true;originalBytesUnchanged=$true;captureScope='own XAML and generated geometric image only';limitations='No associations, same-folder navigation or instance activation tested'}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $output 'single-file-window-report.json')
 Write-Output ('SINGLE_FILE_WINDOW_PASSED '+$checks)
}finally{if($process -and -not $process.HasExited){$process.CloseMainWindow()|Out-Null;$process.WaitForExit(30000)|Out-Null}}
