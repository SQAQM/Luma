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
        Func<string> renderMode, Func<string,Task> setWallpaper, Func<Task> capture)
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
                TransparencySlider.XamlRoot==XamlRoot&&TransparencySlider.ActualHeight>0&&MaterialPicker.Items.Count==6&&
                TransparencySlider.Minimum==0&&TransparencySlider.Maximum==100,
                "settings exposes four materials, two custom backgrounds and a 0–100 transparency slider");
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
            var color=Windows.UI.Color.FromArgb(255,36,53,87);
            TextColorPicker.Color=color;
            Check(CustomAppearance.TextColor=="#243557"&&PageTitle.Foreground is SolidColorBrush heading&&heading.Color==color&&
                titleBar.Foreground is SolidColorBrush title&&title.Color==color&&
                MaterialPicker.Foreground is SolidColorBrush selector&&selector.Color==color,
                "custom font color repaints headings, title and native dropdown immediately");
            ResetTextColorClicked(ResetTextColor,new RoutedEventArgs());
            Check(CustomAppearance.TextColor is null&&PageTitle.Foreground is SolidColorBrush reset&&reset.Color!=color,
                "reset restores the theme foreground");

            MaterialPicker.SelectedIndex=4;
            var canvasColor=Windows.UI.Color.FromArgb(255,181,198,222);
            BackgroundColorPicker.Color=canvasColor;
            Check(CustomAppearance.BackgroundColor=="#B5C6DE"&&wash.Background is SolidColorBrush canvas&&canvas.Color==canvasColor&&wash.Opacity==1
                &&TransparencySlider.Visibility==Visibility.Collapsed,
                "custom background color fills the window and replaces the material controls");
            await Capture("custom-color");

            MaterialPicker.SelectedIndex=5;
            string fixture=Path.Combine(directory,"appearance-fixture.bmp");
            using(var stream=new BinaryWriter(File.Create(fixture)))
            {
                stream.Write((ushort)0x4d42);stream.Write(70);stream.Write(0);stream.Write(54);
                stream.Write(40);stream.Write(2);stream.Write(2);stream.Write((ushort)1);stream.Write((ushort)32);
                for(int i=0;i<6;i++)stream.Write(0);
                stream.Write(new byte[]{65,101,160,0,170,168,216,0,110,146,202,0,213,212,232,0});
            }
            await setWallpaper(fixture);
            Check(CustomAppearance.ImagePath==fixture&&wash.Background is ImageBrush { Stretch: Stretch.UniformToFill, ImageSource: not null }&&wash.Opacity==1,
                "chosen image decodes and covers the window without stretching");
            await Capture("custom-image");
            MaterialPicker.SelectedIndex=(int)BackgroundMaterial.Liquid;
            Check(TransparencySlider.IsEnabled&&BackgroundTransparency==67&&wash.Background is not ImageBrush,
                "return to material restores the previous transparency");

            static bool SameSurface(Brush left,Brush right)=>left is SolidColorBrush a&&right is SolidColorBrush b&&a.Color==b.Color&&Math.Abs(a.Opacity-b.Opacity)<.01;
            var density=FindAppearanceField<ComboBox>(Management,"ManagePreferenceDensity");
            var sort=FindAppearanceField<ComboBox>(Management,"ManagePreferenceSort");
            var thumbnail=FindAppearanceField<NumberBox>(Management,"ManagePreferenceThumbnail");
            Check(density is not null&&sort is not null&&thumbnail is not null&&
                SameSurface(density.Background,thumbnail.Background)&&SameSurface(sort.Background,thumbnail.Background)&&density.CornerRadius==thumbnail.CornerRadius&&
                Math.Abs(density.ActualHeight-sort.ActualHeight)<1,
                "settings dropdowns and numeric fields share the same glass fill and radius");
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

    private static T? FindAppearanceField<T>(DependencyObject root,string id) where T:FrameworkElement
    {
        if(root is T element&&Microsoft.UI.Xaml.Automation.AutomationProperties.GetAutomationId(element)==id)return element;
        for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++)
            if(FindAppearanceField<T>(VisualTreeHelper.GetChild(root,i),id) is { } child)return child;
        return null;
    }
}
