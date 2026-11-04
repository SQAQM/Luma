using Luma.Core.Viewer;
namespace Luma.Preview.Services;
[Microsoft.UI.Xaml.Data.Bindable]
public sealed class TextChunkRow(string text,double fontSize)
{
 public string Text{get;}=text;public double FontSize{get;}=fontSize;
 public static TextChunkRow[] Create(string value,double scale)=>TextChunks.Split(value).Select(t=>new TextChunkRow(t,14*scale)).ToArray();
}
