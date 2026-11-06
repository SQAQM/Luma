# 1.3 模型资料互通参考

参考公开实现的文件约定，未引入前端框架或复制第三方实现。

- [Civitai Helper model.py](https://github.com/butaixianran/Stable-Diffusion-Webui-Civitai-Helper/blob/main/scripts/ch_lib/model.py)：`<stem>.civitai.info` 保存版本对象。
- [rgthree utils_info.py](https://github.com/rgthree/rgthree-comfy/blob/main/py/server/utils_info.py)：`<filename>.rgthree-info.json`、`raw.civitai`、触发词字段及同名预览。
- [AUTOMATIC1111 ui_extra_networks_user_metadata.py](https://github.com/AUTOMATIC1111/stable-diffusion-webui/blob/master/modules/ui_extra_networks_user_metadata.py)：`<stem>.json` 的 description、activation text、sd version 字段。
- [Civitai constants.ts](https://github.com/civitai/civitai/blob/main/src/server/common/constants.ts)：模型文件类型包含主模型与 Text Encoder、VAE 等附件，父类型不能覆盖明确附件类型。

同步成功的资料保留上游 modelId、版本号和实际本地 SHA256。封面采用同名 PNG；已有封面不覆盖。侧车 `_luma.managed` 只跟踪由 Luma 创建且尚未被用户修改的字段。用户字段即使与上游相同或被清空，也不自动认领。新文件采用不覆盖发布；已有 JSON 持锁更新，并在写入前保存恢复副本，成功后移除副本。

模型列表使用独立数据库版本号。图片标签和图库变化不再使模型游标过期；模型列表实际变化时返回明确 stale_cursor，原生界面恢复首页。读取失败保持原页码和历史。
