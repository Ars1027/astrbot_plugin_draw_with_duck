# astrbot_plugin_draw_with_duck

AstrBot 鸭子图绘图插件。用户发送 `/画图 提示词` 后，插件会使用已配置模型按照同目录 `SKILL.md` 的 ANIMA3 规则增强并翻译提示词，调用 RunningHub 工作流生成鸭子图，再用 [copyangle/SS_tools](https://github.com/copyangle/SS_tools) 兼容解码逻辑提取原图。

## 功能

- `/画图 <提示词>`：文生图。
- `/画图帮助`：查看简要用法。
- 可选择已配置的模型 Provider，按 `SKILL.md` 规则增强并翻译提示词。
- 可配置最终 prompt 是否强制规范化为 Danbooru tag 格式；不会联网校验真实 Danbooru tag 库。
- 可选择提示词输出风格：严格 Danbooru tag、SKILL.md 混合格式，或基础 tag + 英文自然语言描述。
- 可配置画师：不添加、指定一个画师，或每次从候选列表随机抽取。
- 可配置提示词送入方式：写入工作流内部提示词输入节点，或由插件端生成最终 prompt 后直接写入最终 CLIPTextEncode。
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
- `prompt_output_style`：提示词增强输出风格。`danbooru_tags` 为严格 tag；`skill_mixed` 遵循 `SKILL.md` 的 tag + 短句；`natural_english` 为少量基础 tag + 2-3 句英文自然语言画面描述，不是纯 tag 模式。
- `prompt_danbooru_tag_format`：兼容旧配置。未配置 `prompt_output_style` 时才会用于映射输出风格。
- `prompt_delivery_mode`：提示词送入方式。`workflow_input` 适合把 prompt 写入工作流内部输入节点；`final_clip` 适合插件端生成最终 prompt 后直接写入最终 CLIPTextEncode.text。
- `instance_type`：RunningHub 实例类型，`default` 为 24G 显存，`plus` 为 48G 显存。
- `use_personal_queue`：是否使用个人独占队列。
- `retain_seconds`：实例保留时长，通常仅企业共享 API Key 生效。
- `prompt_provider_id`：提示词增强/翻译使用的模型 Provider，留空时自动选择当前会话模型。
- `prompt_template`：最终正向提示词模板，`{prompt}` 会替换为增强后的最终 prompt。
- `artist_mode`：画师选择模式。`none` 不添加画师；`fixed` 使用 `artist_id`；`random` 从 `artist_random_list` 随机抽取。
- `artist_id`：指定画师 ID。可以填 `unohana pochiko`，插件会规范化为 `@unohana_pochiko`。
- `artist_random_list`：随机画师候选列表，支持换行、逗号、分号分隔，也支持 `@tare@umi` 这种连续写法。
- `show_enhanced_prompt`：提交任务后显示实际发送给 RunningHub 的最终正向提示词，包含 `prompt_template` 中的预设内容。
- `duck_password`：如果希望鸭子图加密，填写密码；解码时也会使用同一密码。
- `send_duck_image`：兼容旧配置，仅在 `output_image_mode=decoded` 时作为附加鸭子图备份开关。

默认随机画师列表：

`@tare`, `@umi`, `@hjl`, `@unohana_pochiko`, `@ningen_mame`, `@sugimura_tomokazu`, `@jyt`, `@navy`, `@seungju_lee`, `@herio`, `@c.honey`, `@nahanmin`, `@misheng_liu_yin`, `@haruki_(colorful_macaron)`, `@daeho_cha`, `@yusan`, `@yue`, `@mokokoiro`, `@renge`, `@minowa_sukyaru`, `@chigusa_minori`

`natural_english` 示例输出：

`1girl, solo, Ijichi Nijika, Bocchi the Rock!, A cheerful anime girl, Ijichi Nijika, is smiling while standing on a Japanese street during the daytime. She has short blonde hair with a side ponytail and bright yellow eyes. The atmosphere is warm, lively, and relaxed, with soft natural light, clean line art, and delicate anime-style details.`

## SKILL.md

插件会在运行时读取同目录 `SKILL.md`，并把它作为 LLM 提示词增强/翻译的主要规则。修改 `SKILL.md` 后需要热重载或重启插件才会生效。若文件缺失或读取失败，插件会回退到内置的简化 ANIMA3/Danbooru 指令，不会阻塞绘图。

## 工作流节点

仓库内保留了你提供的 Anima base v1 鸭子图版 API JSON 副本 `workflow.json` 作为节点参考。插件提交的是你已发布的 RunningHub 工作流，并默认只覆盖必要输入。采样步数、CFG、seed、宽高等绘图参数全部使用工作流自身默认值：

- 正向提示词：节点 `11` 的 `text`
- 负向提示词：节点 `12` 的 `text`
- DuckHideNode 密码：节点 `99` 的 `password`

如果你的 RunningHub 工作流节点 ID 不同，请在插件配置中调整对应 ID。

### 新 ANIMA 工作流插件控制版

如果使用“超强动漫模型 ANIMA 正式版-全自动版本_仅鸭子图输出版”并希望由 AstrBot/Grok 负责增强、翻译和合并预设提示词，建议发布一份插件控制版工作流：

- 断开 `92 -> 11.text`，避免工作流内部 `RH_LLMAPI_Pro_Node` 覆盖插件生成的最终 prompt。
- 把节点 `91`、`96` 的固定质量词/画风词迁移到 `prompt_template`，例如 `masterpiece, best quality, score_9, score_8, highres, absurdres, anime screenshot, official art, {prompt}`；画师交给 `artist_mode` 管理。
- 插件配置建议：`prompt_delivery_mode=final_clip`，`prompt_node_id=11`，`prompt_field_name=text`，`negative_node_id=12`，`duck_password_node_id=100`。

如果不修改工作流，也可以使用 `prompt_delivery_mode=workflow_input` 并将 `prompt_node_id` 设为 `93`，但这样 prompt 还会经过工作流内部 LLM 二次处理。

## 依赖

插件只依赖 `aiohttp`、`numpy`、`pillow`。SS_tools 的 ComfyUI 节点依赖较重，本插件仅内置鸭子图解码所需的轻量兼容实现。
