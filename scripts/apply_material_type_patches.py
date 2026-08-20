from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Route the synthetic *_image / *_mixed provider names through the new
# stock-material orchestrator while leaving legacy video providers untouched.
replace_once(
    "app/services/material.py",
    '''def download_videos(
    task_id: str,
    search_terms: List[str],
    source: str = "pexels",
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    audio_duration: float = 0.0,
    max_clip_duration: int = 5,
    match_script_order: bool = False,
) -> List[str]:
    provider = "pexels"
    remote_search_videos = search_videos_pexels
''',
    '''def download_videos(
    task_id: str,
    search_terms: List[str],
    source: str = "pexels",
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    audio_duration: float = 0.0,
    max_clip_duration: int = 5,
    match_script_order: bool = False,
    image_duration: int = 8,
    image_motion: str = "random",
) -> List[str]:
    normalized_source = str(source or "").strip().lower()
    if normalized_source.endswith(("_image", "_mixed")):
        # Local import avoids a module cycle: stock_materials deliberately delegates
        # ordinary video mode back to this legacy downloader.
        from app.services import stock_materials

        return stock_materials.download_stock_materials(
            task_id=task_id,
            search_terms=search_terms,
            source=stock_materials.base_source(normalized_source),
            material_type=stock_materials.material_type_from_source(normalized_source),
            video_aspect=video_aspect,
            video_concat_mode=video_concat_mode,
            audio_duration=audio_duration,
            max_clip_duration=max_clip_duration,
            image_duration=image_duration,
            image_motion=image_motion,
            match_script_order=match_script_order,
        )

    source = normalized_source
    provider = "pexels"
    remote_search_videos = search_videos_pexels
''',
)

# 2) Pass the new image settings from VideoParams into the downloader. API clients
# and the Main WebUI then share exactly the same backend path.
replace_once(
    "app/services/task.py",
    '''            audio_duration=audio_duration * params.video_count,
            max_clip_duration=params.video_clip_duration,
            match_script_order=params.match_materials_to_script,
        )
''',
    '''            audio_duration=audio_duration * params.video_count,
            max_clip_duration=params.video_clip_duration,
            match_script_order=params.match_materials_to_script,
            image_duration=params.image_duration,
            image_motion=(
                params.image_motion.value
                if hasattr(params.image_motion, "value")
                else str(params.image_motion)
            ),
        )
''',
)

# 3) Music Batch: propagate resolved global/per-song material settings and rebuild
# provider-specific VideoParams through validation so Pexels/Pixabay are rewritten
# to their internal *_image / *_mixed source names.
replace_once(
    "app/services/music_batch/manager.py",
    '''            "video_transition_mode": resolved.get("video_transition_mode"),
            "video_clip_duration": int(resolved.get("video_clip_duration") or 8),
            "video_clip_speed": float(resolved.get("video_clip_speed") or 1.0),
            "video_count": 1,
''',
    '''            "video_transition_mode": resolved.get("video_transition_mode"),
            "video_clip_duration": int(resolved.get("video_clip_duration") or 8),
            "video_clip_speed": float(resolved.get("video_clip_speed") or 1.0),
            "material_type": resolved.get("material_type") or "video",
            "image_duration": int(resolved.get("image_duration") or 8),
            "image_motion": resolved.get("image_motion") or "random",
            "video_count": 1,
''',
)
replace_once(
    "app/services/music_batch/manager.py",
    '''        for plan_index, source_plan in enumerate(source_plans, start=1):
            provider_params = params.model_copy(
                update={"video_source": source_plan.provider}
            )
            materials = task_service.get_video_materials(
''',
    '''        for plan_index, source_plan in enumerate(source_plans, start=1):
            provider_payload = params.model_dump(mode="python")
            provider_payload["video_source"] = source_plan.provider
            provider_payload["material_type"] = params.material_type
            provider_params = VideoParams.model_validate(provider_payload)
            materials = task_service.get_video_materials(
''',
)
replace_once(
    "app/services/music_batch/manager.py",
    '''            raise RuntimeError(f"no usable stock videos were downloaded from: {providers}")
''',
    '''            raise RuntimeError(f"no usable stock materials were downloaded from: {providers}")
''',
)

