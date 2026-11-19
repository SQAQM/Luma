using System.Text.Json;
using Luma.Core.Design;
using Luma.Preview.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Views;

public sealed partial class ShellView
{
    private ElementTheme? _verificationSystemTheme;

    internal async Task VerifyAppearanceUi(string directory, FrameworkElement surface, Border wash, TitleBar titleBar,
        Func<string> renderMode, Func<Task> capture)
    {
        if (!_diagnostics) return;
        var checks = new List<(bool Passed, string Name)>();
        void Check(bool passed, string name) => checks.Add((passed, name));

        async Task Capture(string name)
        {
            await capture();
            string frame = Directory.EnumerateFiles(directory, "capture-*.png").MaxBy(File.GetLastWriteTimeUtc)
                ?? throw new InvalidOperationException("Synthetic appearance capture missing");
            File.Copy(frame, Path.Combine(directory, $"appearance-{name}.png"), true);
        }

        async Task VerifyFollowSystem(ElementTheme custom, ElementTheme system)
        {
            _verificationSystemTheme = system;
            RestorePreferences(custom.ToString(), false);
            Changed?.Invoke();
            await Task.Delay(120);
            var frames = new List<bool>();
            void OnFrame(object? sender, object args)
            {
                if (Theme != "System") return;
                static bool SameBrush(Brush actual, Brush expected) =>
                    actual is SolidColorBrush a && expected is SolidColorBrush b && a.Color == b.Color;
                var text = Brush("LumaText");
                frames.Add(surface.RequestedTheme==system && ActualTheme==system && PageTitle.ActualTheme==system
                    && SameBrush(PageTitle.Foreground,text) && SameBrush(titleBar.Foreground,text)
                    && _navigation.All(pair=>SameBrush(pair.Value.Foreground,Brush(pair.Key==WorkspacePage.Settings?"LumaNavInk":"LumaText"))));
            }
            CompositionTarget.Rendering += OnFrame;
            try
            {
                ((IInvokeProvider)new ButtonAutomationPeer(SystemButton).GetPattern(PatternInterface.Invoke)).Invoke();
                for (int i = 0; i < 40 && frames.Count < 3; i++) await Task.Delay(25);
            }
            finally { CompositionTarget.Rendering -= OnFrame; }
            Check(frames.Count>0 && frames.All(frame=>frame),$"{custom} to Follow System ({system}) paints matching text on every sampled frame without another click");
        }

        try
        {
            Navigate(WorkspacePage.Settings);
            for (int i=0;i<80 && (TransparencySlider.XamlRoot is null || TransparencySlider.ActualHeight<1);i++) await Task.Delay(50);

            if(!_accessibility.HighContrast)
            {
                await VerifyFollowSystem(ElementTheme.Dark,ElementTheme.Light);
                await VerifyFollowSystem(ElementTheme.Light,ElementTheme.Dark);
            }
            _verificationSystemTheme=null;
            RestorePreferences("Light",false);
            Changed?.Invoke();
            await Task.Delay(160);
            UpdateLayout();
            Check(MaterialPicker.XamlRoot==XamlRoot&&MaterialPicker.ActualHeight>=32&&
                TransparencySlider.XamlRoot==XamlRoot&&TransparencySlider.ActualHeight>0&&MaterialPicker.Items.Count==4&&
                TransparencySlider.Minimum==0&&TransparencySlider.Maximum==100,
                "settings exposes four materials and a 0–100 transparency slider");
            bool acrylicAllowed=_settings.AdvancedEffectsEnabled&&!_accessibility.HighContrast&&
                Microsoft.UI.Composition.SystemBackdrops.DesktopAcrylicController.IsSupported();
            if(acrylicAllowed)
            {
                foreach(var material in new[]{BackgroundMaterial.Acrylic,BackgroundMaterial.Frosted,BackgroundMaterial.Liquid})
                {
                    MaterialPicker.SelectedIndex=(int)material;
                    TransparencySlider.Value=0;
                    Check(renderMode()=="Solid"&&wash.Opacity==1&&Workspace.Opacity==1,material+" zero percent uses solid background");
                    TransparencySlider.Value=70;
                    Check((material==BackgroundMaterial.Liquid?renderMode()=="ClearGlass":renderMode() is "HostEffect" or "SystemAcrylic")&&
                        wash.Opacity is >0 and <1,material+" intermediate transparency uses the matching compositor path");
                    await Capture(material.ToString().ToLowerInvariant());
                    TransparencySlider.Value=100;
                    Check(renderMode()=="Clear"&&wash.Opacity==0&&((SolidColorBrush)RailTint.Background).Opacity==0&&
                        ((SolidColorBrush)Workspace.Background).Opacity==0&&Workspace.Opacity==1,
                        material+" maximum transparency clears blur, tint and pane backgrounds without dimming content");
                }
                TransparencySlider.Value=85;
            }
            else await Capture("fallback");
            MaterialPicker.SelectedIndex=(int)BackgroundMaterial.Solid;
            Check(!TransparencySlider.IsEnabled&&renderMode()=="Solid"&&wash.Opacity==1&&BackgroundTransparency==85,
                "solid switches off the layer and retains the previous transparency");
            await Capture("solid");
            MaterialPicker.SelectedIndex=(int)BackgroundMaterial.Liquid;
            TransparencySlider.Value=67;
            Check(TransparencySlider.IsEnabled&&BackgroundTransparency==67,"changing presets preserves a usable slider");
        }
        catch(Exception error) { Check(false,error.GetType().Name+": "+error.Message); }
        finally
        {
            _verificationSystemTheme=null;
            RefreshSystemPreferences();
        }
        await File.WriteAllTextAsync(Path.Combine(directory,"appearance-ui-report.json"),JsonSerializer.Serialize(new
        {
            result=checks.Count>0&&checks.All(check=>check.Passed)?"passed_native_appearance":"failed_native_appearance",
            checks=checks.Select(check=>new{passed=check.Passed,name=check.Name}),
            scope="synthetic profile, real native theme and compositor settings, controlled light and dark system color inputs"
        }));
    }
}
