# astrbot_plugin_draw_with_duck

AstrBot 鸭子图绘图插件。用户发送 `/画图 提示词` 后，插件会使用已配置模型将提示词增强并转换为 Danbooru tag，调用 RunningHub 工作流生成鸭子图，再用 [copyangle/SS_tools](https://github.com/copyangle/SS_tools) 兼容解码逻辑提取原图。

## 功能

- `/画图 <提示词>`：文生图。
- `/画图帮助`：查看简要用法。
- 可选择已配置的模型 Provider，将中文提示词转换为 Danbooru tag 并增强。
- 本地规范化 LLM 输出，统一为小写、下划线、英文逗号分隔的 tag 列表；不会联网校验真实 Danbooru tag 库。
- 调用 RunningHub `/openapi/v2/run/workflow/{workflowId}` 提交已发布工作流。
- 下载鸭子图后本地解码，可按配置发送解码后的原图或未解码的鸭子图。
- 当发送鸭子图时，会附带 `https://duck.airush.top/` 提示用户可在线解码查看原图。
- 对 aiocqhttp/QQ 图片发送的 `retcode=1200` 假超时做容错，避免图片已发出但任务被误记为失败。

## 配置

必须填写：

- `runninghub_api_key`：RunningHub API Key。
- `workflow_id`：RunningHub 工作流 ID，默认已填入 `2055280648360873986`。

常用可选项：

- `output_image_mode`：输出图片模式。`decoded` 发送解码后的原图；`duck` 发送未解码的鸭子图并附带在线解码地址。
- `instance_type`：RunningHub 实例类型，`default` 为 24G 显存，`plus` 为 48G 显存。
- `use_personal_queue`：是否使用个人独占队列。
- `retain_seconds`：实例保留时长，通常仅企业共享 API Key 生效。
- `prompt_provider_id`：Danbooru tag 转换/增强使用的模型 Provider，留空时自动选择当前会话模型。
- `prompt_template`：最终正向提示词模板，`{prompt}` 会替换为规范化后的 Danbooru tag 列表。
- `default_width` / `default_height`：默认尺寸。
- `duck_password`：如果希望鸭子图加密，填写密码；解码时也会使用同一密码。
- `send_duck_image`：兼容旧配置，仅在 `output_image_mode=decoded` 时作为附加鸭子图备份开关。

## 工作流节点

仓库内保留了你提供的 Anima base v1 鸭子图版 API JSON 副本 `workflow.json` 作为节点参考。插件提交的是你已发布的 RunningHub 工作流，并默认覆盖：

- 正向提示词：节点 `11` 的 `text`
- 负向提示词：节点 `12` 的 `text`
- KSampler：节点 `19` 的 `seed`、`steps`、`cfg`
- 宽高：节点 `63` / `64` 的 `value`
- DuckHideNode 密码：节点 `99` 的 `password`

如果你的 RunningHub 工作流节点 ID 不同，请在插件配置中调整对应 ID。

## 依赖

插件只依赖 `aiohttp`、`numpy`、`pillow`。SS_tools 的 ComfyUI 节点依赖较重，本插件仅内置鸭子图解码所需的轻量兼容实现。