# 4) Main WebUI: keep the provider selectbox as the persisted base source, then add
# Material Type beneath it. Only searchable stock providers expose Image/Mixed in
# this version. Local file handling remains the existing proven pipeline.
replace_once(
    "webui/Main.py",
    '''            saved_video_source_name = config.app.get("video_source", "pexels")

            params.video_source = stable_selectbox(
                tr("Video Source"),
                options=[value for _, value in video_sources],
                default_value=saved_video_source_name,
                key="video_source_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_sources
                )[value],
            )
            _set_runtime_config("app", "video_source", params.video_source)

            if params.video_source == "local":
''',
    '''            saved_video_source_name = config.app.get("video_source", "pexels")

            selected_video_source = stable_selectbox(
                tr("Video Source"),
                options=[value for _, value in video_sources],
                default_value=saved_video_source_name,
                key="video_source_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_sources
                )[value],
            )
            _set_runtime_config("app", "video_source", selected_video_source)

            material_type_labels = {
                "video": "Video",
                "image": "Image",
                "mixed": "Video + Image",
            }
            supported_material_types = (
                ["video", "image", "mixed"]
                if selected_video_source in {"pexels", "pixabay"}
                else ["video"]
            )
            saved_material_type = str(config.ui.get("material_type", "video") or "video")
            if saved_material_type not in supported_material_types:
                saved_material_type = "video"
            params.material_type = stable_selectbox(
                "Material Type",
                options=supported_material_types,
                default_value=saved_material_type,
                key=f"material_type_for_{selected_video_source}",
                format_func=lambda value: material_type_labels[value],
                disabled=len(supported_material_types) == 1,
            )
            _set_runtime_config("ui", "material_type", params.material_type)

            params.image_duration = 8
            params.image_motion = "random"
            if params.material_type in {"image", "mixed"}:
                params.image_duration = st.number_input(
                    "Image Duration (seconds)",
                    min_value=1,
                    max_value=30,
                    value=int(config.ui.get("image_duration", 8) or 8),
                    step=1,
                    key=f"image_duration_for_{selected_video_source}",
                )
                motion_labels = {
                    "slow_zoom_in": "Slow Zoom In",
                    "slow_zoom_out": "Slow Zoom Out",
                    "pan_left_right": "Pan Left → Right",
                    "pan_right_left": "Pan Right → Left",
                    "random": "Random",
                    "none": "None",
                }
                saved_image_motion = str(config.ui.get("image_motion", "random") or "random")
                if saved_image_motion not in motion_labels:
                    saved_image_motion = "random"
                params.image_motion = stable_selectbox(
                    "Ken Burns Effect",
                    options=list(motion_labels),
                    default_value=saved_image_motion,
                    key=f"image_motion_for_{selected_video_source}",
                    format_func=lambda value: motion_labels[value],
                )
                _set_runtime_config("ui", "image_duration", int(params.image_duration))
                _set_runtime_config("ui", "image_motion", params.image_motion)

            if selected_video_source in {"pexels", "pixabay"} and params.material_type != "video":
                params.video_source = f"{selected_video_source}_{params.material_type}"
            else:
                params.video_source = selected_video_source

            if params.video_source == "local":
''',
)

