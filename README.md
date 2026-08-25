# astrbot_plugin_draw_with_duck

AstrBot 鸭子图绘图插件。用户发送 `/画图 提示词` 后，插件默认使用已配置模型按照同目录 `SKILL.md` 的 ANIMA3 规则增强并翻译提示词，调用 RunningHub 工作流生成鸭子图，再用 [copyangle/SS_tools](https://github.com/copyangle/SS_tools) 兼容解码逻辑提取原图。

## 功能

- `/画图 <提示词>`：文生图。
- `/画图帮助`：查看简要用法。
- 开启提示词增强时，可选择已配置的模型 Provider，按 `SKILL.md` 规则增强并翻译提示词。
- 可配置最终 prompt 是否强制规范化为 Danbooru tag 格式；不会联网校验真实 Danbooru tag 库。
- 可选择提示词输出风格：严格 Danbooru tag、SKILL.md 混合格式，或基础 tag + 英文自然语言描述。
- 可配置画师：不添加、指定一个画师，或每次从候选列表随机抽取。
- 可选开启 R18 审查：基于增强结果或英文直投提示词本地判断，命中后自动切换到 R18 专用 RunningHub API Key 和工作流。
- 最终英文 prompt 固定写入插件控制版工作流的 `11.text`，不会再回退到工作流内部 LLM。
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
- `r18_review_enabled`：是否开启 R18 审查与自动路由。开启后会检查增强结果或英文直投提示词，不联网、不调用 LLM 审查。
- `r18_api_key`：R18 专用 RunningHub API Key。仅在开启审查且命中 R18 时使用。
- `r18_workflow_id`：R18 专用工作流 ID，默认 `2060002715337584642`。该工作流需与普通工作流使用相同节点 ID。
- `enhance_prompt`：开启时增强并翻译用户提示词；关闭后仅接受英文提示词，非英文输入不会提交 RunningHub。
- `prompt_output_style`：提示词增强输出风格。`danbooru_tags` 为严格 tag；`skill_mixed` 遵循 `SKILL.md` 的 tag + 短句；`natural_english` 为少量基础 tag + 2-3 句英文自然语言画面描述，不是纯 tag 模式。
- `prompt_danbooru_tag_format`：兼容旧配置。未配置 `prompt_output_style` 时才会用于映射输出风格。
- `instance_type`：RunningHub 实例类型，`default` 为 24G 显存，`plus` 为 48G 显存。
- `use_personal_queue`：是否使用个人独占队列。
- `retain_seconds`：实例保留时长，通常仅企业共享 API Key 生效。
- `prompt_provider_id`：提示词增强/翻译使用的模型 Provider，留空时自动选择当前会话模型。
- `prompt_timeout_seconds`：提示词增强最长等待时间，默认 120 秒、最小 10 秒。超时后仅英文原提示词会降级提交；非英文输入会中止，不创建 RunningHub 任务。
- `prompt_template`：最终正向提示词模板，`{prompt}` 会替换为增强结果或英文直投提示词。
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

插件只支持“超强动漫模型 ANIMA 正式版-全自动版本_仅鸭子图输出版”的插件控制版拓扑。仓库内的 `workflow.json` 是旧版历史参考，不会在运行时读取，也不再作为当前线上节点的权威定义。插件只覆盖必要输入，采样步数、CFG、seed、宽高等参数使用已发布工作流自身的默认值：

- 正向提示词：节点 `11` 的 `text`
- 负向提示词：节点 `12` 的 `text`
- DuckHideNode 密码：节点 `100` 的 `password`
- 最终 SaveImage 输出：节点 `86`

节点 `11.text` 必须保持未连线，由插件直接写入最终英文 prompt；当前插件不支持节点 `93` 或其他工作流内部 LLM 输入方式。普通与 R18 工作流都必须遵循相同节点契约。

### 提示词降级规则

- 增强成功：格式化模型返回的英文 prompt，再写入节点 `11.text`。
- 增强关闭：只有英文原提示词可以直投；中文或中英混合输入会直接拒绝。
- 增强超时、无可用 Provider 或连续失败：英文原提示词可以降级提交；非英文输入会中止，并明确说明没有创建 RunningHub 任务。
- LLM 返回非英文文本：视为无效结果并重试，不能把非英文内容直接送入最终文本编码节点。

升级自旧版本时，`prompt_delivery_mode`、`prompt_node_id`、`prompt_field_name`、`negative_node_id`、`duck_password_node_id` 等旧配置会被忽略，并在插件启动时记录一次迁移警告。

## 依赖

插件只依赖 `aiohttp`、`numpy`、`pillow`。SS_tools 的 ComfyUI 节点依赖较重，本插件仅内置鸭子图解码所需的轻量兼容实现。
