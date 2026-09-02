from app.models.cloud_agent import CloudJobCreate, CloudJobDraftRequest
from app.models.schema import VideoParams
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.research.models import ResearchDraftRequest


def test_target_word_defaults_are_120_for_new_requests_and_plans():
    plan = empty_six_clip_plan()
    job = CloudJobCreate(
        subject="New default",
        script="A valid narration script for this default-value test.",
        master_prompt="Create six chronological videos from this narration.",
        clip_plan=plan,
        tts_provider="test",
        voice_id="voice",
    )
    research = ResearchDraftRequest(
        subject="New default",
        provider="openrouter",
        model_choice="test-model",
    )

    assert CloudJobDraftRequest(subject="New default").target_words == 120
    assert plan.target_words == 120
    assert job.target_words == 120
    assert research.target_words == 120
    assert VideoParams(video_subject="New default").target_words == 120
