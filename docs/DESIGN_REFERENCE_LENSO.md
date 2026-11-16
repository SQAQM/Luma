# Lenso UI 对 Luma WinUI 3 的界面参考

研究日期：2026-11-16。参考源码固定到 [81b06bf](https://github.com/LioRael/lenso-ui/tree/81b06bf475d02c6fefd373ef04d0b942e46d82f2)。本轮为文档与源码研究，尚未修改程序界面。

中文在线文档的浏览器预览两次超时；已读取仓库对应的中文 MDX、组件和 Photos 查看器源码。以下区分源码事实、Luma 现状与待实现建议，不将源码阅读当作实际渲染检查。

## 适用范围

Lenso 是 React / StyleX 组件库。Luma 继续使用 C# / XAML WinUI 3，通过原生资源、控件模板和 Composition 实现相应视觉及交互。保留矩形按钮、两字侧栏、平铺常用操作、右键所在位置、图片下方收藏与评分，以及现有分页和有限缓存。

## 最有价值的参考

| 参考 | 已核对的源码行为 | Luma 的适配方向 |
| --- | --- | --- |
| 材质与主题 | 材质、前景文字、选择、焦点和状态有各自语义；玻璃染色不降低文字透明度 | 在 `Themes/Colors.xaml` 统一材质角色，使标题栏、侧栏、工具栏及弹窗有连续的色调和明确的深度；按钮保持玻璃语言，文字保持清楚 |
| Toolbar / Group | 工具组能够拥有共享材质；子组根据父表面决定自己的装饰层 | 优先整理查看器和模型页工具组：同一组共享边缘、染色与阴影，减少每个按钮重复描边造成的拥挤 |
| Select / Menu | 展示、列表、勾选区和弹层各有部件；`SelectContent` 默认 `alignItemWithTrigger=true` | 借鉴勾选槽、分组间距与文本对齐。展开方式按 Luma 的向下优先规则处理，边界不足时再适配；保持 WinUI 原生选择、键盘输入、关闭及焦点恢复 |
| Photos 查看器 | 包含图片舞台、工具组、信息区、缩略条、沉浸状态与缩放交互；输入区域和弹层不接管普通左右切图 | 保留现有查看器状态与解码管线，优先提高图片可用面积，收紧标题与工具区域；浮动工具组、信息展开与沉浸切换使用稳定空间关系 |
| 动效 | 状态过渡与控件生命周期绑定，支持中断、降低动态效果和焦点恢复 | 继续使用现有 Composition 按压反馈；统一工具组与弹层的状态过渡，不用额外计时器模拟打开、隐藏或恢复焦点 |

以上是针对本项目的设计判断，不等同于直接复制 Web 参数。

## 与现有代码的衔接

- `Themes/Controls.xaml` 已有共享玻璃模板、矩形按钮及 36 DIP 常规控件高度。后续重点是组内关系、主次操作、边缘强度和图标对齐，不再为每页增加另一套按钮。
- `Services/MaterialService.cs` 已管理窗口亚克力、主题、系统效果及高对比度。`Colors.xaml` 中 `AlwaysUseFallback=true` 是初始资源值，运行时会按材质策略切换；不能仅凭这一行判断亚克力失效或全局强制打开。
- `Services/GlassButtonEffects.cs` 已有 Compositor 弹簧按压、键盘激活、失焦复位和降低动态效果处理。优先复用这些行为。
- `Themes/Selections.xaml` 已保留原生 ComboBox / ComboBoxItem 及命名部件。修改弹层外观时保持这些契约，布局和尺寸应在第一帧正确，避免靠延时重排掩盖闪烁。
- `Views/ViewerView.xaml` 仍有独立的标题、工具、评分与缩略条区域。应在实际窗口里比较普通和沉浸模式的图片可用面积，再调整布局；不能只凭 Web 示例断言原生遮挡与缩放已解决。

## 性能取舍

Lenso 的 Photos 页面是示例图库；其查看器用完整数组查找相邻图片、渲染缩略条。Luma 保留 SQL 分页、当前有限图片窗口、缩略条重叠复用和有界解码缓存，不照搬示例数据层。

Lenso 的材质准备代码有可取消的短时间片检查（浏览器示例约 4 ms 后让出下一帧）。可借鉴“分批准备、缓存复用、离屏取消”的原则；该时间值不直接作为 WinUI 的性能承诺。光学采样与装饰层应限制在少量可见操作表面，避免为每张图片或模型卡片建立独立折射和后台刷新。

## 后续落地顺序

1. 统一标题栏、侧栏、工具栏、弹窗的材质角色与主次操作，保留矩形造型及现有功能入口。
2. 完善下拉框和菜单的勾选槽、文本基线、选中态、阴影及向下优先展开；同时观察第一帧与快速开关。
3. 调整查看器的图片可用面积、共享工具组、信息展开和沉浸状态，保持图片切换与输入焦点稳定。
4. 用合成资料检查明暗主题、100% / 150% 文字、小窗口及减少动态效果，并进行独立截图评审。只对受影响部分检查，避免重复构建与无关验收。

本轮没有安装 Web 依赖、下载示例照片或复制第三方组件代码。若后续复用代码，应核对所用文件的 MIT 及第三方版权说明。

## 已阅读的主要来源

- [Lenso 设计原则](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/DESIGN.md)
- [中文材料文档](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/apps/docs/content/lenso/cn/react/getting-started/materials.mdx)
- [中文动效文档](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/apps/docs/content/lenso/cn/react/getting-started/animation.mdx)
- [GlassButton](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/ui/src/components/glass-button/glass-button.tsx)、[Toolbar](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/ui/src/components/toolbar/toolbar.tsx)、[Select](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/ui/src/components/select/select.tsx)
- [Photos 中文说明](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/apps/docs/content/lenso/cn/react/templates/photos.mdx)、[查看器源码](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/apps/docs/src/components/photos-viewer.tsx)、[布局样式](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/apps/docs/src/styles/photos-demo.stylex.ts)
- [主题语义](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/tokens/src/theme.ts)、[材质准备时间片](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/glass/src/preparation-budget.ts)
