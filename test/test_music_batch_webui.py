from pathlib import Path


def test_music_batch_ui_module_exports_renderer():
    from webui.music_batch import render_music_batch_page

    assert callable(render_music_batch_page)


def test_streamlit_page_registers_music_batch_without_replacing_main():
    page = Path("webui/pages/2_Music_Batch.py")
    assert page.is_file()
    assert "render_music_batch_page" in page.read_text(encoding="utf-8")

    main_source = Path("webui/Main.py").read_text(encoding="utf-8")
    assert "def _render_application():" in main_source
    assert main_source.rstrip().endswith("_render_application()")
