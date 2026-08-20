"""GPU-aware adapter for the additive Music Batch Streamlit page."""

from app.services.music_batch.gpu_manager import MusicBatchManager
from webui import music_batch as base_music_batch

# Keep the original UI implementation intact while injecting the GPU-aware manager.
base_music_batch.MusicBatchManager = MusicBatchManager
render_music_batch_page = base_music_batch.render_music_batch_page

__all__ = ["render_music_batch_page"]
