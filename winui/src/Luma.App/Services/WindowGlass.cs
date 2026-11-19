using System.Runtime.InteropServices;
using Luma.Core.Design;
using Microsoft.Graphics.Canvas.Effects;
using Microsoft.UI.Xaml;
using Windows.UI.Composition;
using WinRT;
using WinUIEx;
using ICompositionSupportsSystemBackdrop=Microsoft.UI.Composition.ICompositionSupportsSystemBackdrop;

namespace Luma.Preview.Services;

// Windows supplies the window-behind image directly to the compositor.
// The application does not capture or read desktop pixels.
internal sealed class WindowGlass(Window window) : IDisposable
{
    private ICompositionSupportsSystemBackdrop? _target;
    private CompositionBrush? _clear;
    private CompositionBackdropBrush? _source;
    private CompositionEffectFactory? _factory;
    private CompositionEffectBrush? _effect;
    private bool _hostEnabled;
    private BackgroundLook? _look;
    private nint Hwnd => WinRT.Interop.WindowNative.GetWindowHandle(window);

    public bool IsOpen => _target is not null;

    public void ShowClear()
    {
        var target=EnsureTarget();
        if(!ReferenceEquals(target.SystemBackdrop,_clear)) target.SystemBackdrop=_clear;
    }

    public void ShowEffect(BackgroundLook look,float opacity)
    {
        var target=EnsureTarget();
        if(_effect is null) CreateEffect();
        var effect=_effect!;
        if(_look!=look)
        {
            effect.Properties.InsertScalar("Blur.BlurAmount",look.Blur);
            effect.Properties.InsertScalar("Tone.Saturation",look.Saturation);
            _look=look;
        }
        effect.Properties.InsertScalar("Fade.Opacity",opacity);
        if(!ReferenceEquals(target.SystemBackdrop,effect)) target.SystemBackdrop=effect;
    }

    private ICompositionSupportsSystemBackdrop EnsureTarget()
    {
        if(_target is not null)return _target;
        var target=window.As<ICompositionSupportsSystemBackdrop>();
        window.SystemBackdrop=new TransparentTintBackdrop();
        _clear=target.SystemBackdrop??throw new InvalidOperationException("Transparent backdrop failed to connect.");
        return _target=target;
    }

    private void CreateEffect()
    {
        if(OperatingSystem.IsWindowsVersionAtLeast(10,0,22000))
        {
            int enabled=1;
            if(DwmSetWindowAttribute(Hwnd,17,ref enabled,sizeof(int))<0)
                throw new NotSupportedException("Windows host backdrop is unavailable.");
            _hostEnabled=true;
        }
        var compositor=_clear!.Compositor;
        _source=compositor.CreateHostBackdropBrush();
        var blur=new GaussianBlurEffect
        {
            Name="Blur", BlurAmount=13, BorderMode=EffectBorderMode.Hard,
            Source=new CompositionEffectSourceParameter("Desktop")
        };
        var tone=new SaturationEffect {Name="Tone",Source=blur};
        var fade=new OpacityEffect {Name="Fade",Source=tone};
        _factory=compositor.CreateEffectFactory(fade,["Blur.BlurAmount","Tone.Saturation","Fade.Opacity"]);
        _effect=_factory.CreateBrush();
        _effect.SetSourceParameter("Desktop",_source);
    }

    public void Dispose()
    {
        if(_target is not null)
        {
            _target.SystemBackdrop=_clear;
            window.SystemBackdrop=null;
            _target=null;_clear=null;
        }
        else if(window.SystemBackdrop is TransparentTintBackdrop)
            window.SystemBackdrop=null;
        _effect?.Dispose();_effect=null;
        _factory?.Dispose();_factory=null;
        _source?.Dispose();_source=null;
        _look=null;
        if(_hostEnabled)
        {
            int disabled=0;
            _=DwmSetWindowAttribute(Hwnd,17,ref disabled,sizeof(int));
            _hostEnabled=false;
        }
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(nint window,int attribute,ref int value,int size);
}
