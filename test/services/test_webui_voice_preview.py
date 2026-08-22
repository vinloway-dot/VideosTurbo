import ast
import hashlib
import json
import re
import shutil
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.config import config
from app.models.schema import VideoParams
from app.services import task as tm
from app.services import llm, six_clip_plan
from app.services import voice
from app.services import webui_task
from app.utils import utils


ROOT_DIR = Path(__file__).parent.parent.parent
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"


def _load_duration_estimator():
    """只加载纯估算函数，避免单元测试导入并执行完整 Streamlit 页面。"""
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_estimate_voiceover_duration_range"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"re": re}
    exec(compile(module, str(WEBUI_MAIN), "exec"), namespace)
    return namespace["_estimate_voiceover_duration_range"]


def _load_provider_signature(test_config):
    """加载凭证摘要和 Provider 指纹函数，独立验证缓存失效规则。"""
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "_credential_signature",
            "_get_voice_preview_provider_signature",
        }
    ]
    module = ast.Module(body=functions, type_ignores=[])
    namespace = {"hashlib": hashlib, "config": test_config}
    exec(compile(module, str(WEBUI_MAIN), "exec"), namespace)
    return namespace["_get_voice_preview_provider_signature"]


def _button_by_key(app, key):
    return next(
        button
        for button in app.button
        if str(getattr(button, "key", "")).startswith(key)
    )


def _timeline_response(duration: float) -> str:
    ranges = six_clip_plan.build_timeline_ranges(duration)
    return json.dumps(
        {
            "clips": [
                {
                    "index": index,
                    "start_sec": start,
                    "end_sec": end,
                    "title": f"Clip {index}",
                    "narration_context": f"Narration {index}",
                    "video_prompt": f"Prompt {index}",
                }
                for index, (start, end) in enumerate(ranges, start=1)
            ]
        }
    )


def test_generate_script_is_text_only_and_never_calls_tts_or_timeline_ai():
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )
    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(llm, "generate_script", return_value="Generated narration."),
        patch.object(llm, "generate_terms", return_value=["term one"]),
        patch.object(voice, "tts") as synthesize,
        patch.object(six_clip_plan, "generate_six_clip_plan") as analyze,
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "en"
        app.session_state["video_subject"] = "A useful subject"
        app.run()
        _button_by_key(app, "auto_generate_script").click().run()

    assert app.session_state["video_script"] == "Generated narration."
    synthesize.assert_not_called()
    analyze.assert_not_called()
    assert [str(item.value) for item in app.exception] == []


def test_confirm_builds_dynamic_timeline_once_and_reuses_canonical_audio():
    script = "Narration long enough to produce a measured 63-second voiceover."
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="en-US-JennyNeural-Female",
        voice_volume=1.5,
    )
    sub_maker = SimpleNamespace(
        subs=["Opening", "Ending"],
        offset=[(0, 100_000_000), (600_000_000, 630_000_000)],
    )

    def fake_tts(**kwargs):
        Path(kwargs["voice_file"]).write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
        )
        return sub_maker

    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(voice, "tts", side_effect=fake_tts) as synthesize,
        patch.object(voice, "get_audio_duration", return_value=63.0),
        patch.object(llm, "_generate_response", return_value=_timeline_response(63.0)),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "en"
        app.session_state["video_script"] = script
        app.session_state["voice_volume_select"] = 1.5
        app.run()

        _button_by_key(app, "confirm_script_build_timeline_button").click().run()
        _button_by_key(app, "confirm_script_build_timeline_button").click().run()

        app.session_state["video_script"] = "Edited narration makes the plan stale."
        app.run()

    plan = app.session_state["six_clip_plan"]
    assert plan["narration_duration_sec"] == 63.0
    assert plan["timeline_duration_sec"] == 63.0
    assert len(plan["segments"]) == 7
    synthesize.assert_called_once()
    assert synthesize.call_args.kwargs["voice_volume"] == 1.0
    assert any(
        "Timeline is stale" in str(item.value) for item in app.warning
    )
    assert [str(item.value) for item in app.exception] == []


