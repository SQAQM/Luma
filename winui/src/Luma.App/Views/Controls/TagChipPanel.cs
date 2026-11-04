using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;
namespace Luma.Preview.Views.Controls;
public sealed class TagChipPanel : Panel
{
    protected override Size MeasureOverride(Size available)
    {
        double width = double.IsInfinity(available.Width) ? 800 : Math.Max(0, available.Width);
        double x = 0, y = 0, line = 0;
        foreach (UIElement child in Children)
        {
            if(child.Visibility==Visibility.Collapsed)continue;
            child.Measure(new Size(width, double.PositiveInfinity)); var size = child.DesiredSize;
            if (x > 0 && x + size.Width > width) { x = 0; y += line + 8; line = 0; }
            x += size.Width + 8; line = Math.Max(line, size.Height);
        }
        return new Size(width, y + line);
    }
    protected override Size ArrangeOverride(Size final)
    {
        double x = 0, y = 0, line = 0;
        var row=new List<UIElement>();
        void ArrangeRow(){
            double left=0;
            foreach(var child in row){var size=child.DesiredSize;bool label=child is TextBlock;child.Arrange(new Rect(left,label?y+(line-size.Height)/2:y,Math.Min(size.Width,final.Width),label?size.Height:line));left+=size.Width+8;}
            row.Clear();
        }
        foreach (UIElement child in Children)
        {
            if(child.Visibility==Visibility.Collapsed)continue;
            var size = child.DesiredSize;
            if (x > 0 && x + size.Width > final.Width) { ArrangeRow();x = 0; y += line + 8; line = 0; }
            row.Add(child);x += size.Width + 8;line = Math.Max(line, size.Height);
        }
        ArrangeRow();
        return final;
    }
}
