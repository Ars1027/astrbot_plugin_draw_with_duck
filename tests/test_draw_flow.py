from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_draw_with_duck_test_package"


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _MessageChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])

    def message(self, text):
        self.chain.append(SimpleNamespace(text=text))
        return self

    def file_image(self, path):
        self.chain.append(SimpleNamespace(file=path))
        return self

    @property
    def plain_text(self):
        return "".join(getattr(item, "text", "") for item in self.chain)


class _Star:
    def __init__(self, context):
        self.context = context
        self._kv = {}

    async def put_kv_data(self, key, value):
        self._kv[key] = value

    async def get_kv_data(self, key, default=None):
        return self._kv.get(key, default)


def _identity_decorator(*args, **kwargs):
    return lambda target: target


def _install_import_stubs():
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientResponse = type("ClientResponse", (), {})
    aiohttp.ClientSession = type("ClientSession", (), {})
    aiohttp.ClientTimeout = lambda **kwargs: SimpleNamespace(**kwargs)
    sys.modules["aiohttp"] = aiohttp

    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    api.AstrBotConfig = dict
    api.logger = _Logger()

    event_api = types.ModuleType("astrbot.api.event")
    event_api.AstrMessageEvent = object
    event_api.MessageChain = _MessageChain
    event_api.filter = SimpleNamespace(command=_identity_decorator)

    star_api = types.ModuleType("astrbot.api.star")
    star_api.Context = object
    star_api.Star = _Star
    star_api.register = _identity_decorator

    core = types.ModuleType("astrbot.core")
    core.__path__ = []
    utils = types.ModuleType("astrbot.core.utils")
    utils.__path__ = []
    path_api = types.ModuleType("astrbot.core.utils.astrbot_path")
    path_api.get_astrbot_data_path = lambda: str(ROOT)

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event_api,
            "astrbot.api.star": star_api,
            "astrbot.core": core,
            "astrbot.core.utils": utils,
            "astrbot.core.utils.astrbot_path": path_api,
        }
    )

    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    decoder = types.ModuleType(f"{PACKAGE_NAME}.ss_tools_decoder")
    decoder.DuckDecodeError = type("DuckDecodeError", (Exception,), {})
    decoder.decode_duck_image = lambda *args, **kwargs: ("decoded.png", {})
    sys.modules[f"{PACKAGE_NAME}.ss_tools_decoder"] = decoder


_install_import_stubs()
plugin_module = importlib.import_module(f"{PACKAGE_NAME}.main")


class _Result:
    def __init__(self, text):
        self.text = text


class _PersistentStopEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.unified_msg_origin = "platform:friend:user"
        self._force_stopped = False

    def plain_result(self, text):
        return _Result(text)

    def stop_event(self):
        self._force_stopped = True

    def is_stopped(self):
        return self._force_stopped

    def get_sender_id(self):
        return "user"


class _Context:
    def __init__(self):
        self.sent = []

    async def llm_generate(self, **kwargs):
        return SimpleNamespace(completion_text="1girl, blue hair")

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))


def _make_plugin(**overrides):
    config = {
        "runninghub_api_key": "runninghub-key",
        "workflow_id": "workflow-id",
        "enhance_prompt": True,
        "prompt_output_style": "danbooru_tags",
    }
    config.update(overrides)
    context = _Context()
    return plugin_module.DrawWithDuckPlugin(context, config), context


class DrawFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_draw_continues_after_progress_and_stops_only_when_exhausted(self):
        plugin, _ = _make_plugin()
        event = _PersistentStopEvent("/画图 蓝发少女")
        plugin._enhance_prompt = AsyncMock(
            return_value=("1girl, blue_hair", plugin_module.PROMPT_ENHANCEMENT_SUCCESS)
        )
        plugin._submit_task = AsyncMock(return_value={"taskId": "task-123"})

        release_background = asyncio.Event()

        async def background_polling(task_id, umo):
            await release_background.wait()

        plugin._background_polling = background_polling
        generator = plugin.draw(event)

        progress = await anext(generator)
        self.assertIn("正在润色提示词", progress.text)
        self.assertFalse(event.is_stopped())
        plugin._enhance_prompt.assert_not_awaited()

        submitted = await anext(generator)
        self.assertIn("任务已提交：task-123", submitted.text)
        plugin._enhance_prompt.assert_awaited_once()
        plugin._submit_task.assert_awaited_once()
        self.assertEqual(len(plugin._tasks), 1)
        self.assertFalse(event.is_stopped())

        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        self.assertTrue(event.is_stopped())

        release_background.set()
        await asyncio.gather(*list(plugin._tasks), return_exceptions=True)
        await asyncio.sleep(0)

    async def test_usage_and_config_errors_stop_after_their_reply(self):
        plugin, _ = _make_plugin()
        usage_event = _PersistentStopEvent("/画图")
        usage_generator = plugin.draw(usage_event)

        usage = await anext(usage_generator)
        self.assertIn("用法", usage.text)
        self.assertFalse(usage_event.is_stopped())
        with self.assertRaises(StopAsyncIteration):
            await anext(usage_generator)
        self.assertTrue(usage_event.is_stopped())

        plugin.api_key = ""
        config_event = _PersistentStopEvent("/画图 蓝发少女")
        config_generator = plugin.draw(config_event)
        config_error = await anext(config_generator)
        self.assertIn("runninghub_api_key", config_error.text)
        with self.assertRaises(StopAsyncIteration):
            await anext(config_generator)
        self.assertTrue(config_event.is_stopped())

    async def test_closing_generator_runs_stop_finally(self):
        plugin, _ = _make_plugin()
        event = _PersistentStopEvent("/画图 蓝发少女")
        generator = plugin.draw(event)

        await anext(generator)
        self.assertFalse(event.is_stopped())
        await generator.aclose()
        self.assertTrue(event.is_stopped())

    async def test_draw_help_replies_before_stopping(self):
        plugin, _ = _make_plugin()
        event = _PersistentStopEvent("/画图帮助")
        generator = plugin.draw_help(event)

        help_result = await anext(generator)
        self.assertIn("鸭子图绘图插件", help_result.text)
        self.assertFalse(event.is_stopped())
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        self.assertTrue(event.is_stopped())

    async def test_submit_failure_replies_and_does_not_start_background_task(self):
        plugin, _ = _make_plugin()
        event = _PersistentStopEvent("/画图 蓝发少女")
        plugin._enhance_prompt = AsyncMock(
            return_value=("1girl, blue_hair", plugin_module.PROMPT_ENHANCEMENT_SUCCESS)
        )
        plugin._submit_task = AsyncMock(side_effect=RuntimeError("submit failed"))
        generator = plugin.draw(event)

        await anext(generator)
        failure = await anext(generator)
        self.assertIn("提交任务失败：submit failed", failure.text)
        self.assertFalse(plugin._tasks)
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        self.assertTrue(event.is_stopped())

    async def test_disabled_non_english_prompts_are_rejected_without_submission(self):
        for prompt in ("蓝发少女", "2B站在东京街道"):
            with self.subTest(prompt=prompt):
                plugin, _ = _make_plugin(enhance_prompt=False)
                plugin._enhance_prompt = AsyncMock()
                plugin._submit_task = AsyncMock()
                event = _PersistentStopEvent(f"/画图 {prompt}")
                generator = plugin.draw(event)

                rejection = await anext(generator)

                self.assertIn("输入包含非英文文字", rejection.text)
                self.assertIn("未提交 RunningHub 任务", rejection.text)
                self.assertIn("不会消耗本次绘图点数", rejection.text)
                plugin._enhance_prompt.assert_not_awaited()
                plugin._submit_task.assert_not_awaited()
                self.assertFalse(plugin._kv)
                self.assertFalse(plugin._tasks)
                self.assertFalse(event.is_stopped())
                with self.assertRaises(StopAsyncIteration):
                    await anext(generator)
                self.assertTrue(event.is_stopped())

    async def test_disabled_english_prompt_is_submitted_without_normalization(self):
        plugin, _ = _make_plugin(enhance_prompt=False)
        plugin._submit_task = AsyncMock(return_value={"taskId": "task-direct"})
        plugin._background_polling = AsyncMock()
        event = _PersistentStopEvent("/画图 2B in a rainy Tokyo street")
        generator = plugin.draw(event)

        progress = await anext(generator)
        submitted = await anext(generator)

        self.assertIn("提示词增强已关闭", progress.text)
        self.assertNotIn("正在润色", progress.text)
        self.assertIn("已直接使用英文原提示词", submitted.text)
        submitted_prompt = plugin._submit_task.await_args.args[0]
        self.assertIn("2B in a rainy Tokyo street", submitted_prompt)
        self.assertNotIn("2b_in_a_rainy_tokyo_street", submitted_prompt)
        stored = json.loads(plugin._kv["duck_task_task-direct"])
        self.assertEqual(stored["enhanced_prompt"], "2B in a rainy Tokyo street")
        self.assertEqual(
            stored["prompt_enhancement_status"],
            plugin_module.PROMPT_ENHANCEMENT_DISABLED,
        )
        self.assertNotIn("prompt_delivery_mode", stored)
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        await asyncio.gather(*list(plugin._tasks), return_exceptions=True)

    async def test_disabled_english_prompt_keeps_r18_routing(self):
        plugin, _ = _make_plugin(
            enhance_prompt=False,
            r18_review_enabled=True,
            r18_api_key="r18-key",
            r18_workflow_id="r18-workflow",
        )
        plugin._submit_task = AsyncMock(return_value={"taskId": "task-r18"})
        plugin._background_polling = AsyncMock()
        event = _PersistentStopEvent("/画图 1girl, explicit, bedroom")
        generator = plugin.draw(event)

        await anext(generator)
        await anext(generator)

        route = plugin._submit_task.await_args.args[1]
        self.assertEqual(route["mode"], plugin_module.R18_ROUTE_R18)
        self.assertEqual(route["workflow_id"], "r18-workflow")
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        await asyncio.gather(*list(plugin._tasks), return_exceptions=True)

    async def test_non_english_fallback_statuses_abort_before_runninghub(self):
        statuses = (
            plugin_module.PROMPT_ENHANCEMENT_TIMEOUT,
            plugin_module.PROMPT_ENHANCEMENT_NO_PROVIDER,
            plugin_module.PROMPT_ENHANCEMENT_FAILED,
            plugin_module.PROMPT_ENHANCEMENT_INVALID,
        )
        for status in statuses:
            with self.subTest(status=status):
                plugin, _ = _make_plugin()
                plugin._enhance_prompt = AsyncMock(return_value=("蓝发少女", status))
                plugin._submit_task = AsyncMock()
                event = _PersistentStopEvent("/画图 蓝发少女")
                generator = plugin.draw(event)

                progress = await anext(generator)
                rejection = await anext(generator)

                self.assertIn("正在润色提示词", progress.text)
                self.assertIn("未提交 RunningHub 任务", rejection.text)
                plugin._submit_task.assert_not_awaited()
                self.assertFalse(plugin._kv)
                self.assertFalse(plugin._tasks)
                with self.assertRaises(StopAsyncIteration):
                    await anext(generator)
                self.assertTrue(event.is_stopped())

    async def test_final_prompt_is_revalidated_before_runninghub(self):
        plugin, _ = _make_plugin()
        plugin._enhance_prompt = AsyncMock(
            return_value=("1girl, blue hair", plugin_module.PROMPT_ENHANCEMENT_SUCCESS)
        )
        plugin._build_final_prompt = MagicMock(return_value="masterpiece, 蓝发少女")
        plugin._submit_task = AsyncMock()
        event = _PersistentStopEvent("/画图 蓝发少女")
        generator = plugin.draw(event)

        await anext(generator)
        rejection = await anext(generator)

        self.assertIn("最终正向提示词包含非英文文字", rejection.text)
        self.assertIn("未提交 RunningHub 任务", rejection.text)
        plugin._submit_task.assert_not_awaited()
        self.assertFalse(plugin._kv)
        self.assertFalse(plugin._tasks)
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)

    async def test_prompt_timeout_uses_one_budget_and_falls_back(self):
        plugin, context = _make_plugin()
        event = _PersistentStopEvent("/画图 blue hair")
        plugin.prompt_timeout_seconds = 0.02
        plugin._get_prompt_provider_id = AsyncMock(return_value="provider-id")
        plugin._build_prompt_system_prompt = lambda: "system"
        plugin._build_prompt_user_prompt = lambda prompt: prompt

        async def never_returns(**kwargs):
            await asyncio.Event().wait()

        context.llm_generate = never_returns
        started_at = asyncio.get_running_loop().time()
        prompt, status = await plugin._enhance_prompt(
            event, "2B in a rainy Tokyo street"
        )
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertEqual(status, plugin_module.PROMPT_ENHANCEMENT_TIMEOUT)
        self.assertEqual(prompt, "2B in a rainy Tokyo street")
        self.assertLess(elapsed, 0.5)

    async def test_prompt_success_returns_enhanced_status(self):
        plugin, _ = _make_plugin()
        event = _PersistentStopEvent("/画图 blue hair")
        plugin._get_prompt_provider_id = AsyncMock(return_value="provider-id")
        plugin._build_prompt_system_prompt = lambda: "system"
        plugin._build_prompt_user_prompt = lambda prompt: prompt

        prompt, status = await plugin._enhance_prompt(event, "blue hair")

        self.assertEqual(status, plugin_module.PROMPT_ENHANCEMENT_SUCCESS)
        self.assertIn("blue_hair", prompt)

    async def test_prompt_fast_failures_retry_twice_then_fall_back(self):
        plugin, context = _make_plugin()
        event = _PersistentStopEvent("/画图 blue hair")
        plugin.prompt_timeout_seconds = 2
        plugin._get_prompt_provider_id = AsyncMock(return_value="provider-id")
        plugin._build_prompt_system_prompt = lambda: "system"
        plugin._build_prompt_user_prompt = lambda prompt: prompt
        context.llm_generate = AsyncMock(side_effect=RuntimeError("provider down"))

        with patch.object(plugin_module.asyncio, "sleep", new=AsyncMock()):
            _, status = await plugin._enhance_prompt(event, "blue hair")

        self.assertEqual(status, plugin_module.PROMPT_ENHANCEMENT_FAILED)
        self.assertEqual(context.llm_generate.await_count, 2)

    async def test_non_english_llm_outputs_retry_then_fall_back_as_invalid(self):
        plugin, context = _make_plugin()
        event = _PersistentStopEvent("/画图 蓝发少女")
        plugin._get_prompt_provider_id = AsyncMock(return_value="provider-id")
        plugin._build_prompt_system_prompt = lambda: "system"
        plugin._build_prompt_user_prompt = lambda prompt: prompt
        context.llm_generate = AsyncMock(
            return_value=SimpleNamespace(completion_text="蓝发少女，东京街道")
        )

        with patch.object(plugin_module.asyncio, "sleep", new=AsyncMock()):
            prompt, status = await plugin._enhance_prompt(event, "蓝发少女")

        self.assertEqual(prompt, "蓝发少女")
        self.assertEqual(status, plugin_module.PROMPT_ENHANCEMENT_INVALID)
        self.assertEqual(context.llm_generate.await_count, 2)

    async def test_non_english_llm_output_can_recover_on_retry(self):
        plugin, context = _make_plugin()
        event = _PersistentStopEvent("/画图 蓝发少女")
        plugin._get_prompt_provider_id = AsyncMock(return_value="provider-id")
        plugin._build_prompt_system_prompt = lambda: "system"
        plugin._build_prompt_user_prompt = lambda prompt: prompt
        context.llm_generate = AsyncMock(
            side_effect=[
                SimpleNamespace(completion_text="蓝发少女"),
                SimpleNamespace(completion_text="1girl, blue hair, Tokyo street"),
            ]
        )

        with patch.object(plugin_module.asyncio, "sleep", new=AsyncMock()):
            prompt, status = await plugin._enhance_prompt(event, "蓝发少女")

        self.assertEqual(status, plugin_module.PROMPT_ENHANCEMENT_SUCCESS)
        self.assertIn("blue_hair", prompt)
        self.assertEqual(context.llm_generate.await_count, 2)

    async def test_llm_output_unusable_after_formatting_uses_raw_fallback(self):
        plugin, context = _make_plugin(prompt_output_style="skill_mixed")
        event = _PersistentStopEvent("/画图 Blue-haired heroine")
        plugin._get_prompt_provider_id = AsyncMock(return_value="provider-id")
        plugin._build_prompt_system_prompt = lambda: "system"
        plugin._build_prompt_user_prompt = lambda prompt: prompt
        context.llm_generate = AsyncMock(
            return_value=SimpleNamespace(completion_text="Prompt: !!!")
        )

        with patch.object(plugin_module.asyncio, "sleep", new=AsyncMock()):
            prompt, status = await plugin._enhance_prompt(
                event, "Blue-haired heroine"
            )

        self.assertEqual(prompt, "Blue-haired heroine")
        self.assertEqual(status, plugin_module.PROMPT_ENHANCEMENT_INVALID)
        self.assertEqual(context.llm_generate.await_count, 2)

    def test_english_compatibility_rejects_non_ascii_letter_scripts(self):
        plugin, _ = _make_plugin()

        self.assertEqual(
            plugin._clean_raw_prompt("  Blue  hair\r\n  Tokyo  "),
            "Blue  hair Tokyo",
        )
        self.assertTrue(plugin._is_english_compatible_prompt("2B, rain, Tokyo"))
        self.assertTrue(plugin._is_english_compatible_prompt("1girl, smile ✨"))
        self.assertFalse(plugin._is_english_compatible_prompt("蓝发少女"))
        self.assertFalse(plugin._is_english_compatible_prompt("2B站在东京街道"))
        self.assertFalse(plugin._is_english_compatible_prompt("café portrait"))
        self.assertFalse(plugin._is_english_compatible_prompt("K portrait"))
        self.assertFalse(plugin._is_english_compatible_prompt("123, !!! ✨"))

    def test_prompt_template_must_preserve_prompt_and_use_english(self):
        missing_placeholder, _ = _make_plugin(prompt_template="masterpiece")
        invalid_format, _ = _make_plugin(prompt_template="masterpiece, {prompt")
        non_english, _ = _make_plugin(prompt_template="杰作, {prompt}")

        self.assertIn("必须包含 {prompt}", missing_placeholder._check_config())
        self.assertIn("格式无效", invalid_format._check_config())
        self.assertIn("只能包含英文", non_english._check_config())

    async def test_timeout_fallback_status_is_saved_and_reported(self):
        plugin, _ = _make_plugin()
        event = _PersistentStopEvent("/画图 blue hair")
        plugin._enhance_prompt = AsyncMock(
            return_value=("blue_hair", plugin_module.PROMPT_ENHANCEMENT_TIMEOUT)
        )
        plugin._submit_task = AsyncMock(return_value={"taskId": "task-timeout"})
        plugin._background_polling = AsyncMock()
        generator = plugin.draw(event)

        await anext(generator)
        submitted = await anext(generator)

        self.assertIn("提示词润色超时", submitted.text)
        stored = json.loads(plugin._kv["duck_task_task-timeout"])
        self.assertEqual(
            stored["prompt_enhancement_status"],
            plugin_module.PROMPT_ENHANCEMENT_TIMEOUT,
        )
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        await asyncio.gather(*list(plugin._tasks), return_exceptions=True)

    async def test_english_fallback_statuses_still_submit_raw_prompt(self):
        statuses = (
            plugin_module.PROMPT_ENHANCEMENT_NO_PROVIDER,
            plugin_module.PROMPT_ENHANCEMENT_FAILED,
            plugin_module.PROMPT_ENHANCEMENT_INVALID,
        )
        for status in statuses:
            with self.subTest(status=status):
                plugin, _ = _make_plugin()
                plugin._enhance_prompt = AsyncMock(
                    return_value=("Blue-haired heroine in Tokyo", status)
                )
                plugin._submit_task = AsyncMock(
                    return_value={"taskId": f"task-{status}"}
                )
                plugin._background_polling = AsyncMock()
                event = _PersistentStopEvent(
                    "/画图 Blue-haired heroine in Tokyo"
                )
                generator = plugin.draw(event)

                await anext(generator)
                submitted = await anext(generator)

                self.assertIn("英文原提示词", submitted.text)
                sent_prompt = plugin._submit_task.await_args.args[0]
                self.assertIn("Blue-haired heroine in Tokyo", sent_prompt)
                with self.assertRaises(StopAsyncIteration):
                    await anext(generator)
                await asyncio.gather(*list(plugin._tasks), return_exceptions=True)

    async def test_background_preflight_failures_are_persisted_and_reported(self):
        cases = {
            "missing": AsyncMock(return_value=None),
            "read_error": AsyncMock(side_effect=RuntimeError("kv unavailable")),
            "invalid_json": AsyncMock(return_value="{invalid json"),
            "non_object": AsyncMock(return_value="[]"),
            "route_error": AsyncMock(
                return_value=json.dumps(
                    {
                        "task_id": "task-bad",
                        "umo": "wrong:friend:user",
                        "route_mode": "r18",
                    }
                )
            ),
        }

        for name, get_kv_mock in cases.items():
            with self.subTest(name=name):
                plugin, context = _make_plugin()
                plugin.get_kv_data = get_kv_mock

                await plugin._background_polling("task-bad", "direct:friend:user")

                persisted = json.loads(plugin._kv["duck_task_task-bad"])
                self.assertEqual(persisted["task_id"], "task-bad")
                self.assertEqual(persisted["status"], "failed")
                self.assertEqual(persisted["umo"], "direct:friend:user")
                self.assertEqual(len(context.sent), 1)
                self.assertEqual(context.sent[0][0], "direct:friend:user")
                self.assertIn("绘图或解码失败", context.sent[0][1].plain_text)

    async def test_background_failure_reporting_is_best_effort(self):
        plugin, context = _make_plugin()
        plugin.get_kv_data = AsyncMock(return_value=None)
        plugin.put_kv_data = AsyncMock(side_effect=RuntimeError("write failed"))
        context.send_message = AsyncMock(side_effect=RuntimeError("send failed"))

        await plugin._background_polling("task-bad", "direct:friend:user")

        plugin.put_kv_data.assert_awaited_once()
        context.send_message.assert_awaited_once()

    async def test_background_success_persists_completed_status(self):
        plugin, _ = _make_plugin()
        task_info = {
            "task_id": "task-ok",
            "umo": "direct:friend:user",
            "route_mode": "normal",
            "workflow_id": "workflow-id",
        }
        plugin._kv["duck_task_task-ok"] = json.dumps(task_info)
        plugin._poll_outputs = AsyncMock(
            return_value={"status": "SUCCESS", "url": "https://example.com/duck.png"}
        )
        plugin._download_and_decode = AsyncMock(
            return_value={"decoded": "decoded.png", "duck": "duck.png"}
        )
        plugin._send_result = AsyncMock(return_value="sent")
        plugin._clean_old_files = MagicMock()

        await plugin._background_polling("task-ok", "direct:friend:user")

        persisted = json.loads(plugin._kv["duck_task_task-ok"])
        self.assertEqual(persisted["status"], "completed")
        self.assertEqual(persisted["send_status"], "sent")
        plugin._poll_outputs.assert_awaited_once()
        plugin._download_and_decode.assert_awaited_once()
        plugin._send_result.assert_awaited_once_with(
            "direct:friend:user", "decoded.png", "duck.png"
        )
        plugin._clean_old_files.assert_called_once()

    async def test_done_callback_consumes_unknown_task_exception(self):
        plugin, _ = _make_plugin()

        async def fail_unexpectedly():
            raise RuntimeError("unexpected")

        task = asyncio.create_task(fail_unexpectedly())
        plugin._tasks.add(task)
        task.add_done_callback(plugin._on_background_task_done)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertTrue(task.done())
        self.assertNotIn(task, plugin._tasks)

    def test_legacy_workflow_settings_are_ignored_for_fixed_nodes(self):
        legacy_config = {
            "prompt_delivery_mode": "workflow_input",
            "prompt_node_id": "93",
            "prompt_field_name": "custom_text",
            "negative_node_id": "98",
            "duck_password_node_id": "99",
            "negative_prompt": "bad anatomy",
            "duck_password": "duck-secret",
        }
        with patch.object(plugin_module.logger, "warning") as warning:
            plugin, _ = _make_plugin(**legacy_config)

        self.assertEqual(warning.call_count, 1)
        warning_text = warning.call_args.args[0]
        for key in plugin_module.LEGACY_WORKFLOW_CONFIG_KEYS:
            self.assertIn(key, warning_text)
        self.assertNotIn("custom_text", warning_text)
        self.assertNotIn("duck-secret", warning_text)
        self.assertEqual(
            plugin._build_node_info_list("1girl, blue hair"),
            [
                {
                    "nodeId": "11",
                    "fieldName": "text",
                    "fieldValue": "1girl, blue hair",
                },
                {
                    "nodeId": "12",
                    "fieldName": "text",
                    "fieldValue": "bad anatomy",
                },
                {
                    "nodeId": "100",
                    "fieldName": "password",
                    "fieldValue": "duck-secret",
                },
            ],
        )
        self.assertFalse(hasattr(plugin, "prompt_delivery_mode"))
        self.assertFalse(hasattr(plugin, "prompt_node_id"))

    def test_empty_optional_overrides_only_write_positive_prompt(self):
        plugin, _ = _make_plugin()

        self.assertEqual(
            plugin._build_node_info_list("1girl, blue hair"),
            [
                {
                    "nodeId": "11",
                    "fieldName": "text",
                    "fieldValue": "1girl, blue hair",
                }
            ],
        )

    async def test_config_and_version_contract(self):
        default_plugin, _ = _make_plugin()
        clamped_plugin, _ = _make_plugin(prompt_timeout_seconds=1)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        source = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertEqual(default_plugin.prompt_timeout_seconds, 120)
        self.assertEqual(clamped_plugin.prompt_timeout_seconds, 10)
        self.assertEqual(schema["prompt_timeout_seconds"]["default"], 120)
        for key in plugin_module.LEGACY_WORKFLOW_CONFIG_KEYS:
            self.assertNotIn(key, schema)
        self.assertIn("version: v1.2.2", metadata)
        self.assertIn('    "v1.2.2",', source)


if __name__ == "__main__":
    unittest.main()
