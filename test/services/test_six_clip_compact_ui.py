from pathlib import Path


TIMELINE_SOURCE = Path("webui/six_clip_timeline.py").read_text(encoding="utf-8")


def test_timeline_uses_three_column_cards_and_one_active_editor():
    assert 'EDIT_SEGMENT_SESSION_KEY = "six_clip_edit_segment_index"' in TIMELINE_SOURCE
    assert "card_columns = st.columns(3)" in TIMELINE_SOURCE
    assert '"Edit Clip"' in TIMELINE_SOURCE
    assert "active_index = st.session_state.get(EDIT_SEGMENT_SESSION_KEY)" in TIMELINE_SOURCE
    assert "active_segment = next(" in TIMELINE_SOURCE


def test_active_editor_uses_compact_text_areas():
    narration_block = TIMELINE_SOURCE.split("narration = st.text_area(", 1)[1].split(
        "prompt = st.text_area(", 1
    )[0]
    prompt_block = TIMELINE_SOURCE.split("prompt = st.text_area(", 1)[1].split(
        "media_mode = st.radio(", 1
    )[0]

    assert '"Narration Context"' in narration_block
    assert "height=90" in narration_block
    assert '"Video Prompt (English)"' in prompt_block
    assert "height=140" in prompt_block


def test_master_prompt_batches_are_collapsed_by_default():
    assert "with st.expander(" in TIMELINE_SOURCE
    assert 'f"Master Prompt — Batch {batch_index}"' in TIMELINE_SOURCE
    assert "expanded=False" in TIMELINE_SOURCE
