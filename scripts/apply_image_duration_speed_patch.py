from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/services/video.py",
    '''    def output_duration_limit(source_path: str) -> float:
        normalized_key = os.path.normcase(os.path.abspath(os.fspath(source_path)))
        return normalized_duration_overrides.get(
            normalized_key,
            float(max_clip_duration),
        )

    # max_clip_duration 约束的是成片里的最终播放时长，而不是源视频读取时长。
''',
    '''    def normalized_source_key(source_path: str) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(source_path)))

    def output_duration_limit(source_path: str) -> float:
        return normalized_duration_overrides.get(
            normalized_source_key(source_path),
            float(max_clip_duration),
        )

    def has_duration_override(source_path: str) -> bool:
        return normalized_source_key(source_path) in normalized_duration_overrides

    # max_clip_duration 约束的是成片里的最终播放时长，而不是源视频读取时长。
''',
)

replace_once(
    "app/services/video.py",
    '''    for video_path in video_paths:
        source_clip_duration = output_duration_limit(video_path) * normalized_clip_speed
        clip = _open_video_clip_quietly(video_path)
''',
    '''    for video_path in video_paths:
        duration_overridden = has_duration_override(video_path)
        source_clip_duration = output_duration_limit(video_path)
        if not duration_overridden:
            source_clip_duration *= normalized_clip_speed
        clip = _open_video_clip_quietly(video_path)
''',
)

replace_once(
    "app/services/video.py",
    '''            if normalized_clip_speed != 1.0:
                clip = clip.with_speed_scaled(normalized_clip_speed)
''',
    '''            duration_overridden = has_duration_override(
                subclipped_item.source_file_path
            )
            if normalized_clip_speed != 1.0 and not duration_overridden:
                clip = clip.with_speed_scaled(normalized_clip_speed)
''',
)
