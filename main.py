import asyncio
import json
import random
import re
import time
import traceback
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .ss_tools_decoder import DuckDecodeError, decode_duck_image


DEFAULT_POSITIVE_TEMPLATE = (
    "masterpiece, best_quality, amazing_quality, detailed, newest, anime_coloring, "
    "anime_screenshot, {prompt}"
)
DUCK_DECODER_URL = "https://duck.airush.top/"
VALID_OUTPUT_IMAGE_MODES = {"decoded", "duck"}
VALID_PROMPT_DELIVERY_MODES = {"workflow_input", "final_clip"}
VALID_PROMPT_OUTPUT_STYLES = {"danbooru_tags", "skill_mixed", "natural_english"}
VALID_ARTIST_MODES = {"none", "fixed", "random"}
DEFAULT_ARTIST_IDS = (
    "@tare",
    "@umi",
    "@hjl",
    "@unohana_pochiko",
    "@ningen_mame",
    "@sugimura_tomokazu",
    "@jyt",
    "@navy",
    "@seungju_lee",
    "@herio",
    "@c.honey",
    "@nahanmin",
    "@misheng_liu_yin",
    "@haruki_(colorful_macaron)",
    "@daeho_cha",
    "@yusan",
    "@yue",
    "@mokokoiro",
    "@renge",
    "@minowa_sukyaru",
    "@chigusa_minori",
)
DEFAULT_ARTIST_RANDOM_LIST = "\n".join(DEFAULT_ARTIST_IDS)
FALLBACK_PROMPT_SKILL = (
    "You are an ANIMA3 prompt engineer for anime text-to-image generation. "
    "Translate and enhance the user's idea into one concise English positive prompt. "
    "Use Danbooru-style tags first, ordered by subject count, character identity, appearance, "
    "clothing/state, pose/action, expression, camera/shot, scene/environment, and detail/mood. "
    "Return only one line of final prompt text. Do not output explanations, markdown, JSON, or self-check notes."
)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


