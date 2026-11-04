using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;

namespace Luma.Preview.Views.Controls;

// Symbols beneath a photo, with ordinary button keyboard/automation behavior.
public sealed class ImageReactions : StackPanel
{
    public static readonly DependencyProperty FavoriteProperty = DependencyProperty.Register(nameof(Favorite), typeof(bool), typeof(ImageReactions), new PropertyMetadata(false, RefreshChanged));
    public static readonly DependencyProperty RatingProperty = DependencyProperty.Register(nameof(Rating), typeof(int), typeof(ImageReactions), new PropertyMetadata(0, RefreshChanged));
    public static readonly DependencyProperty CanEditProperty = DependencyProperty.Register(nameof(CanEdit), typeof(bool), typeof(ImageReactions), new PropertyMetadata(false, RefreshChanged));
    public static readonly DependencyProperty IdPrefixProperty = DependencyProperty.Register(nameof(IdPrefix), typeof(string), typeof(ImageReactions), new PropertyMetadata("", RefreshChanged));
    public bool Favorite { get => (bool)GetValue(FavoriteProperty); set => SetValue(FavoriteProperty, value); }
    public int Rating { get => (int)GetValue(RatingProperty); set => SetValue(RatingProperty, value); }
    public bool CanEdit { get => (bool)GetValue(CanEditProperty); set => SetValue(CanEditProperty, value); }
    public string IdPrefix { get => (string)GetValue(IdPrefixProperty); set => SetValue(IdPrefixProperty, value); }
    public event Action<bool?, int?>? ChangeRequested;
    private readonly Button _heart = new();
    private readonly Button[] _stars = Enumerable.Range(0, 5).Select(_ => new Button()).ToArray();
    private static void RefreshChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ImageReactions)sender).Refresh();

    public ImageReactions()
    {
        Orientation = Orientation.Horizontal; Spacing = 0; MinHeight = 28;
        GotFocus += (_, e) => { if (e.OriginalSource is FrameworkElement target) ScrollFocus.Request(target); };
        _heart.Click += (_, _) => ChangeRequested?.Invoke(!Favorite, null);
        Children.Add(_heart);
        for (int i = 0; i < _stars.Length; i++)
        {
            int value = i + 1;
            _stars[i].Click += (_, _) => ChangeRequested?.Invoke(null, Rating == value ? 0 : value);
            Children.Add(_stars[i]);
        }
        Refresh();
    }
    public void Bind(bool favorite, int rating, bool enabled, string prefix)
    {
        Favorite = favorite; Rating = rating; CanEdit = enabled; IdPrefix = prefix;
    }
    private void Refresh()
    {
        Configure(_heart, Favorite ? "♥" : "♡", Favorite ? "LumaHeartSymbol" : "LumaReactionSymbol", Favorite ? "取消收藏" : "收藏", IdPrefix.StartsWith("Gallery") ? IdPrefix.Replace("Gallery", "GalleryHeart") : IdPrefix + "Heart");
        for (int i = 0; i < _stars.Length; i++)
        {
            int value = i + 1;
            string id = IdPrefix.StartsWith("Gallery") ? IdPrefix.Replace("Gallery", "GalleryStar") + "_" + value : IdPrefix + "Star_" + value;
            Configure(_stars[i], Rating >= value ? "★" : "☆", Rating >= value ? "LumaStarSymbol" : "LumaReactionSymbol", Rating == value ? $"取消{value}星评分" : $"评为{value}星", id);
            AutomationProperties.SetItemStatus(_stars[i], Rating >= value ? "已选" : "未选");
        }
        AutomationProperties.SetItemStatus(_heart, Favorite ? "已收藏" : "未收藏");
    }
    private void Configure(Button button, string symbol, string style, string label, string id)
    {
        button.Style = (Style)Application.Current.Resources["LumaReaction"];
        button.IsEnabled = CanEdit;
        if (button.Content is not TextBlock text) { text = new TextBlock(); button.Content = text; }
        text.Text = symbol; text.Style = (Style)Application.Current.Resources[style];
        AutomationProperties.SetName(button, label); AutomationProperties.SetAutomationId(button, id);
        ToolTipService.SetToolTip(button, CanEdit ? label : "当前图片只读或正在保存");
    }
}

// The gallery's virtualized photos and the viewer's information pane need focus
// scrolling; unscrolled overlays do not. Defer until the focus/layout callback
// returns, then reject requests for collapsed, detached or no-longer-focused UI.
internal static class ScrollFocus
{
    internal static void Request(FrameworkElement target)
    {
        var root = target.XamlRoot;
        var scroll = VisibleScrollAncestor(target);
        if (root is null || scroll is null) return;
        target.DispatcherQueue.TryEnqueue(DispatcherQueuePriority.Low, () =>
        {
            if (!target.IsLoaded || !ReferenceEquals(target.XamlRoot, root) ||
                !ReferenceEquals(VisibleScrollAncestor(target), scroll) || scroll.ActualWidth <= 0 || scroll.ActualHeight <= 0) return;
            for (DependencyObject? focused = FocusManager.GetFocusedElement(root) as DependencyObject;
                 focused is not null; focused = VisualTreeHelper.GetParent(focused))
            {
                if (!ReferenceEquals(focused, target)) continue;
                target.StartBringIntoView(new BringIntoViewOptions { AnimationDesired = false, VerticalAlignmentRatio = .5 });
                return;
            }
        });
    }

    private static ScrollViewer? VisibleScrollAncestor(FrameworkElement target)
    {
        ScrollViewer? scroll = null;
        for (DependencyObject? node = target; node is not null; node = VisualTreeHelper.GetParent(node))
        {
            if (node is FrameworkElement element && (!element.IsLoaded || element.Visibility != Visibility.Visible)) return null;
            if (!ReferenceEquals(node, target) && scroll is null && node is ScrollViewer ancestor) scroll = ancestor;
        }
        return scroll;
    }
}
