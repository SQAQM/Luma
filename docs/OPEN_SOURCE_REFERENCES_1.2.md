# 多帧浏览的源码参考（2026-11-06）

- [PicView GifInstance](https://github.com/Ruben2776/PicView/blob/c0105647e5580c48b895d943a4322c96d66b9e3e/src/PicView.Avalonia/AnimatedImage/GifInstance.cs)：帧时长、循环控制和关闭时取消。
- [PicView WebpInstance](https://github.com/Ruben2776/PicView/blob/c0105647e5580c48b895d943a4322c96d66b9e3e/src/PicView.Avalonia/AnimatedImage/WebpInstance.cs)：复用解码状态与像素目标，按帧更新。
- [PicView TIFF导航状态](https://github.com/Ruben2776/PicView/blob/c0105647e5580c48b895d943a4322c96d66b9e3e/src/PicView.Core/Navigation/Tiff/TiffNavigationInfo.cs)：文件页码与图库文件导航各自维护。
- [Pillow 12.3 WebP解码器](https://github.com/python-pillow/Pillow/blob/12.3.0/src/PIL/WebPImagePlugin.py#L119)：duration在load时更新，不能只seek后读取旧帧info。
- [Pillow格式文档](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)：GIF重复次数、APNG默认海报与帧序列、TIFF页尺寸。

参考行为、边界与格式，按Luma原生WinUI协议实现；未复制上述项目源码或引入其前端框架。当前逐帧PNG资源方案优先保持现有校验与有界缓存；慢解码会延长显示时间，尚未实现原生渲染线程的高帧率解码路径。
