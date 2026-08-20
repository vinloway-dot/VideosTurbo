from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Preserve mixed Video -> Image alternation in the final compositor and pass
# per-material duration limits so Image Duration is independent of Video Clip Duration.
replace_once(
    "app/services/task.py",
    '''def generate_final_videos(
    task_id, params, downloaded_videos, audio_file, subtitle_path, audio_duration
):
    final_video_paths = []
''',
    '''def generate_final_videos(
    task_id, params, downloaded_videos, audio_file, subtitle_path, audio_duration
):
    from app.services import stock_materials

    final_video_paths = []
''',
)

replace_once(
    "app/services/task.py",
    '''    # 多视频生成默认会打散素材以增加差异；但“按文案顺序匹配素材”追求的是
    # 时间线稳定性和可解释性，所以开启后所有输出都使用顺序拼接。
    if params.match_materials_to_script:
        video_concat_mode = VideoConcatMode.sequential
    elif params.video_count == 1:
        video_concat_mode = params.video_concat_mode
    else:
        video_concat_mode = VideoConcatMode.random
    video_transition_mode = params.video_transition_mode
''',
    '''    # 多视频生成默认会打散素材以增加差异；但“按文案顺序匹配素材”追求的是
    # 时间线稳定性和可解释性，所以开启后所有输出都使用顺序拼接。Mixed 模式
    # 会先在各自素材池内随机，再按 Video -> Image 交错，因此最终 compositor
    # 必须顺序消费交错后的列表，不能再次全局打乱。
    if params.match_materials_to_script:
        requested_concat_mode = VideoConcatMode.sequential
    elif params.video_count == 1:
        requested_concat_mode = params.video_concat_mode
    else:
        requested_concat_mode = VideoConcatMode.random
    video_concat_mode = stock_materials.effective_concat_mode(
        params.material_type,
        requested_concat_mode,
    )
    clip_duration_overrides = stock_materials.build_clip_duration_overrides(
        downloaded_videos,
        material_type=params.material_type,
        video_clip_duration=params.video_clip_duration,
        image_duration=params.image_duration,
    )
    video_transition_mode = params.video_transition_mode
''',
)

replace_once(
    "app/services/task.py",
    '''            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
            clip_speed=params.video_clip_speed,
        )
''',
    '''            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
            clip_speed=params.video_clip_speed,
            clip_duration_overrides=clip_duration_overrides,
        )
''',
)

# 2) Teach the existing core combiner an optional per-source duration override.
# Empty/None overrides preserve legacy Video-only behavior exactly.
replace_once(
    "app/services/video.py",
    '''def combine_videos(
    combined_video_path: str,
    video_paths: List[str],
    audio_file: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    video_transition_mode: VideoTransitionMode = None,
    max_clip_duration: int = 5,
    threads: int = 2,
    clip_speed: float = 1.0,
) -> str:
''',
    '''def combine_videos(
    combined_video_path: str,
    video_paths: List[str],
    audio_file: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    video_transition_mode: VideoTransitionMode = None,
    max_clip_duration: int = 5,
    threads: int = 2,
    clip_speed: float = 1.0,
    clip_duration_overrides: dict[str, float] | None = None,
) -> str:
''',
)

replace_once(
    "app/services/video.py",
    '''    # max_clip_duration 约束的是成片里的最终播放时长，而不是源视频读取时长。
    # MoviePy 以 0.5 倍速播放 1.5 秒源画面会得到 3 秒片段，以 2 倍速播放
    # 6 秒源画面同样会得到 3 秒片段。因此切片前必须按速度反推源时长；如果
    # 仍固定读取 3 秒再慢放、裁剪，下一段却从源视频第 3 秒开始，会跳过中间
    # 1.5 秒画面。该计算同时保证不同速度下的源时间线连续且无重叠。
    source_clip_duration = max_clip_duration * normalized_clip_speed
    output_dir = os.path.dirname(combined_video_path)
''',
    '''    # 默认仍使用统一 max_clip_duration。Image/Mixed 模式可以为预生成的
    # image clip 指定独立时长；路径统一转绝对路径并 normcase，避免 Windows
    # 大小写或相对路径差异导致 override 找不到。
    normalized_duration_overrides: dict[str, float] = {}
    for source_path, duration_limit in (clip_duration_overrides or {}).items():
        try:
            normalized_limit = float(duration_limit)
        except (TypeError, ValueError):
            continue
        if normalized_limit <= 0:
            continue
        normalized_key = os.path.normcase(os.path.abspath(os.fspath(source_path)))
        normalized_duration_overrides[normalized_key] = normalized_limit

    def output_duration_limit(source_path: str) -> float:
        normalized_key = os.path.normcase(os.path.abspath(os.fspath(source_path)))
        return normalized_duration_overrides.get(
            normalized_key,
            float(max_clip_duration),
        )

    # max_clip_duration 约束的是成片里的最终播放时长，而不是源视频读取时长。
    # MoviePy 以 0.5 倍速播放 1.5 秒源画面会得到 3 秒片段，以 2 倍速播放
    # 6 秒源画面同样会得到 3 秒片段。因此切片前必须按速度反推源时长。
    output_dir = os.path.dirname(combined_video_path)
''',
)

replace_once(
    "app/services/video.py",
    '''    for video_path in video_paths:
        clip = _open_video_clip_quietly(video_path)
        clip_duration = clip.duration
''',
    '''    for video_path in video_paths:
        source_clip_duration = output_duration_limit(video_path) * normalized_clip_speed
        clip = _open_video_clip_quietly(video_path)
        clip_duration = clip.duration
''',
)

replace_once(
    "app/services/video.py",
    '''            if clip.duration > max_clip_duration:
                clip = clip.subclipped(0, max_clip_duration)
''',
    '''            output_limit = output_duration_limit(subclipped_item.source_file_path)
            if clip.duration > output_limit:
                clip = clip.subclipped(0, output_limit)
''',
)
