# 动图与多页图片

**Goal:** 补齐GIF/WebP动图和TIFF多页浏览，沿用原生WinUI查看器。

**方案:** 不依赖额外系统WebP插件，也不将动图转成有损GIF。Pillow在本地逐帧合成，输出现有受校验PNG资源；只保留一个压缩容器和有界像素缓存。原文件句柄仅在请求期间持有，关闭查看器释放容器。PNG压缩使用较低压缩级别减少逐帧延迟。

**交互:** 现有播放按钮在动图中控制动图，在静态图/文档中控制自动切图；不增加重复播放入口。多帧文件有页/帧导航，PageUp/PageDown切换内部页，左右键仍切图片。保留当前画面直到下一帧就绪，同尺寸帧保留缩放/旋转，减少动态效果时不自动播放。

**边界:** 仍保持128MiB源容器、50MP源画布、24MP/32MiB单帧、128MiB像素LRU限制；每文件最多4096帧/页。按文件实际时长和循环次数播放，慢帧不积压导航。缓存清理同时移除对应帧描述文件，避免大量动图留下孤立元数据。开发只用生成的测试文件与新建Data。

- [x] 以合成GIF、WebP、TIFF验证帧、时长、循环、尺寸、源身份、取消及缓存边界，先红后绿。
- [x] 实现逐帧资源、单容器复用及释放；扩展严格协议。
- [x] 原生控制器与有界解码接入，保留帧期间不清空画面，避免逐帧刷新窗口背景。
- [x] 核对多页、暂停/继续、切文件及大字体，独立代码/PNG评审。
- [ ] 正式1.2.0，更新当前程序与两种包、公开发布并清理本轮旧产物。

依据：[微软BitmapImage](https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.xaml.media.imaging.bitmapimage)、[Pillow多帧格式](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)、[WebP时长在load后更新的源码/问题记录](https://github.com/python-pillow/Pillow/issues/9790)。采用逐帧方案覆盖GIF/WebP/TIFF，不限定于原生GIF自动播放。
