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
        prompt, status = await plugin._enhance_prompt(event, "blue hair")
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertEqual(status, plugin_module.PROMPT_ENHANCEMENT_TIMEOUT)
        self.assertTrue(prompt)
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

    async def test_config_and_version_contract(self):
        default_plugin, _ = _make_plugin()
        clamped_plugin, _ = _make_plugin(prompt_timeout_seconds=1)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        source = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertEqual(default_plugin.prompt_timeout_seconds, 120)
        self.assertEqual(clamped_plugin.prompt_timeout_seconds, 10)
        self.assertEqual(schema["prompt_timeout_seconds"]["default"], 120)
        self.assertIn("version: v1.2.1", metadata)
        self.assertIn('    "v1.2.1",', source)


if __name__ == "__main__":
    unittest.main()