# 5) Music Batch UI: add global and per-song choices. For Image/Mixed, hide Coverr
# because it is video-only in this feature.
replace_once(
    "webui/music_batch.py",
    '''_SOURCES = ["pexels", "pixabay", "coverr"]
_OVERRIDE_SUFFIXES = (
    "enabled",
    "script",
    "keywords",
    "sources",
    "clip_duration",
    "concat",
    "transition",
    "speed",
)
''',
    '''_SOURCES = ["pexels", "pixabay", "coverr"]
_MATERIAL_TYPES = ["video", "image", "mixed"]
_MATERIAL_TYPE_LABELS = {
    "video": "Video",
    "image": "Image",
    "mixed": "Video + Image",
}
_IMAGE_MOTIONS = {
    "slow_zoom_in": "Slow Zoom In",
    "slow_zoom_out": "Slow Zoom Out",
    "pan_left_right": "Pan Left → Right",
    "pan_right_left": "Pan Right → Left",
    "random": "Random",
    "none": "None",
}
_OVERRIDE_SUFFIXES = (
    "enabled",
    "script",
    "keywords",
    "sources",
    "material_type",
    "image_duration",
    "image_motion",
    "clip_duration",
    "concat",
    "transition",
    "speed",
)
''',
)
replace_once(
    "webui/music_batch.py",
    '''    sources = st.multiselect(
        "Stock Sources override",
        _SOURCES,
        key=f"{prefix}_sources",
    )
    clip_duration = st.number_input(
''',
    '''    material_type = st.selectbox(
        "Material Type override",
        _MATERIAL_TYPES,
        key=f"{prefix}_material_type",
        format_func=lambda value: _MATERIAL_TYPE_LABELS[value],
    )
    override_sources = _SOURCES if material_type == "video" else ["pexels", "pixabay"]
    sources = st.multiselect(
        "Stock Sources override",
        override_sources,
        key=f"{prefix}_sources",
    )
    image_duration = None
    image_motion = None
    if material_type in {"image", "mixed"}:
        image_duration = st.number_input(
            "Image Duration override (seconds)",
            min_value=1,
            max_value=30,
            value=8,
            step=1,
            key=f"{prefix}_image_duration",
        )
        image_motion = st.selectbox(
            "Ken Burns Effect override",
            list(_IMAGE_MOTIONS),
            index=list(_IMAGE_MOTIONS).index("random"),
            key=f"{prefix}_image_motion",
            format_func=lambda value: _IMAGE_MOTIONS[value],
        )
    clip_duration = st.number_input(
''',
)
replace_once(
    "webui/music_batch.py",
    '''        video_keywords=_parse_keywords(keywords_text) or None,
        stock_sources=list(sources) or None,
        video_clip_duration=int(clip_duration),
''',
    '''        video_keywords=_parse_keywords(keywords_text) or None,
        stock_sources=list(sources) or None,
        material_type=material_type,
        image_duration=int(image_duration) if image_duration is not None else None,
        image_motion=image_motion,
        video_clip_duration=int(clip_duration),
''',
)
replace_once(
    "webui/music_batch.py",
    '''    global_sources = st.multiselect(
        "Stock Video Sources",
        _SOURCES,
        default=["pexels"],
        key="music_batch_sources",
    )

    row1 = st.columns(4)
''',
    '''    global_material_type = st.selectbox(
        "Material Type",
        _MATERIAL_TYPES,
        index=0,
        key="music_batch_material_type",
        format_func=lambda value: _MATERIAL_TYPE_LABELS[value],
    )
    global_source_options = (
        _SOURCES if global_material_type == "video" else ["pexels", "pixabay"]
    )
    global_sources = st.multiselect(
        "Stock Sources",
        global_source_options,
        default=["pexels"],
        key="music_batch_sources",
    )
    global_image_duration = 8
    global_image_motion = "random"
    if global_material_type in {"image", "mixed"}:
        image_row = st.columns(2)
        global_image_duration = image_row[0].number_input(
            "Image Duration (seconds)",
            min_value=1,
            max_value=30,
            value=8,
            step=1,
            key="music_batch_image_duration",
        )
        global_image_motion = image_row[1].selectbox(
            "Ken Burns Effect",
            list(_IMAGE_MOTIONS),
            index=list(_IMAGE_MOTIONS).index("random"),
            key="music_batch_image_motion",
            format_func=lambda value: _IMAGE_MOTIONS[value],
        )

    row1 = st.columns(4)
''',
)
replace_once(
    "webui/music_batch.py",
    '''            stock_sources=list(global_sources),
            video_aspect=aspect,
''',
    '''            stock_sources=list(global_sources),
            material_type=global_material_type,
            image_duration=int(global_image_duration),
            image_motion=global_image_motion,
            video_aspect=aspect,
''',
)

print("material type wiring patch applied")