def test_short_sample_does_not_replace_confirmed_full_narration_cache():
    script = "Confirmed narration must survive a later short voice sample."
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="en-US-JennyNeural-Female",
    )
    sub_maker = SimpleNamespace(
        subs=[script],
        offset=[(0, 630_000_000)],
    )

    def fake_tts(**kwargs):
        Path(kwargs["voice_file"]).write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
        )
        return sub_maker

    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(voice, "tts", side_effect=fake_tts) as synthesize,
        patch.object(voice, "get_audio_duration", return_value=63.0),
        patch.object(llm, "_generate_response", return_value=_timeline_response(63.0)),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "en"
        app.session_state["video_script"] = script
        app.run()

        _button_by_key(app, "confirm_script_build_timeline_button").click().run()
        confirmed_fingerprint = app.session_state["voice_preview_audio"][
            "fingerprint"
        ]
        _button_by_key(app, "play_voice_button").click().run()
        _button_by_key(app, "confirm_script_build_timeline_button").click().run()

    assert synthesize.call_count == 2
    assert (
        app.session_state["voice_preview_audio"]["fingerprint"]
        == confirmed_fingerprint
    )


def test_duration_estimator_is_local_and_respects_voice_rate():
    """本地估算应覆盖中英文，并随用户选择的语速合理缩短。"""
    estimate = _load_duration_estimator()
    script = "人工智能正在改变日常生活。它可以帮助我们整理信息，也能提高效率。"

    normal_range = estimate(script, 1.0)
    fast_range = estimate(script, 2.0)

    assert normal_range is not None
    assert fast_range is not None
    assert normal_range[0] < normal_range[1]
    assert fast_range[0] < normal_range[0]
    assert estimate("", 1.0) is None
    assert estimate("AI tools can simplify repetitive work.", 1.0) is not None


def test_provider_signature_changes_when_api_key_changes():
    """只修改 API Key 也必须让试听缓存失效，不能伪装成新凭证验证成功。"""
    test_config = SimpleNamespace(
        app={"gemini_api_key": "old-gemini", "mimo_api_key": "old-mimo"},
        azure={"speech_region": "eastasia", "speech_key": "old-azure"},
        siliconflow={"api_key": "old-siliconflow"},
        elevenlabs={"api_key": "old-elevenlabs", "model_id": "eleven_v3"},
        chatterbox={
            "api_key": "old-chatterbox",
            "base_url": "http://127.0.0.1:4123/v1",
            "model_id": "chatterbox",
        },
    )
    provider_signature = _load_provider_signature(test_config)

    old_signature = provider_signature("elevenlabs")
    test_config.elevenlabs["api_key"] = "new-elevenlabs"
    new_signature = provider_signature("elevenlabs")

    assert old_signature != new_signature
    assert "old-elevenlabs" not in str(old_signature)
    assert "new-elevenlabs" not in str(new_signature)


def test_full_voiceover_preview_is_disabled_until_script_exists():
    """完整预览必须由用户主动触发，文案为空时不能误调用商业 TTS。"""
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )
    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "zh"
        app.run()

    full_preview = _button_by_key(
        app,
        "confirm_script_build_timeline_button",
    )
    assert full_preview.disabled
    assert any("填写视频文案后" in item.value for item in app.caption)


def test_script_shows_estimate_and_enables_full_voiceover_preview():
    """填写文案后展示免费估算，并明确完整预览可能产生 API 成本。"""
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )
    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "zh"
        app.session_state["video_script"] = (
            "人工智能正在改变日常生活。合理使用工具，可以帮助我们提高工作效率。"
        )
        app.run()

    full_preview = _button_by_key(
        app,
        "confirm_script_build_timeline_button",
    )
    assert not full_preview.disabled
    assert any("本地估算，不调用 API" in item.value for item in app.caption)
    assert "可能消耗 API 额度" in full_preview.help
    assert [str(item.value) for item in app.exception] == []


def test_short_preview_autoplays_only_after_explicit_click_and_reuses_cache():
    """短试听应立即播放；普通 rerun 不重播，重复点击也不重复调用 TTS。"""
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )

    def fake_tts(**kwargs):
        Path(kwargs["voice_file"]).write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
        )
        return object()

    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(voice, "tts", side_effect=fake_tts) as synthesize,
        patch.object(voice, "get_audio_duration", return_value=3.0),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "zh"
        app.run()

        _button_by_key(app, "play_voice_button").click().run()
        assert len(app.get("audio")) == 1
        assert app.get("audio")[0].proto.autoplay

        app.run()
        assert len(app.get("audio")) == 1
        assert not app.get("audio")[0].proto.autoplay

        _button_by_key(app, "play_voice_button").click().run()

    synthesize.assert_called_once()
    assert app.get("audio")[0].proto.autoplay
    assert [str(item.value) for item in app.exception] == []


