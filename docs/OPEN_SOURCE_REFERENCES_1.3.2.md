# 1.3.2 封面与资料参考

- [Civitai edge-url.ts](https://github.com/civitai/civitai/blob/main/src/client-utils/edge-url.ts)：媒体类型、CDN文件名与视频静态预览约定。
- [Civitai 版本接口](https://github.com/civitai/civitai/blob/main/src/pages/api/v1/model-versions/%5Bid%5D.ts)：按实际图片类型生成封面URL。
- 文件互通约定延续[1.3参考](OPEN_SOURCE_REFERENCES_1.3.md)，本轮默认只生成.civitai.info；WebUI及rgthree格式改为显式兼容导出。

复用缓存前核对本地文件身份和已验证SHA256。封面以有界完整解码后的新像素输出PNG；模型封面失败不会拿其他版本图片替代。精简仅处理完整匹配Luma所有权字段与哈希的旧兼容资料，先持久预览再确认，恢复目录位于软件Data。
