from pathlib import Path


path = Path("webui/Main.py")
source = path.read_text(encoding="utf-8")
old = '''    st.session_state["custom_system_prompt"] = (\n        params.get("custom_system_prompt") or llm.DEFAULT_SCRIPT_SYSTEM_PROMPT\n    )\n\n    # 视频设置。素材上传控件不能由服务端写入，因此本地素材需要用户重新选择。\n'''
new = '''    st.session_state["custom_system_prompt"] = (\n        params.get("custom_system_prompt") or llm.DEFAULT_SCRIPT_SYSTEM_PROMPT\n    )\n    st.session_state["target_words_input"] = int(\n        params.get("target_words") or 130\n    )\n\n    restored_six_clip_plan = six_clip_timeline.restore_plan_from_task_params(params)\n    if restored_six_clip_plan is not None:\n        six_clip_timeline.set_session_plan(\n            restored_six_clip_plan,\n            sync_widgets=True,\n        )\n    _set_stable_widget_value(\n        "six_clip_video_aspect_select",\n        params.get("video_aspect") or VideoAspect.portrait.value,\n    )\n    _set_stable_widget_value(\n        "six_clip_image_motion_select",\n        params.get("image_motion") or "random",\n    )\n\n    # 视频设置。素材上传控件不能由服务端写入，因此本地素材需要用户重新选择。\n'''

if new in source:
    raise SystemExit(0)
count = source.count(old)
if count != 1:
    raise SystemExit(f"webui/Main.py: expected one history restore target, found {count}")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