@register(
    "astrbot_plugin_draw_with_duck",
    "Luochang",
    "按 SKILL.md 规则增强并翻译提示词，调用 RunningHub 生成鸭子图并用 SS_tools 解码后返回图片",
    "v1.1.1",
)
class DrawWithDuckPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.api_key = str(config.get("runninghub_api_key", "") or "").strip()
        self.workflow_id = str(config.get("workflow_id", "2055280648360873986") or "").strip()
        self.api_base = str(config.get("api_base", "https://www.runninghub.ai") or "").rstrip("/")
        self.query_api_base = str(
            config.get("query_api_base", self.api_base) or self.api_base
        ).rstrip("/")
        self.add_metadata = _as_bool(config.get("add_metadata", True), True)
        self.instance_type = str(config.get("instance_type", "default") or "default").strip()
        self.use_personal_queue = _as_bool(config.get("use_personal_queue", False), False)
        self.retain_seconds = int(config.get("retain_seconds", 0) or 0)

        self.prompt_node_id = str(config.get("prompt_node_id", "11") or "11").strip()
        self.prompt_field_name = str(config.get("prompt_field_name", "text") or "text").strip()
        self.negative_node_id = str(config.get("negative_node_id", "12") or "12").strip()
        self.negative_prompt = str(config.get("negative_prompt", "") or "").strip()
        self.duck_password_node_id = str(config.get("duck_password_node_id", "99") or "99").strip()
        self.duck_password = str(config.get("duck_password", "") or "")

        self.enhance_prompt = _as_bool(config.get("enhance_prompt", True), True)
        legacy_danbooru_format = _as_bool(config.get("prompt_danbooru_tag_format", True), True)
        self.prompt_output_style = str(config.get("prompt_output_style", "") or "").strip().lower()
        if not self.prompt_output_style:
            self.prompt_output_style = "danbooru_tags" if legacy_danbooru_format else "skill_mixed"
        if self.prompt_output_style not in VALID_PROMPT_OUTPUT_STYLES:
            logger.warning(
                f"invalid prompt_output_style={self.prompt_output_style}, fallback by legacy config"
            )
            self.prompt_output_style = "danbooru_tags" if legacy_danbooru_format else "skill_mixed"
        self.prompt_danbooru_tag_format = self.prompt_output_style == "danbooru_tags"
        self.prompt_provider_id = str(config.get("prompt_provider_id", "") or "").strip()
        self.prompt_template = str(
            config.get("prompt_template", DEFAULT_POSITIVE_TEMPLATE) or DEFAULT_POSITIVE_TEMPLATE
        )
        self.artist_mode = str(config.get("artist_mode", "none") or "none").strip().lower()
        if self.artist_mode not in VALID_ARTIST_MODES:
            logger.warning(f"invalid artist_mode={self.artist_mode}, fallback to none")
            self.artist_mode = "none"
        self.artist_id = self._normalize_artist_id(str(config.get("artist_id", "") or ""))
        self.artist_random_list = str(
            config.get("artist_random_list", DEFAULT_ARTIST_RANDOM_LIST) or DEFAULT_ARTIST_RANDOM_LIST
        )
        self.prompt_delivery_mode = str(
            config.get("prompt_delivery_mode", "workflow_input") or "workflow_input"
        ).strip().lower()
        if self.prompt_delivery_mode not in VALID_PROMPT_DELIVERY_MODES:
            logger.warning(
                f"invalid prompt_delivery_mode={self.prompt_delivery_mode}, fallback to workflow_input"
            )
            self.prompt_delivery_mode = "workflow_input"
        if self.prompt_delivery_mode == "final_clip" and self.prompt_node_id == "93":
            logger.warning(
                "prompt_delivery_mode=final_clip usually needs prompt_node_id to point to the final "
                "CLIPTextEncode text field, not the workflow LLM input node"
            )
        self.show_enhanced_prompt = _as_bool(config.get("show_enhanced_prompt", False), False)
        self.output_image_mode = str(config.get("output_image_mode", "decoded") or "decoded").strip().lower()
        if self.output_image_mode not in VALID_OUTPUT_IMAGE_MODES:
            logger.warning(f"invalid output_image_mode={self.output_image_mode}, fallback to decoded")
            self.output_image_mode = "decoded"
        self.send_duck_image = _as_bool(config.get("send_duck_image", False), False)

        self.max_retries = max(1, int(config.get("max_retries", 60) or 60))
        self.poll_interval = max(1, int(config.get("poll_interval", 5) or 5))
        self.timeout_seconds = max(30, int(config.get("timeout_seconds", 600) or 600))
        self.keep_files = max(1, int(config.get("keep_files", 30) or 30))

        self.session: aiohttp.ClientSession | None = None
        self._tasks: set[asyncio.Task] = set()
        self._prompt_skill_text: str | None = None

    async def initialize(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        logger.info("DrawWithDuck plugin initialized")

    async def terminate(self):
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        if self.session:
            await self.session.close()
            self.session = None
        logger.info("DrawWithDuck plugin terminated")

    @filter.command("画图", alias={"drawduck", "duckdraw"}, priority=1)
    async def draw(self, event: AstrMessageEvent):
        event.stop_event()

        raw_prompt = self._extract_command_arg(event.message_str)
        if not raw_prompt:
            yield event.plain_result("用法：/画图 提示词\n例如：/画图 蓝发机器人少女，夜晚水面，赛博朋克")
            return

        config_error = self._check_config()
        if config_error:
            yield event.plain_result(config_error)
            return

        yield event.plain_result("收到，正在润色提示词并提交 RunningHub 任务。")

        try:
            enhanced_prompt = await self._enhance_prompt(event, raw_prompt)
            final_prompt = self._build_final_prompt(enhanced_prompt)
            task_resp = await self._submit_task(final_prompt)
            task_id = self._extract_task_id(task_resp)
            if not task_id:
                yield event.plain_result(f"RunningHub 未返回 taskId：{self._brief_json(task_resp)}")
                return
        except Exception as exc:
            logger.error(f"submit draw task failed: {traceback.format_exc()}")
            yield event.plain_result(f"提交任务失败：{exc}")
            return

        task_info = {
            "task_id": task_id,
            "umo": event.unified_msg_origin,
            "sender_id": str(event.get_sender_id()),
            "raw_prompt": raw_prompt,
            "enhanced_prompt": enhanced_prompt,
            "final_prompt": final_prompt,
            "prompt_output_style": self.prompt_output_style,
            "artist_mode": self.artist_mode,
            "prompt_delivery_mode": self.prompt_delivery_mode,
            "created_at": time.time(),
        }
        await self.put_kv_data(f"duck_task_{task_id}", json.dumps(task_info, ensure_ascii=False))

        msg = f"任务已提交：{task_id}"
        if self.show_enhanced_prompt:
            msg += f"\n最终正向提示词：{final_prompt}"
        yield event.plain_result(msg)

        task = asyncio.create_task(self._background_polling(task_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @filter.command("画图帮助", alias={"duckdrawhelp"}, priority=1)
    async def draw_help(self, event: AstrMessageEvent):
        event.stop_event()
        yield event.plain_result(
            "鸭子图绘图插件\n"
            "用法：/画图 提示词\n"
            "示例：/画图 蓝发机器人少女，夜晚水面，赛博朋克\n"
            "流程：当前会话模型增强并翻译提示词 -> RunningHub 生成鸭子图 -> SS_tools 解码 -> 返回解码后的图片。"
        )

    def _extract_command_arg(self, message: str) -> str:
        text = (message or "").strip()
        if not text:
            return ""
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            return ""
        return parts[1].strip()

    def _check_config(self) -> str:
        if not self.api_key:
            return "请先在插件配置中填写 runninghub_api_key。"
        if not self.workflow_id:
            return "请先在插件配置中填写 workflow_id，即 RunningHub 工作流 ID。"
        return ""

    async def _enhance_prompt(self, event: AstrMessageEvent, prompt: str) -> str:
        if not self.enhance_prompt:
            return self._format_enhanced_prompt(prompt) or prompt

        provider_id = await self._get_prompt_provider_id(event.unified_msg_origin)
        if not provider_id:
            logger.warning("no available prompt provider, fallback to raw prompt")
            return self._format_enhanced_prompt(prompt) or prompt

        system_prompt = self._build_prompt_system_prompt()
        user_prompt = self._build_prompt_user_prompt(prompt)

        for attempt in range(2):
            try:
                resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                )
                text = self._format_enhanced_prompt(getattr(resp, "completion_text", "") or "")
                if text:
                    return text
            except Exception as exc:
                logger.warning(f"prompt enhancement failed ({attempt + 1}/2): {exc}")
                await asyncio.sleep(1)

        return self._format_enhanced_prompt(prompt) or prompt

    def _build_prompt_system_prompt(self) -> str:
        skill_text = self._load_prompt_skill()
        if self.prompt_output_style == "danbooru_tags":
            format_rule = (
                "After following the skill, force the final output into strict Danbooru-style tags: "
                "lowercase English, comma-separated, underscores instead of spaces, no natural-language sentences."
            )
        elif self.prompt_output_style == "natural_english":
            format_rule = (
                "Natural English output protocol overrides any conflicting output-format instructions in the skill. "
                "Return exactly one line in this shape: "
                "'1girl, solo, Character Name, Series Title, A natural English sentence... Another sentence...'. "
                "The prefix must contain only 3-6 short comma-separated basics: subject count, solo/group, "
                "character name, series title, and essential identity tags. Do not put appearance, clothing, "
                "expression, pose, action, background, lighting, mood, or style details into the prefix as tags. "
                "Write those details as 2-3 complete natural English sentences, about 35-80 English words after "
                "the prefix. Preserve proper capitalization, character names, series titles, and punctuation. "
                "Do not force underscores. Do not output a pure tag list. "
                "Example input: 伊地知虹夏微笑站在日本街道. "
                "Example output: 1girl, solo, Ijichi Nijika, Bocchi the Rock!, A cheerful anime girl, Ijichi Nijika, "
                "is smiling while standing on a Japanese street during the daytime. She has short blonde hair "
                "with a side ponytail and bright yellow eyes. The atmosphere is warm, lively, and relaxed, "
                "with soft natural light, clean line art, and delicate anime-style details."
            )
        else:
            format_rule = (
                "After following the skill, keep its output protocol: tags first, with a short English "
                "natural-language supplement at the end only when the skill says it is needed."
            )
        return (
            f"{skill_text}\n\n"
            "Runtime constraints for this plugin:\n"
            "- Follow the skill above when translating and enhancing the user idea.\n"
            "- Output only the final positive prompt as one plain-text line.\n"
            "- Do not output explanations, markdown, code fences, JSON, headings, self-check notes, or negative prompt.\n"
            f"- {format_rule}"
        )

    def _build_prompt_user_prompt(self, prompt: str) -> str:
        if self.prompt_output_style == "natural_english":
            return (
                "User idea:\n"
                f"{prompt}\n\n"
                "Return only the final one-line positive prompt. Use a short 3-6 item tag prefix, "
                "then 2-3 complete natural English sentences. Do not return a pure comma-separated tag list."
            )
        return (
            "User idea:\n"
            f"{prompt}\n\n"
            "Return only the final one-line prompt."
        )

    def _load_prompt_skill(self) -> str:
        if self._prompt_skill_text is not None:
            return self._prompt_skill_text

        skill_path = Path(__file__).with_name("SKILL.md")
        for encoding in ("utf-8", "utf-8-sig", "gbk"):
            try:
                self._prompt_skill_text = skill_path.read_text(encoding=encoding).strip()
                if self._prompt_skill_text:
                    return self._prompt_skill_text
            except UnicodeDecodeError:
                continue
            except FileNotFoundError:
                logger.warning(f"prompt skill file not found: {skill_path}")
                break
            except Exception as exc:
                logger.warning(f"failed to read prompt skill file {skill_path}: {exc}")
                break

        self._prompt_skill_text = FALLBACK_PROMPT_SKILL
        return self._prompt_skill_text

    async def _get_prompt_provider_id(self, umo: str) -> str | None:
        if self.prompt_provider_id:
            try:
                provider = self.context.get_provider_by_id(self.prompt_provider_id)
                if provider:
                    return self.prompt_provider_id
                logger.warning(f"configured prompt provider is unavailable: {self.prompt_provider_id}")
            except Exception as exc:
                logger.warning(f"get configured prompt provider failed: {exc}")

        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            if provider_id:
                return provider_id
        except Exception as exc:
            logger.warning(f"get current provider failed: {exc}")

        try:
            providers = self.context.get_all_providers()
            if providers:
                meta = providers[0].meta()
                provider_id = getattr(meta, "id", None)
                if provider_id:
                    return provider_id
        except Exception as exc:
            logger.warning(f"get first available provider failed: {exc}")

        return None

    def _clean_llm_prompt(self, text: str) -> str:
        return self._format_enhanced_prompt(text)

    def _format_enhanced_prompt(self, text: str) -> str:
        if self.prompt_output_style == "natural_english":
            return self._clean_natural_english_prompt(text)
        cleaned = self._clean_prompt_text(text)
        if self.prompt_output_style == "danbooru_tags":
            return self._normalize_danbooru_tags(cleaned)
        return cleaned

    def _clean_natural_english_prompt(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r"^```(?:\w+)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
        text = text.strip("\"'` \n\r\t")
        text = re.sub(r"^\s*(?:[-*+•]|\d+[\.)]|[a-zA-Z][\.)])\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\b(?:positive\s*)?prompt\s*[:：]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:tags?|danbooru\s*tags?)\s*[:：]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[\n\r]+", " ", text)
        text = re.sub(r"(,\s*){2,}", ", ", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        return " ".join(text.split())

    def _clean_prompt_text(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r"^```(?:\w+)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
        text = text.strip("\"'` \n\r\t")
        text = re.sub(r"^\s*(?:[-*+•]|\d+[\.)]|[a-zA-Z][\.)])\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\b(?:positive\s*)?prompt\s*[:：]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:tags?|danbooru\s*tags?)\s*[:：]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[\n\r]+", ", ", text)
        text = re.sub(r"(,\s*){2,}", ", ", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        return " ".join(text.split())

    def _normalize_danbooru_tags(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        text = re.sub(r"^```(?:\w+)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
        text = text.strip("\"'` \n\r\t")
        text = re.sub(r"^\s*(?:[-*+•]|\d+[\.)]|[a-zA-Z][\.)])\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\b(?:positive\s*)?prompt\s*[:：]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:tags?|danbooru\s*tags?)\s*[:：]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[\n\r;；、，]+", ",", text)

        tags: list[str] = []
        seen: set[str] = set()
        for raw_tag in text.split(","):
            tag = raw_tag.strip().strip("\"'`[]{}")
            if not tag:
                continue
            tag = tag.lower()
            tag = re.sub(r"\s*-\s*", "_", tag)
            tag = re.sub(r"\s+", "_", tag)
            tag = re.sub(r"[^a-z0-9_():.+-]", "", tag)
            tag = re.sub(r"_+", "_", tag).strip("_")
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)

        return ", ".join(tags)

    def _build_final_prompt(self, prompt: str) -> str:
        try:
            final_prompt = self.prompt_template.format(prompt=prompt)
        except Exception:
            final_prompt = DEFAULT_POSITIVE_TEMPLATE.format(prompt=prompt)
        return self._apply_artist_prompt(final_prompt.strip())

    def _apply_artist_prompt(self, prompt: str) -> str:
        artist = self._select_artist_id()
        if not artist:
            return prompt
        if self._prompt_has_artist(prompt, artist):
            return prompt
        if not prompt:
            return artist
        return f"{artist}, {prompt}"

    def _select_artist_id(self) -> str:
        if self.artist_mode == "none":
            return ""
        if self.artist_mode == "fixed":
            return self.artist_id
        artists = self._parse_artist_list(self.artist_random_list)
        if not artists:
            artists = list(DEFAULT_ARTIST_IDS)
        return random.choice(artists)

    def _parse_artist_list(self, text: str) -> list[str]:
        artists: list[str] = []
        seen: set[str] = set()
        for raw_artist in re.split(r"[\n\r,;，；、]+|(?=@)", text or ""):
            artist = self._normalize_artist_id(raw_artist)
            if not artist or artist in seen:
                continue
            seen.add(artist)
            artists.append(artist)
        return artists

    def _normalize_artist_id(self, artist: str) -> str:
        artist = (artist or "").strip().strip("\"'`")
        if not artist:
            return ""
        artist = artist.lstrip("@").strip().lower()
        artist = re.sub(r"\s+", "_", artist)
        artist = re.sub(r"_+", "_", artist)
        artist = re.sub(r"[^a-z0-9_().+-]", "", artist).strip("_")
        if not artist:
            return ""
        return f"@{artist}"

    def _prompt_has_artist(self, prompt: str, artist: str) -> bool:
        return bool(re.search(rf"(?<![a-zA-Z0-9_]){re.escape(artist)}(?![a-zA-Z0-9_])", prompt, re.IGNORECASE))

    async def _submit_task(self, prompt: str) -> dict[str, Any]:
        node_info_list = self._build_node_info_list(prompt)
        payload: dict[str, Any] = {
            "addMetadata": self.add_metadata,
            "nodeInfoList": node_info_list,
            "instanceType": self.instance_type,
            "usePersonalQueue": self.use_personal_queue,
        }
        if self.retain_seconds > 0:
            payload["retainSeconds"] = self.retain_seconds

        headers = self._headers(with_bearer=True)
        url = f"{self.api_base}/openapi/v2/run/workflow/{self.workflow_id}"
        assert self.session is not None
        async with self.session.post(url, json=payload, headers=headers) as resp:
            data = await self._read_json_response(resp)
        if not data.get("taskId"):
            raise RuntimeError(self._brief_json(data))
        return data

    def _build_node_info_list(self, prompt: str) -> list[dict[str, Any]]:
        # workflow_input mode targets the workflow's text input node.
        # final_clip mode targets the final CLIPTextEncode text field; the published
        # workflow must leave that text widget unlinked so this value is not overwritten.
        items: list[dict[str, Any]] = [
            {"nodeId": self.prompt_node_id, "fieldName": self.prompt_field_name, "fieldValue": prompt},
        ]
        if self.negative_prompt:
            items.append(
                {
                    "nodeId": self.negative_node_id,
                    "fieldName": "text",
                    "fieldValue": self.negative_prompt,
                }
            )
        if self.duck_password:
            items.append(
                {
                    "nodeId": self.duck_password_node_id,
                    "fieldName": "password",
                    "fieldValue": self.duck_password,
                }
            )
        return items

    def _headers(self, with_bearer: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if with_bearer:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _read_json_response(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON response: {text[:300]}") from exc

    def _extract_task_id(self, data: dict[str, Any]) -> str:
        if data.get("taskId"):
            return str(data["taskId"])
        task_data = data.get("data")
        if isinstance(task_data, dict) and task_data.get("taskId"):
            return str(task_data["taskId"])
        return ""

    async def _background_polling(self, task_id: str):
        raw = await self.get_kv_data(f"duck_task_{task_id}", default=None)
        if not raw:
            logger.error(f"task info not found: {task_id}")
            return

        task_info = json.loads(raw)
        umo = task_info.get("umo")
        try:
            outputs = await self._poll_outputs(task_id)
            duck_url = self._pick_first_url(outputs)
            if not duck_url:
                raise RuntimeError(f"no output url in response: {self._brief_json(outputs)}")

            paths = await self._download_and_decode(task_id, duck_url)
            send_status = await self._send_result(umo, paths["decoded"], paths.get("duck"))
            task_info["status"] = "completed"
            task_info["send_status"] = send_status
            task_info["output_image_mode"] = self.output_image_mode
            task_info["duck_url"] = duck_url
            task_info["decoded_path"] = paths["decoded"]
            await self.put_kv_data(f"duck_task_{task_id}", json.dumps(task_info, ensure_ascii=False))
            self._clean_old_files()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"duck draw task failed: {traceback.format_exc()}")
            try:
                await self.context.send_message(
                    umo,
                    MessageChain().message(f"绘图或解码失败：{exc}"),
                )
            except Exception as send_exc:
                logger.warning(f"failed to send failure message: {send_exc}")

    async def _poll_outputs(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.timeout_seconds
        last_data: dict[str, Any] = {}

        for _ in range(self.max_retries):
            if time.time() > deadline:
                break

            try:
                data = await self._query_v2(task_id)
                last_data = data
                status = self._extract_status(data)
                if status == "SUCCESS":
                    return data
                if status in {"FAILED", "ERROR", "FAILURE"}:
                    raise RuntimeError(self._extract_error_message(data) or self._brief_json(data))
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning(f"v2 query failed, trying legacy outputs: {exc}")

            try:
                legacy = await self._query_legacy_outputs(task_id)
                if isinstance(legacy.get("data"), list) and legacy["data"]:
                    return legacy
                last_data = legacy
            except Exception as exc:
                logger.warning(f"legacy outputs query failed: {exc}")

            await asyncio.sleep(self.poll_interval)

        raise TimeoutError(f"RunningHub task timeout, last response: {self._brief_json(last_data)}")

    async def _query_v2(self, task_id: str) -> dict[str, Any]:
        assert self.session is not None
        url = f"{self.query_api_base}/openapi/v2/query"
        headers = self._headers(with_bearer=True)
        async with self.session.post(url, json={"taskId": task_id}, headers=headers) as resp:
            return await self._read_json_response(resp)

    async def _query_legacy_outputs(self, task_id: str) -> dict[str, Any]:
        assert self.session is not None
        url = f"{self.api_base}/task/openapi/outputs"
        payload = {"apiKey": self.api_key, "taskId": task_id}
        async with self.session.post(url, json=payload, headers=self._headers()) as resp:
            return await self._read_json_response(resp)

    def _extract_status(self, data: Any) -> str:
        if isinstance(data, dict):
            for key in ("status", "taskStatus"):
                if data.get(key) is not None:
                    return str(data[key]).upper()
            nested = data.get("data")
            if isinstance(nested, dict):
                return self._extract_status(nested)
        return ""

    def _extract_error_message(self, data: Any) -> str:
        if isinstance(data, dict):
            for key in ("errorMessage", "error", "msg", "message"):
                value = data.get(key)
                if value:
                    return str(value)
            nested = data.get("data")
            if isinstance(nested, dict):
                return self._extract_error_message(nested)
        return ""

    def _pick_first_url(self, outputs: Any) -> str:
        if isinstance(outputs, dict):
            for key in ("url", "fileUrl", "resultUrl", "imageUrl"):
                value = outputs.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
            for key in ("results", "outputs", "data", "files", "images"):
                value = outputs.get(key)
                url = self._pick_first_url(value)
                if url:
                    return url
        elif isinstance(outputs, list):
            for item in outputs:
                url = self._pick_first_url(item)
                if url:
                    return url
        return ""

    async def _download_and_decode(self, task_id: str, duck_url: str) -> dict[str, str]:
        root = self._data_dir()
        duck_dir = root / "duck"
        decoded_dir = root / "decoded"
        duck_dir.mkdir(parents=True, exist_ok=True)
        decoded_dir.mkdir(parents=True, exist_ok=True)

        duck_path = duck_dir / f"{task_id}.png"
        assert self.session is not None
        async with self.session.get(duck_url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise RuntimeError(f"download duck image failed HTTP {resp.status}: {body[:200]}")
            duck_path.write_bytes(await resp.read())

        try:
            decoded_path, _ = await asyncio.to_thread(
                decode_duck_image,
                duck_path,
                decoded_dir,
                self.duck_password,
                task_id,
            )
        except DuckDecodeError:
            raise
        except Exception as exc:
            raise DuckDecodeError(str(exc)) from exc

        return {"duck": str(duck_path), "decoded": decoded_path}

    async def _send_result(self, umo: str, decoded_path: str, duck_path: str | None = None) -> str:
        if self.output_image_mode == "duck":
            if not duck_path:
                raise RuntimeError("duck image output requested, but duck image path is missing")
            chain = (
                MessageChain()
                .message(f"画好了，这是鸭子图。可在 {DUCK_DECODER_URL} 解码查看原图。")
                .file_image(duck_path)
            )
        else:
            chain = MessageChain().message("画好了，已从鸭子图中解码出原图。").file_image(decoded_path)
            if self.send_duck_image and duck_path:
                chain = chain.message(f"\n鸭子图备份，可在 {DUCK_DECODER_URL} 解码：").file_image(duck_path)
        try:
            await self.context.send_message(umo, chain)
            return "sent"
        except Exception as exc:
            if self._is_platform_send_timeout(exc):
                logger.warning(
                    "image send returned platform timeout, but the message may already be delivered; "
                    f"treating task as completed: {exc}"
                )
                return "platform_timeout"
            raise

    def _is_platform_send_timeout(self, exc: Exception) -> bool:
        text = str(exc)
        return "retcode=1200" in text or "Timeout: NTEvent" in text

    def _data_dir(self) -> Path:
        return Path(get_astrbot_data_path()) / "plugin_data" / "draw_with_duck"

    def _clean_old_files(self):
        try:
            for sub in ("duck", "decoded"):
                directory = self._data_dir() / sub
                if not directory.exists():
                    continue
                files = sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files[self.keep_files :]:
                    if old.is_file():
                        old.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"clean old duck files failed: {exc}")

    def _brief_json(self, data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)[:500]
        except Exception:
            return str(data)[:500]