def test_full_preview_uses_script_and_reuses_identical_cached_audio():
    """完整试听使用当前文案，相同参数重复点击时不得再次调用 TTS。"""
    script = "这是一段用于验证完整配音预览缓存的测试文案。"
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )

    def fake_tts(**kwargs):
        # 文件扩展名虽然是 mp3，但真实 TTS 可能返回 WAV；这个最小文件头同时
        # 验证 WebUI 会按内容识别播放器 MIME，而不是盲信扩展名。
        Path(kwargs["voice_file"]).write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
        )
        return object()

    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(voice, "tts", side_effect=fake_tts) as synthesize,
        patch.object(voice, "get_audio_duration", return_value=12.3),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "zh"
        app.session_state["video_script"] = script
        app.run()

        _button_by_key(
            app,
            "confirm_script_build_timeline_button",
        ).click().run()
        _button_by_key(
            app,
            "confirm_script_build_timeline_button",
        ).click().run()

    synthesize.assert_called_once()
    assert synthesize.call_args.kwargs["text"] == script
    assert len(app.get("audio")) == 1
    assert not app.get("audio")[0].proto.autoplay
    assert any("实际配音时长：12.3 秒" in item.value for item in app.caption)
    assert [str(item.value) for item in app.exception] == []


def test_full_preview_reports_when_tts_returns_no_audio():
    """TTS 返回空结果时必须给出可操作提示，不能让按钮点击后无任何反馈。"""
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )
    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(voice, "tts", return_value=None),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "zh"
        app.session_state["video_script"] = "验证配音服务空响应。"
        app.run()
        _button_by_key(
            app,
            "confirm_script_build_timeline_button",
        ).click().run()

    assert [item.value for item in app.error] == [
        "配音服务未返回试听音频，请检查相关设置和应用日志。"
    ]
    assert [str(item.value) for item in app.exception] == []


def test_full_preview_returns_immediately_when_runtime_config_is_busy():
    """后台任务持有配置锁时，试听应提示稍后重试而不是阻塞页面。"""
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )
    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(
            config,
            "try_runtime_config_lock",
            return_value=nullcontext(False),
        ),
        patch.object(voice, "tts") as synthesize,
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "zh"
        app.session_state["video_script"] = "验证忙碌状态不会阻塞页面。"
        app.run()
        _button_by_key(
            app,
            "confirm_script_build_timeline_button",
        ).click().run()

    synthesize.assert_not_called()
    warning_messages = [item.value for item in app.warning]
    assert "当前有视频任务正在使用配音配置，请稍后重试。" in warning_messages


