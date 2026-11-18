# Luma 1.4 原生玻璃与动效来源

参考基线为 Lenso UI [81b06bf475d02c6fefd373ef04d0b942e46d82f2](https://github.com/LioRael/lenso-ui/tree/81b06bf475d02c6fefd373ef04d0b942e46d82f2)，使用其设计分组与动效原理，按 Luma 的矩形桌面控件和性能要求进行原生适配。

- [press-model.ts](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/glass/src/motion/press-model.ts)、[press.ts](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/glass/src/motion/press.ts)、[spring.ts](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/glass/src/motion/spring.ts)：鼠标/触摸弹簧系数、位置归一化及可中断反馈；本项目限制为向内形变，并在合成器上限制边界，避免紧凑控件互相覆盖。
- 触摸响应上游为 [onlymoein/liquidglass 89f8b736](https://github.com/onlymoein/liquidglass/tree/89f8b736)。版权与完整 MIT 许可保存在 `packaging/LENSO-MOTION-LICENSE.txt` 并随两种交付包进入 ThirdPartyNotices。
- [Toolbar](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/packages/ui/src/components/toolbar/toolbar.tsx)、[材料文档](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/apps/docs/content/lenso/cn/react/getting-started/materials.mdx)、[Photos](https://github.com/LioRael/lenso-ui/blob/81b06bf475d02c6fefd373ef04d0b942e46d82f2/apps/docs/src/components/photos-viewer.tsx)：共享材质归属、内容优先、按任务分组和浮动查看控件。布局和 XAML 材质为本项目原生实现，没有复制 React 组件或示例照片。
- Microsoft [XAML/Composition 互操作](https://learn.microsoft.com/windows/apps/design/motion/xaml-property-animations)、[原生阴影](https://learn.microsoft.com/en-us/windows/apps/develop/ui/shadows)、[ImplicitShowAnimation](https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.xaml.hosting.elementcompositionpreview.setimplicitshowanimation)：使用合成器属性、原生阴影接收面及原生显隐生命周期；同一视觉对象不混用 facade 与 handout 变换。
- Microsoft [GotFocus](https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.xaml.uielement.gotfocus)：焦点事件异步发出，因此全屏闲置后主动返回视口的焦点事件不应重新展开工具。原生合成场景先复现该问题，再核对修复。

实际范围：WinUI 3、原生 Acrylic/ThemeShadow/Composition；没有引入 WebView、React、StyleX 或额外图形依赖。没有移植 Lenso 的 WebGL 背景采样/折射渲染器，不将本机材质描述为 Apple 原生渲染保证。性能与输入覆盖范围见版本验证记录。
