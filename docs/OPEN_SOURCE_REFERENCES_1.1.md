# 本轮代码研究（2026-11-06）

按GitHub官方API当日数据挑选并固定源码：

| 项目 | Stars | 阅读位置 | 采用的设计 |
|---|---:|---|---|
| PicView | 3,735 | [Slideshow.cs](https://github.com/Ruben2776/PicView/blob/c0105647e5580c48b895d943a4322c96d66b9e3e/src/PicView.Avalonia/Navigation/Slideshow.cs) | 播放、暂停与当前查看窗口关联，关闭时释放计时 |
| ImageGlass | 14,843 | [SlideshowProvider.cs](https://github.com/d2phap/ImageGlass/blob/0c8113ac19f4b2ac37691b597db62c1da0457d90/source/ImageGlass.Lib/Common/ServiceProviders/SlideshowProvider.cs) | 等前一次切换结束再计时，避免解码慢时累积跳图 |
| Stability Matrix | 8,988 | [ConnectedModelInfo.cs](https://github.com/LykosAI/StabilityMatrix/blob/87dced77c1980e73c467dfac67009a94a9aa3d90/StabilityMatrix.Core/Models/ConnectedModelInfo.cs) | 同名.cm-info.json保存模型名称、版本、底模、介绍、触发词 |
| Civitai Helper | 2,518 | [model.py](https://github.com/butaixianran/Stable-Diffusion-Webui-Civitai-Helper/blob/e33b1c5cf7091ec1e5d95240350d59a6f360fe4b/scripts/ch_lib/model.py) | 复用模型旁已有JSON资料，与本地封面结合，减少重复联网 |

参考行为与数据格式，按Luma现有协议自行实现。保留原生WinUI，不引入网页前端。侧车资料只关联当前同名文件；不会将其中声称的SHA256登记为文件字节核验结果。查看器仍使用有界解码和邻居查询，自动播放不会加载整个目录。