def test_full_preview_warns_when_audio_duration_is_unavailable():
    """音频可播放但无法解码时长时，不能把 0.0 秒展示为真实结果。"""
    test_ui = dict(
        config.ui,
        voice_mode="tts",
        tts_server="azure-tts-v1",
        voice_name="zh-CN-XiaoxiaoNeural-Female",
    )

    def fake_tts(**kwargs):
        Path(kwargs["voice_file"]).write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
        )
        return object()

    with (
        patch.object(config, "ui", test_ui),
        patch.object(config, "save_config"),
        patch.object(voice, "tts", side_effect=fake_tts),
        patch.object(voice, "get_audio_duration", return_value=0),
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
        app.session_state["ui_language"] = "zh"
        app.session_state["video_script"] = "验证无法读取试听音频时长的提示。"
        app.run()
        _button_by_key(
            app,
            "confirm_script_build_timeline_button",
        ).click().run()

    assert len(app.get("audio")) == 1
    warning_messages = [item.value for item in app.warning]
    assert "试听音频已生成，但无法读取准确时长，请检查应用日志。" in warning_messages


def test_task_reuses_matching_full_preview_without_calling_tts():
    """参数完全一致时，正式任务应复用试听音频和字幕时间轴。"""
    task_id = "reuse-full-voice-preview"
    task_dir = Path(utils.task_dir(task_id))
    audio_file = task_dir / "audio.mp3"
    audio_file.write_bytes(b"preview audio")
    sub_maker = object()
    script = "完整试听和正式任务使用同一段文案。"
    params = VideoParams(
        video_subject="preview reuse",
        video_script=script,
        voice_name="zh-CN-XiaoxiaoNeural-Female",
        voice_rate=1.2,
        voice_volume=1.0,
    )
    preview = {
        "audio_file": str(audio_file),
        "duration": 8.2,
        "sub_maker": sub_maker,
        "script": script,
        "voice_name": params.voice_name,
        "voice_rate": params.voice_rate,
        "voice_volume": params.voice_volume,
    }

    try:
        with patch.object(tm.voice, "tts") as synthesize:
            result = tm.generate_audio(
                task_id,
                params,
                script,
                voice_preview=preview,
            )
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

    assert result == (str(audio_file.resolve()), 9, sub_maker)
    synthesize.assert_not_called()


def test_task_regenerates_audio_when_preview_parameters_changed():
    """文案或配音参数变化后必须回退 TTS，不能复用已经过期的完整试听。"""
    task_id = "stale-full-voice-preview"
    task_dir = Path(utils.task_dir(task_id))
    audio_file = task_dir / "audio.mp3"
    audio_file.write_bytes(b"stale preview audio")
    script = "正式任务需要使用新的语速重新生成配音。"
    params = VideoParams(
        video_subject="stale preview",
        video_script=script,
        voice_name="zh-CN-XiaoxiaoNeural-Female",
        voice_rate=1.5,
        voice_volume=1.0,
    )
    preview = {
        "audio_file": str(audio_file),
        "duration": 8.2,
        "sub_maker": object(),
        "script": script,
        "voice_name": params.voice_name,
        "voice_rate": 1.0,
        "voice_volume": params.voice_volume,
    }
    regenerated_sub_maker = object()

    try:
        with (
            patch.object(
                tm.voice,
                "tts",
                return_value=regenerated_sub_maker,
            ) as synthesize,
            patch.object(tm.voice, "get_audio_duration", return_value=6),
        ):
            result = tm.generate_audio(
                task_id,
                params,
                script,
                voice_preview=preview,
            )
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

    assert result[1:] == (6, regenerated_sub_maker)
    synthesize.assert_called_once()
    assert synthesize.call_args.kwargs["voice_rate"] == 1.5


def test_non_default_volume_reuses_canonical_audio_without_double_gain():
    """规范化完整配音可复用，音量仅由最终视频合成阶段应用一次。"""
    task_id = "voice-volume-forwarding"
    task_dir = Path(utils.task_dir(task_id))
    audio_file = task_dir / "audio.mp3"
    audio_file.write_bytes(b"canonical preview audio")
    script = "非默认音量复用规范化配音。"
    params = VideoParams(
        video_subject="voice volume",
        video_script=script,
        voice_name="zh-CN-XiaoxiaoNeural-Female",
        voice_rate=1.2,
        voice_volume=1.5,
    )
    sub_maker = object()
    preview = {
        "audio_file": str(audio_file),
        "duration": 5.0,
        "sub_maker": sub_maker,
        "script": script,
        "voice_name": params.voice_name,
        "voice_rate": params.voice_rate,
        "voice_volume": params.voice_volume,
    }

    try:
        with (
            patch.object(tm.voice, "tts") as synthesize,
        ):
            result = tm.generate_audio(
                task_id,
                params,
                script,
                voice_preview=preview,
            )
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

    assert result == (str(audio_file.resolve()), 5, sub_maker)
    synthesize.assert_not_called()


def test_webui_worker_forwards_voice_preview_to_pipeline():
    """后台任务包装层不能丢失提交时已经校验过的试听缓存。"""
    preview = {"audio_file": "audio.mp3", "duration": 5.0}
    with (
        patch.object(webui_task.tm, "start", return_value={"videos": []}) as start,
        patch.object(
            webui_task.config,
            "runtime_config_lock",
            return_value=nullcontext(),
        ),
    ):
        webui_task._run_generation(
            "preview-forwarding",
            VideoParams(video_subject="preview forwarding"),
            capture_logs=False,
            voice_preview=preview,
        )

    assert start.call_args.kwargs["voice_preview"] == preview
