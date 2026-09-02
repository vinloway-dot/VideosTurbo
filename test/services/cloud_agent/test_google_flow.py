from contextlib import contextmanager
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.errors import (
    FlowBatchIncompleteError,
    FlowGenerationTimeoutError,
    FlowWorkspaceVerificationError,
    HumanRequiredError,
    MediaValidationError,
)
from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.providers import google_flow
from app.services.cloud_agent.providers.google_flow import classify_google_flow_session


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "resources" / "cloud_agent" / "google_flow"


@pytest.mark.parametrize(
    ("fixture_name", "url", "expected"),
    [
        ("ready.html", "https://labs.google/fx/tools/flow/project/demo", ServiceSessionStatus.READY),
        ("login.html", "https://accounts.google.com/v3/signin/identifier", ServiceSessionStatus.SESSION_EXPIRED),
        ("continue_google.html", "https://labs.google/fx/tools/flow", ServiceSessionStatus.SESSION_EXPIRED),
        ("password.html", "https://accounts.google.com/v3/signin/challenge/pwd", ServiceSessionStatus.LOGIN_REQUIRED),
        ("captcha.html", "https://accounts.google.com/v3/signin/challenge", ServiceSessionStatus.CAPTCHA_REQUIRED),
        ("two_factor.html", "https://accounts.google.com/v3/signin/challenge/totp", ServiceSessionStatus.TWO_FACTOR_REQUIRED),
        ("verification.html", "https://accounts.google.com/v3/signin/challenge/ipp", ServiceSessionStatus.VERIFICATION_REQUIRED),
        ("unknown.html", "https://labs.google/fx/tools/flow", ServiceSessionStatus.ERROR),
    ],
)
def test_google_flow_session_fixture_classification(fixture_name, url, expected):
    html = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")

    assert classify_google_flow_session(url=url, html=html) is expected


def test_google_flow_challenge_wins_over_ready_marker():
    html = """
    <html><body>
      <button aria-label="Agent">Agent</button>
      <textarea aria-label="Prompt"></textarea>
      <div>2-Step Verification</div>
      <input autocomplete="one-time-code" />
    </body></html>
    """

    assert (
        classify_google_flow_session(
            url="https://labs.google/fx/tools/flow/project/demo",
            html=html,
        )
        is ServiceSessionStatus.TWO_FACTOR_REQUIRED
    )


def test_google_flow_project_shell_ignores_passive_recaptcha_legal_notice():
    html = """
    <html><body>
      <main>
        <h1>Meet your agent</h1>
        <section>Your agent in Google Flow</section>
        <button>Agent</button>
      </main>
      <footer>
        This site is protected by <a href="privacy">reCAPTCHA</a>. The Google
        <a href="privacy">Privacy Policy</a> and
        <a href="terms">Terms of Service</a> apply.
      </footer>
      <script src="recaptcha-runtime.js"></script>
      <iframe title="reCAPTCHA" src="recaptcha-frame"></iframe>
      <textarea class="g-recaptcha-response" hidden></textarea>
      <template>Sign in</template>
    </body></html>
    """

    assert (
        classify_google_flow_session(
            url="https://labs.google/fx/th/tools/flow/project/demo",
            html=html,
        )
        is ServiceSessionStatus.READY
    )


def test_google_flow_active_captcha_wins_over_project_shell():
    html = """
    <html><body>
      <main>
        <h1>Meet your agent</h1>
        <div class="g-recaptcha">Confirm you're not a robot</div>
      </main>
    </body></html>
    """

    assert (
        classify_google_flow_session(
            url="https://labs.google/fx/th/tools/flow/project/demo",
            html=html,
        )
        is ServiceSessionStatus.CAPTCHA_REQUIRED
    )


class FakeDownload:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    def save_as(self, path):
        self.page.download_attempts[self.index] += 1
        Path(path).write_bytes(f"video-{self.index + 1}".encode())


class FakeDownloadExpectation:
    def __init__(self, page):
        self.page = page
        self.index = None

    def __enter__(self):
        self.page.active_download = self
        self.page.actions.append(("expect_download", "registered"))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.page.active_download = None

    @property
    def value(self):
        assert self.index is not None
        return FakeDownload(self.page, self.index)


class FakeLocator:
    def __init__(self, page, kind, index=None, *, pinned=False):
        self.page = page
        self.kind = kind
        self.index = index
        self.pinned = pinned

    def click(self):
        if self.kind in {"agent", "agent_role"}:
            self.page.agent_click_count += 1
            self.page.actions.append(("click", "agent"))
            if self.page.agent_pressed is True:
                self.page.agent_pressed = False
            elif self.page.agent_pressed is False:
                if self.page.agent_activation_delay_reads:
                    self.page.agent_activation_pending = True
                else:
                    self.page.agent_pressed = True
            return
        if self.kind == "launch":
            self.page.landing = False
            self.page.actions.append(("click", "launch"))
            return
        if self.kind == "media_card":
            self.page.active_card_index = self.index
            self.page.actions.append(("click", "media_card", self.index))
            if self.page.card_click_opens_editor:
                self.page.in_clip_editor = True
            return
        if self.kind == "card_menu":
            self.page.active_card_index = self.index
            self.page.card_menu_open = True
            self.page.actions.append(("click", "card_menu", self.index))
            return
        if self.kind == "card_menu_delete":
            self.page.actions.append(("click", "card_menu_delete"))
            if self.page.delete_requires_confirmation:
                self.page.confirmation_pending = True
                self.page.pending_card_delete_index = self.page.active_card_index
            elif self.page.card_delete_removes:
                self.page._remove_active_card()
            self.page.card_menu_open = False
            return
        if self.kind == "back_to_project":
            self.page.in_clip_editor = False
            self.page.actions.append(("click", "back_to_project"))
            return
        if self.kind == "card_delete":
            self.page.actions.append(("click", "card_delete"))
            if self.page.delete_requires_confirmation:
                self.page.confirmation_pending = True
                self.page.pending_card_delete_index = self.page.active_card_index
            elif self.page.card_delete_removes:
                self.page._remove_active_card()
            return
        if self.kind == "delete_selected":
            self.page.actions.append(("click", "delete_selected"))
            if self.page.delete_requires_confirmation:
                self.page.confirmation_pending = True
            else:
                self.page.clip_names.clear()
                self.page.selected_clip_indexes.clear()
                self.page.inventory_sequence = [0, 0, 0]
                self.page.last_inventory_count = 0
            return
        if self.kind == "confirm_delete":
            self.page.actions.append(("click", "confirm_delete"))
            if self.page.pending_card_delete_index is not None:
                self.page._remove_active_card()
                self.page.pending_card_delete_index = None
            else:
                self.page.clip_names.clear()
            self.page.selected_clip_indexes.clear()
            self.page.confirmation_pending = False
            self.page.inventory_sequence = [0, 0, 0]
            self.page.last_inventory_count = 0
            return
        if self.kind in {"generate", "fallback_generate"}:
            action_kind = self.kind
            if (
                self.kind == "fallback_generate"
                and self.page.fallback_composer_x_positions[self.index or 0] >= 1000
            ):
                action_kind = (
                    "side_panel_generate"
                    if self.page.fallback_composer_y_positions[self.index or 0] >= 600
                    else "upper_panel_generate"
                )
            self.page.actions.append(("click", action_kind))
            self.page.generate_clicked = True
            if self.page.last_filled in {
                google_flow.RENAME_CLIPS_INSTRUCTION,
                google_flow.RENAME_SURVIVING_CLIPS_INSTRUCTION,
            }:
                self.page.pending_clip_names = list(self.page.renamed_clip_names)
                self.page.agent_response_count += 1
            elif self.page.generation_completion_names is not None:
                self.page.clip_names = list(self.page.generation_completion_names)
            return
        if self.kind == "bulk_download":
            assert self.page.active_download is not None
            self.page.active_download.index = 0
            self.page.actions.append(("click", "bulk_download"))
            return
        if self.kind == "project_menu":
            self.page.project_menu_open = True
            self.page.actions.append(("click", "project_menu"))
            return
        if self.kind == "project_download":
            assert self.page.active_download is not None
            assert self.page.project_menu_open
            self.page.active_download.index = 0
            self.page.project_menu_open = False
            self.page.semantic_name_reads_at_project_download.append(
                dict(self.page.semantic_name_reads)
            )
            self.page.actions.append(("click", "project_download"))
            return
        if self.kind == "download":
            assert self.page.active_download is not None
            self.page.active_download.index = self.index
            return
        self.page.actions.append(("click", self.kind))

    def check(self):
        assert self.kind == "checkbox"
        self.page.selected_clip_indexes.add(self.index)
        self.page.actions.append(("check", self.index))

    def hover(self):
        assert self.kind == "media_card"
        self.page.hovered_card_index = self.index
        self.page.actions.append(("hover", "media_card", self.index))

    def fill(self, value):
        action_kind = self.kind
        if (
            self.kind == "fallback_prompt"
            and self.page.fallback_composer_x_positions[self.index or 0] >= 1000
        ):
            action_kind = (
                "side_panel_prompt"
                if self.page.fallback_composer_y_positions[self.index or 0] >= 600
                else "upper_panel_prompt"
            )
        self.page.actions.append(("fill", action_kind, value))
        self.page.fill_agent_pressed_states.append(self.page.agent_pressed)
        self.page.last_filled = value
        if self.kind == "fallback_prompt":
            self.page.command_prompt_values[self.index or 0] = value
            self.page.fallback_prompt_was_filled = True
        if self.page.agent_deactivates_on_prompt_fill:
            self.page.agent_pressed = False

    def count(self):
        if self.kind == "downloads":
            return self.page.download_count
        if self.kind == "project_menu":
            return int(self.page.project_menu_available)
        if self.kind == "global_menu":
            return 1
        if self.kind == "project_download":
            return int(self.page.project_menu_open)
        if self.kind == "agent_response_feedback":
            self.page.agent_response_reads += 1
            if self.index is None:
                return self.page.agent_response_count
            return int(self.index < self.page.agent_response_count)
        if self.kind == "launch":
            return int(self.page.landing)
        if self.kind == "back_to_project":
            return int(self.page.in_clip_editor)
        if self.kind == "agent_role":
            return int(not self.page.landing and self.page.agent_available)
        if self.kind == "agent":
            return int(not self.page.landing and self.page.agent_text_available)
        if self.kind == "agent_text":
            return int(not self.page.landing and self.page.agent_text_available)
        if self.kind == "checkboxes":
            return (
                len(self.page.clip_names)
                if self.page.checkbox_count is None
                else self.page.checkbox_count
            )
        if self.kind == "media_cards":
            if self.page.in_clip_editor:
                return 0
            if self.page.has_completed_video_polls:
                return len(self.page.active_completed_video_poll)
            if self.page.card_count_sequence:
                self.page.last_card_count = self.page.card_count_sequence.pop(0)
                return self.page.last_card_count
            return len(self.page.clip_names)
        if self.kind == "media_card":
            card_count = (
                len(self.page.active_completed_video_poll)
                if self.page.has_completed_video_polls
                else len(self.page.clip_names)
            )
            return int(self.index is not None and self.index < card_count)
        if self.kind == "card_menu":
            return int(self.page.hovered_card_index == self.index)
        if self.kind == "card_menu_delete":
            return int(self.page.card_menu_open and self.page.card_delete_available)
        if self.kind == "card_video":
            if self.index is None or self.index >= len(self.page.active_completed_video_poll):
                return 0
            return int(
                self.page.active_completed_video_poll[self.index].get(
                    "has_video_element", True
                )
            )
        if self.kind == "card_delete":
            return int(
                self.page.card_delete_available
                and self.page.active_card_index is not None
                and self.page.active_card_index < len(self.page.clip_names)
            )
        if self.kind == "delete_selected":
            return int(
                self.page.delete_available
                and bool(self.page.clip_names)
                and len(self.page.selected_clip_indexes) == len(self.page.clip_names)
            )
        if self.kind == "empty_state":
            if self.page.empty_state_sequence:
                self.page.last_empty_state = self.page.empty_state_sequence.pop(0)
            else:
                self.page.last_empty_state = self.page.empty_state_available
            return int(not self.page.clip_names and self.page.last_empty_state)
        if self.kind == "fallback_prompts":
            return len(self.page.fallback_composer_x_positions)
        if self.kind in {
            "fallback_prompt",
            "fallback_container",
            "fallback_generate",
        }:
            return int(
                self.index is not None
                and self.index < len(self.page.fallback_composer_x_positions)
            )
        if self.kind in {
            "prompt",
            "default_prompt",
            "multiple_prompt",
            "composer",
            "generate",
            "fallback_prompt",
            "fallback_container",
            "fallback_generate",
            "bulk_download",
        }:
            if self.kind == "multiple_prompt":
                return self.page.agent_prompt_count
            return 1
        if self.kind == "media_control":
            return int(self.page.media_control_available)
        if self.kind == "media_list":
            if self.page.in_clip_editor:
                return 0
            if self.page.media_list_sequence:
                self.page.last_media_list_available = self.page.media_list_sequence.pop(0)
            return int(self.page.last_media_list_available)
        if self.kind == "inventory_cards":
            if self.page.inventory_sequence:
                self.page.last_inventory_count = self.page.inventory_sequence.pop(0)
            return self.page.last_inventory_count
        if self.kind == "busy":
            return int(self.page.busy)
        if self.kind == "progressbar":
            return int(self.page.progressbar)
        if self.kind == "generated_images":
            return len(self.page.generated_image_alts)
        if self.kind == "videos":
            return sum(
                int(
                    card["media_type"] == "video"
                    and card.get("has_video_element", True)
                    and card.get("video_visible", True)
                )
                + int(card.get("extra_visible_videos", 0))
                for card in self.page.active_completed_video_poll
            )
        if self.kind == "missing":
            return 0
        if self.kind in {"semantic_name", "semantic_card_name"}:
            self.page.semantic_name_reads[self.index] += 1
            names = (
                self.page.clip_names
                if self.kind == "semantic_card_name"
                or self.page.page_semantic_names is None
                else self.page.page_semantic_names
            )
            return names.count(f"clip {self.index}")
        if self.kind in {"dialog", "confirm_delete"}:
            return int(self.page.confirmation_pending)
        raise AssertionError(f"count is unavailable for {self.kind}")

    def is_visible(self):
        if self.kind == "empty_state":
            return not self.page.clip_names and self.page.last_empty_state
        if self.kind == "media_list":
            return self.page.last_media_list_available
        return self.count() == 1

    def wait_for(self, **_kwargs):
        if not self.is_visible():
            raise AssertionError(f"{self.kind} did not become visible")

    def is_enabled(self):
        if self.kind == "generate" and not self.page.generate_clicked:
            return self.page.generate_available
        if self.kind == "generate" and self.page.send_stale_after_click:
            raise PlaywrightError("stale Flow rename submit locator")
        if self.kind == "generate" and self.page.agent_enabled_states:
            return self.page.agent_enabled_states.pop(0)
        if self.kind == "generate" and self.page.generate_clicked:
            return self.page.send_enabled_after_click
        return self.page.agent_ready

    def get_attribute(self, name):
        if self.kind in {"agent", "agent_role"} and name == "aria-pressed":
            if self.page.agent_activation_pending:
                if self.page.agent_activation_delay_reads:
                    self.page.agent_activation_delay_reads -= 1
                    return "false"
                self.page.agent_activation_pending = False
                self.page.agent_pressed = True
            if self.page.agent_pressed is None:
                return None
            return str(self.page.agent_pressed).lower()
        if self.kind == "generated_image" and name == "alt":
            return self.page.generated_image_alts[self.index]
        if self.kind == "media_card" and name == "aria-busy":
            if self.index >= len(self.page.active_completed_video_poll):
                return "false"
            card = self.page.active_completed_video_poll[self.index]
            return "true" if card.get("busy", False) else "false"
        return None

    def inner_text(self):
        if self.kind == "media_card":
            if self.index >= len(self.page.active_completed_video_poll):
                return ""
            card = self.page.active_completed_video_poll[self.index]
            if card.get("failed"):
                return card.get("failure_text", "Audio Generation Failed")
            if card.get("processing"):
                return "Processing"
            return card.get("description", "Duration 00:10")
        raise AssertionError(f"inner_text is unavailable for {self.kind}")

    def input_value(self):
        assert self.kind in {
            "prompt",
            "default_prompt",
            "multiple_prompt",
            "fallback_prompt",
        }
        if self.kind == "fallback_prompt":
            if self.page.require_pinned_fallback_identity and not self.pinned:
                return ""
            return self.page.command_prompt_values.get(self.index or 0, "")
        return self.page.last_filled

    def bounding_box(self):
        if self.kind in {"fallback_prompt", "fallback_container"}:
            return {
                "x": self.page.fallback_composer_x_positions[self.index or 0],
                "y": self.page.fallback_composer_y_positions[self.index or 0],
                "width": 300,
                "height": 80,
            }
        raise AssertionError(f"bounding_box is unavailable for {self.kind}")

    def element_handle(self):
        return FakeLocator(
            self.page,
            self.kind,
            index=self.index,
            pinned=True,
        )

    def locator(self, selector):
        if self.kind in {"agent", "agent_role"} and str(selector).startswith("xpath="):
            return FakeLocator(self.page, "composer")
        if self.kind == "agent_text" and str(selector).startswith("xpath="):
            return FakeLocator(self.page, "agent")
        if self.kind == "composer" and "textarea" in str(selector):
            prompt_kind = (
                "multiple_prompt"
                if self.page.agent_prompt_count != 1
                else "prompt"
            )
            return FakeLocator(self.page, prompt_kind)
        if self.kind == "fallback_prompt" and str(selector).startswith("xpath="):
            return FakeLocator(
                self.page,
                "fallback_container",
                index=self.index,
                pinned=self.pinned,
            )
        if self.kind == "media_list" and "role=\"button\"" in str(selector):
            return FakeLocator(self.page, "inventory_cards")
        if self.kind == "media_card" and str(selector) == "video":
            return FakeLocator(self.page, "card_video", index=self.index)
        raise AssertionError(f"unexpected nested locator: {self.kind} {selector}")

    def get_by_role(self, role, *, name=None, exact=None):
        del exact
        pattern = getattr(name, "pattern", str(name)).lower()
        if self.kind == "composer" and role == "button" and "generate" in pattern:
            return FakeLocator(self.page, "generate")
        if self.kind == "fallback_container" and role == "button" and (
            "generate" in pattern or "สร้าง" in pattern
        ):
            return FakeLocator(
                self.page,
                "fallback_generate",
                index=self.index,
                pinned=self.pinned,
            )
        if self.kind == "media_card" and role == "button" and "more" in pattern:
            return FakeLocator(self.page, "card_menu", index=self.index)
        if self.kind == "dialog" and role == "button" and (
            "delete" in pattern or "ลบ" in pattern
        ):
            return FakeLocator(self.page, "confirm_delete")
        raise AssertionError(f"unexpected nested role lookup: {self.kind} {role} {pattern}")

    def get_by_text(self, text, *, exact=None):
        del exact
        pattern = getattr(text, "pattern", str(text)).lower()
        semantic = re.fullmatch(r"clip\s+([1-6])", pattern)
        if self.kind == "media_cards" and semantic is not None:
            return FakeLocator(
                self.page,
                "semantic_card_name",
                index=int(semantic.group(1)),
            )
        raise AssertionError(f"unexpected nested text lookup: {self.kind} {pattern}")

    def nth(self, index):
        if self.kind == "downloads":
            return FakeLocator(self.page, "download", index=index)
        if self.kind == "checkboxes":
            return FakeLocator(self.page, "checkbox", index=index)
        if self.kind == "media_cards":
            return FakeLocator(self.page, "media_card", index=index)
        if self.kind == "generated_images":
            return FakeLocator(self.page, "generated_image", index=index)
        if self.kind == "agent_response_feedback":
            return FakeLocator(self.page, "agent_response_feedback", index=index)
        if self.kind == "fallback_prompts":
            return FakeLocator(self.page, "fallback_prompt", index=index)
        raise AssertionError(f"nth is unavailable for {self.kind}")


class FakePage:
    def __init__(
        self,
        *,
        progress_html,
        home_progress_html=None,
        reload_progress_html=None,
        reload_state_updates=None,
        project_navigation_url=None,
        session_ready=True,
        download_count=6,
        landing=False,
        agent_available=True,
        agent_text_available=None,
        clip_names=None,
        page_semantic_names=None,
        empty_state_available=True,
        delete_available=True,
        prompt_label_available=True,
        generation_completion_names=None,
        renamed_clip_names=None,
        agent_enabled_states=None,
        send_enabled_after_click=True,
        send_stale_after_click=False,
        delete_requires_confirmation=False,
        empty_state_sequence=None,
        document_ready=True,
        document_ready_sequence=None,
        media_control_available=True,
        media_list_available=True,
        media_list_sequence=None,
        inventory_sequence=None,
        card_count_sequence=None,
        busy=False,
        progressbar=False,
        evaluate_errors=None,
        agent_pressed=True,
        agent_activation_delay_reads=0,
        agent_prompt_count=1,
        default_prompt_visible=False,
        agent_deactivates_on_prompt_fill=False,
        generate_available=True,
        generated_image_alts=None,
        completed_video_polls=None,
        agent_response_count=1,
        checkbox_count=None,
        card_delete_available=True,
        card_delete_removes=True,
        card_click_opens_editor=False,
        fallback_composer_available=False,
        fallback_composer_x_positions=None,
        fallback_composer_y_positions=None,
        require_pinned_fallback_identity=False,
        project_menu_available=True,
        project_header_menu_name=None,
        global_menu_name=None,
        rename_response_text="",
    ):
        self.url = "about:blank"
        self.project_progress_html = list(progress_html)
        self.progress_html = list(progress_html)
        self.home_progress_html = list(home_progress_html or ["<main>Flow home</main>"])
        self.project_navigation_url = project_navigation_url
        self.session_ready = session_ready
        self.on_home = False
        self.reload_progress_html = [
            list(html) for html in (reload_progress_html or [])
        ]
        self.reload_state_updates = list(reload_state_updates or [])
        self.download_count = download_count
        self.download_attempts = [0 for _ in range(download_count)]
        self.goto_calls = []
        self.actions = []
        self.active_download = None
        self.landing = landing
        self.agent_available = agent_available
        self.agent_text_available = (
            agent_available
            if agent_text_available is None
            else agent_text_available
        )
        self.clip_names = list(clip_names or [])
        self.page_semantic_names = (
            None if page_semantic_names is None else list(page_semantic_names)
        )
        self.empty_state_available = empty_state_available
        self.delete_available = delete_available
        self.selected_clip_indexes = set()
        self.reload_calls = []
        self.prompt_label_available = prompt_label_available
        self.generation_completion_names = generation_completion_names
        self.renamed_clip_names = list(
            renamed_clip_names
            or [f"clip {number}" for number in range(1, 7)]
        )
        self.pending_clip_names = None
        self.last_filled = ""
        self.agent_ready = True
        self.agent_enabled_states = list(agent_enabled_states or [False, True])
        self.send_enabled_after_click = send_enabled_after_click
        self.send_stale_after_click = send_stale_after_click
        self.delete_requires_confirmation = delete_requires_confirmation
        self.confirmation_pending = False
        self.empty_state_sequence = list(empty_state_sequence or [])
        self.last_empty_state = empty_state_available
        self.document_ready = document_ready
        self.document_ready_sequence = list(document_ready_sequence or [])
        self.media_control_available = media_control_available
        self.media_list_available = media_list_available
        self.media_list_sequence = list(media_list_sequence or [])
        self.last_media_list_available = media_list_available
        self.inventory_sequence = list(
            inventory_sequence if inventory_sequence is not None else [len(self.clip_names)]
        )
        self.card_count_sequence = list(card_count_sequence or [])
        self.last_card_count = len(self.clip_names)
        self.last_inventory_count = self.inventory_sequence[0]
        self.busy = busy
        self.progressbar = progressbar
        self.evaluate_errors = list(evaluate_errors or [])
        self.agent_pressed = agent_pressed
        self.agent_activation_delay_reads = agent_activation_delay_reads
        self.agent_activation_pending = False
        self.agent_prompt_count = agent_prompt_count
        self.default_prompt_visible = default_prompt_visible
        self.agent_deactivates_on_prompt_fill = agent_deactivates_on_prompt_fill
        self.generate_available = generate_available
        self.generated_image_alts = list(generated_image_alts or [])
        self.has_completed_video_polls = completed_video_polls is not None
        self.completed_video_polls = list(completed_video_polls or [[]])
        self.agent_response_count = agent_response_count
        self.agent_response_reads = 0
        self.active_completed_video_poll = list(self.completed_video_polls[0])
        self.context = FakeCdpContext(self)
        self.checkbox_count = checkbox_count
        self.card_delete_available = card_delete_available
        self.card_delete_removes = card_delete_removes
        self.card_click_opens_editor = card_click_opens_editor
        self.fallback_composer_available = fallback_composer_available
        self.fallback_composer_x_positions = list(
            fallback_composer_x_positions
            if fallback_composer_x_positions is not None
            else ([0] if fallback_composer_available else [])
        )
        self.fallback_composer_y_positions = list(
            fallback_composer_y_positions
            if fallback_composer_y_positions is not None
            else [700] * len(self.fallback_composer_x_positions)
        )
        assert len(self.fallback_composer_y_positions) == len(
            self.fallback_composer_x_positions
        )
        self.require_pinned_fallback_identity = require_pinned_fallback_identity
        self.fallback_prompt_was_filled = False
        self.command_prompt_values = {}
        self.project_menu_available = project_menu_available
        self.project_header_menu_name = project_header_menu_name
        self.global_menu_name = global_menu_name
        self.project_menu_open = False
        self.semantic_name_reads = {number: 0 for number in range(1, 7)}
        self.semantic_name_reads_at_project_download = []
        self.rename_response_text = rename_response_text
        self.in_clip_editor = False
        self.hovered_card_index = None
        self.card_menu_open = False
        self.active_card_index = None
        self.pending_card_delete_index = None
        self.generate_clicked = False
        self.agent_click_count = 0
        self.fill_agent_pressed_states = []
        self._content_index = 0

    def _remove_active_card(self):
        if self.active_card_index is None:
            return
        if self.active_card_index < len(self.clip_names):
            self.clip_names.pop(self.active_card_index)
        self.active_card_index = None
        self.inventory_sequence = [len(self.clip_names)] * 3
        self.last_inventory_count = len(self.clip_names)

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if url.rstrip("/").endswith("/tools/flow"):
            self.url = url
            self.on_home = True
            self.progress_html = list(self.home_progress_html)
        else:
            self.url = self.project_navigation_url or url
            self.on_home = False
            self.progress_html = list(self.project_progress_html)
        self._content_index = 0

    def reload(self, **kwargs):
        self.reload_calls.append(kwargs)
        if self.reload_progress_html:
            self.progress_html = self.reload_progress_html.pop(0)
            self._content_index = 0
        if self.reload_state_updates:
            for field, value in self.reload_state_updates.pop(0).items():
                setattr(self, field, value)
            self.last_media_list_available = self.media_list_available
            self.last_empty_state = self.empty_state_available
        if self.pending_clip_names is not None:
            self.clip_names = self.pending_clip_names
            self.pending_clip_names = None

    def content(self):
        index = min(self._content_index, len(self.progress_html) - 1)
        html = self.progress_html[index]
        self._content_index += 1
        if self.session_ready and not self.on_home:
            html += '<button aria-label="Agent">Agent</button><textarea aria-label="Prompt"></textarea>'
        html += self.rename_response_text
        return html

    def evaluate(self, expression):
        assert expression == "document.readyState"
        if self.evaluate_errors:
            raise self.evaluate_errors.pop(0)
        if self.document_ready_sequence:
            self.document_ready = self.document_ready_sequence.pop(0)
        return "complete" if self.document_ready else "interactive"

    def locator(self, selector):
        if (
            selector
            == '[data-testid="virtuoso-item-list"]:visible '
            '[role="button"][tabindex="0"]'
        ):
            return FakeLocator(self, "media_cards")
        if selector == '[data-testid="virtuoso-item-list"]:visible':
            return FakeLocator(self, "media_list")
        if selector == '[data-slate-editor="true"][role="textbox"]:visible':
            if len(self.fallback_composer_x_positions) == 1:
                return FakeLocator(self, "fallback_prompt", index=0)
            return FakeLocator(self, "fallback_prompts")
        if selector == '[aria-busy="true"]:visible':
            return FakeLocator(self, "busy")
        if selector == "img[alt]":
            return FakeLocator(self, "generated_images")
        if selector == "video:visible":
            return FakeLocator(self, "videos")
        raise AssertionError(f"unexpected page locator: {selector}")

    def get_by_role(self, role, *, name=None, exact=None):
        del exact
        pattern = getattr(name, "pattern", str(name)).lower()
        if role in {"link", "button"} and "create with google flow" in pattern:
            return FakeLocator(self, "launch")
        if role == "button" and "download product clips" in pattern:
            return FakeLocator(self, "bulk_download")
        if role == "button" and "more_vert" in pattern:
            if self.project_header_menu_name is None:
                return FakeLocator(self, "project_menu")

            def matches(accessible_name):
                return bool(name.search(accessible_name))

            if matches(self.project_header_menu_name):
                return FakeLocator(self, "project_menu")
            if self.global_menu_name and matches(self.global_menu_name):
                return FakeLocator(self, "global_menu")
            return FakeLocator(self, "missing")
        if role == "button" and (
            "thumb_up" in pattern or "good response" in pattern or "คำตอบดี" in pattern
        ):
            return FakeLocator(self, "agent_response_feedback")
        if role == "button" and (
            "back to project" in pattern or "กลับไปที่โปรเจ็กต์" in pattern
        ):
            return FakeLocator(self, "back_to_project")
        if role == "button" and "agent" in pattern:
            return FakeLocator(self, "agent_role")
        if role == "button" and ("all media" in pattern or "สื่อทั้งหมด" in pattern):
            return FakeLocator(self, "media_control")
        if role == "progressbar":
            return FakeLocator(self, "progressbar")
        if role == "button" and "generate" in pattern:
            return FakeLocator(self, "generate")
        if role == "button" and "download" in pattern:
            return FakeLocator(self, "downloads")
        if role == "menuitem" and ("delete" in pattern or "ลบ" in pattern):
            return FakeLocator(self, "card_menu_delete")
        if role == "menuitem" and (
            "download project" in pattern or "ดาวน์โหลดโปรเจ็กต์" in pattern
        ):
            return FakeLocator(self, "project_download")
        if role == "checkbox":
            return FakeLocator(self, "checkboxes")
        if role == "button" and ("delete" in pattern or "ลบ" in pattern):
            if self.active_card_index is not None:
                return FakeLocator(self, "card_delete")
            return FakeLocator(self, "delete_selected")
        if role == "dialog":
            return FakeLocator(self, "dialog")
        raise AssertionError(f"unexpected role lookup: {role} {pattern}")

    def get_by_text(self, text, *, exact=None):
        del exact
        pattern = getattr(text, "pattern", str(text)).lower()
        if pattern == "agent":
            return FakeLocator(self, "agent_text")
        if "start creating" in pattern or "เริ่มสร้าง" in pattern:
            return FakeLocator(self, "empty_state")
        semantic = re.fullmatch(r"clip\s+([1-6])", pattern)
        if semantic is not None:
            return FakeLocator(self, "semantic_name", index=int(semantic.group(1)))
        raise AssertionError(f"unexpected text lookup: {pattern}")

    def get_by_label(self, name):
        assert "prompt" in str(name).lower()
        return FakeLocator(
            self,
            (
                "default_prompt"
                if self.default_prompt_visible
                else "prompt"
                if self.prompt_label_available
                else "missing"
            ),
        )

    def expect_download(self):
        return FakeDownloadExpectation(self)


class PopupButton:
    def __init__(self, page, name):
        self.page = page
        self.name = str(name)

    def count(self):
        return int(self.page.visible and self.name in self.page.buttons)

    def is_visible(self):
        return bool(self.count())

    def is_enabled(self):
        return bool(self.count())

    def click(self):
        assert self.count() == 1
        self.page.clicked.append(self.name)
        if self.page.dismisses_on_click:
            self.page.visible = False


class PopupDialog:
    def __init__(self, page):
        self.page = page

    def count(self):
        return int(self.page.visible)

    def is_visible(self):
        return self.page.visible

    def inner_text(self):
        return self.page.text

    def locator(self, selector):
        assert "input:visible" in selector
        return SimpleNamespace(count=lambda: self.page.visible_input_count)

    def get_by_role(self, role, *, name=None, exact=None):
        assert role == "button"
        assert exact is True
        return PopupButton(self.page, name)


class PopupPage:
    def __init__(
        self,
        *,
        text: str,
        buttons: set[str],
        dismisses_on_click: bool = True,
        visible_input_count: int = 0,
    ):
        self.text = text
        self.buttons = buttons
        self.dismisses_on_click = dismisses_on_click
        self.visible_input_count = visible_input_count
        self.visible = True
        self.clicked: list[str] = []

    def get_by_role(self, role, **_kwargs):
        assert role == "dialog"
        return PopupDialog(self)


class FakeCdpSession:
    def __init__(self, page):
        self.page = page

    def send(self, method, params=None):
        if method == "DOM.describeNode":
            backend_node_id = str((params or {}).get("backendNodeId", ""))
            card = next(
                (
                    candidate
                    for candidate in self.page.active_completed_video_poll
                    if str(candidate["fingerprint"]) == backend_node_id
                ),
                None,
            )
            if card is None:
                return {"node": {"nodeName": "DIV", "children": []}}
            children = []
            if card.get("has_video_element", True):
                children.append({"nodeName": "VIDEO"})
            return {"node": {"nodeName": "DIV", "children": children}}

        assert method == "Accessibility.getFullAXTree"
        if self.page.completed_video_polls:
            self.page.active_completed_video_poll = list(
                self.page.completed_video_polls.pop(0)
            )

        nodes = []
        for card in self.page.active_completed_video_poll:
            media_type = card["media_type"]
            if media_type == "video":
                name = "Video cover play_circle"
            elif media_type == "image":
                name = "Generated image"
            else:
                name = "Unknown media output"
            description = card.get("description", "Duration 00:10")
            if card.get("processing"):
                description = "Processing"
            if card.get("failed"):
                description = "Failed"
            nodes.append(
                {
                    "backendDOMNodeId": card["fingerprint"],
                    "role": {"value": "button"},
                    "name": {"value": name},
                    "description": {"value": description},
                    "properties": [
                        {"name": "disabled", "value": {"value": not card.get("playable", True)}},
                        {"name": "busy", "value": {"value": card.get("busy", False)}},
                    ],
                }
            )
        return {"nodes": nodes}

    def detach(self):
        return None


class FakeCdpContext:
    def __init__(self, page):
        self.page = page

    def new_cdp_session(self, page):
        assert page is self.page
        return FakeCdpSession(page)


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.page = page

    def new_page(self):
        return self.page


class FakeBrowserManager:
    def __init__(self, page):
        self.page = page
        self.open_calls = []
        self.context_is_open = False

    @contextmanager
    def open(self, service, *, headed=None, lock_timeout_seconds=None):
        self.open_calls.append((service, headed, lock_timeout_seconds))
        self.context_is_open = True
        try:
            yield FakeContext(self.page)
        finally:
            self.context_is_open = False


class FakeSessionManager:
    def __init__(self):
        self.calls = []

    def ensure_service_ready(self, service, job_id):
        self.calls.append((service, job_id))
        return object()


def _job(*, flow_generation_unresolved=False):
    return SimpleNamespace(
        id="job-123",
        master_prompt="Create six chronological videos about Saturn's hexagon.",
        flow_generation_unresolved=flow_generation_unresolved,
    )


def _client(
    page,
    *,
    timeout_seconds=30.0,
    workspace_lock_timeout_seconds=None,
    editor_ready_timeout_seconds=None,
    settled_poll_count=None,
):
    client_cls = getattr(google_flow, "GoogleFlowClient", None)
    assert client_cls is not None, "Task 8 Google Flow production client is not implemented"
    sessions = FakeSessionManager()
    kwargs = {
        "service_url": "https://labs.google/fx/tools/flow/project/demo",
        "generation_timeout_seconds": timeout_seconds,
        "poll_seconds": 0.0,
        "expected_width": 1080,
        "expected_height": 1920,
    }
    if workspace_lock_timeout_seconds is not None:
        kwargs["workspace_lock_timeout_seconds"] = workspace_lock_timeout_seconds
    if editor_ready_timeout_seconds is not None:
        kwargs["editor_ready_timeout_seconds"] = editor_ready_timeout_seconds
    if settled_poll_count is not None:
        kwargs["settled_poll_count"] = settled_poll_count
    client = client_cls(FakeBrowserManager(page), sessions, **kwargs)
    return client, sessions


def test_google_flow_popup_guard_dismisses_safe_announcement_before_editor_check():
    client, _ = _client(FakePage(progress_html=["<div>Ready</div>"]))
    popup = PopupPage(
        text="Flow has added a new 360p option for Gemini Omni Flash.",
        buttons={"เริ่มต้นใช้งาน"},
    )

    client._dismiss_safe_announcement_dialog(popup)

    assert popup.clicked == ["เริ่มต้นใช้งาน"]
    assert popup.visible is False


def test_google_flow_popup_guard_requires_human_for_payment_dialog_even_with_safe_button():
    client, _ = _client(FakePage(progress_html=["<div>Ready</div>"]))
    popup = PopupPage(
        text="Payment required before you can continue.",
        buttons={"Get started"},
    )

    with pytest.raises(HumanRequiredError, match="blocking dialog"):
        client._dismiss_safe_announcement_dialog(popup)

    assert popup.clicked == []


def test_google_flow_popup_guard_requires_human_when_safe_click_does_not_close_dialog():
    client, _ = _client(FakePage(progress_html=["<div>Ready</div>"]))
    popup = PopupPage(
        text="New features are available in Google Flow.",
        buttons={"Get started"},
        dismisses_on_click=False,
    )

    with pytest.raises(HumanRequiredError, match="did not close"):
        client._dismiss_safe_announcement_dialog(popup)

    assert popup.clicked == ["Get started"]


def test_google_flow_workspace_context_uses_browser_configuration_and_holds_lock():
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, sessions = _client(page, timeout_seconds=45.0)
    browser = client.browser

    acquire_workspace = getattr(client, "acquire_workspace", None)
    assert acquire_workspace is not None, "Flow workspace context is not implemented"
    with acquire_workspace(_job()) as workspace:
        assert browser.context_is_open is True
        assert workspace.page is page
        assert sessions.calls == []

    assert browser.context_is_open is False
    assert browser.open_calls == [("google_flow", None, 45.0)]
    assert page.goto_calls == [
        (
            "https://labs.google/fx/tools/flow",
            {"wait_until": "domcontentloaded"},
        ),
        (
            "https://labs.google/fx/tools/flow/project/demo",
            {"wait_until": "domcontentloaded"},
        )
    ]


def test_google_flow_workspace_lock_timeout_can_differ_from_generation_timeout():
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, _ = _client(
        page,
        timeout_seconds=45.0,
        workspace_lock_timeout_seconds=90.0,
    )

    with client.acquire_workspace(_job()):
        pass

    assert client.browser.open_calls == [("google_flow", None, 90.0)]


def test_google_flow_workspace_enters_project_from_observable_landing_control():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        landing=True,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page

    assert ("click", "launch") in page.actions
    assert page.landing is False


def test_google_flow_workspace_accepts_text_backed_agent_and_empty_media_state():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        agent_available=False,
        agent_text_available=True,
        clip_names=[],
        media_control_available=False,
        media_list_available=False,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert not any(action[0] == "click" for action in page.actions)


def test_google_flow_workspace_requires_observable_project_editor(monkeypatch):
    page = FakePage(
        progress_html=["<div>Loading</div>"],
        agent_available=False,
    )
    client, _ = _client(page, timeout_seconds=1.0)
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
        with client.acquire_workspace(_job()):
            pass


def test_google_flow_editor_readiness_timeout_is_independent_from_generation(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Loading</div>"],
        agent_available=False,
        document_ready=False,
        media_control_available=False,
        media_list_available=False,
    )
    client, _ = _client(
        page,
        timeout_seconds=1800.0,
        editor_ready_timeout_seconds=1.0,
    )
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
        with client.acquire_workspace(_job()):
            pass


def test_google_flow_editor_readiness_retries_destroyed_navigation_context():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        evaluate_errors=[
            PlaywrightError(
                "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
            )
        ],
    )
    # The reconciliation inventory itself needs several bounded observations;
    # leave enough simulated time for that, then let the rename postcondition
    # time out without a real sleep.
    client, _ = _client(page, editor_ready_timeout_seconds=5.0)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page


def test_google_flow_editor_requires_consecutive_healthy_observations(monkeypatch):
    page = FakePage(progress_html=["<div>Ready</div>"])
    client, _ = _client(page, settled_poll_count=3)
    observations = iter([True, False, True, True, True])
    calls = []

    def actionable(_page):
        calls.append("checked")
        return next(observations)

    monkeypatch.setattr(client, "_is_editor_actionable", actionable)

    client._wait_for_settled_editor(page)

    assert calls == ["checked"] * 5


def test_google_flow_fatal_application_error_is_not_actionable():
    page = FakePage(
        progress_html=[
            "<main>Application error: a client-side exception has occurred "
            "(reading 'service')</main>"
        ]
    )
    client, _ = _client(page, editor_ready_timeout_seconds=1.0)

    assert client._is_editor_actionable(page) is False


def test_google_flow_healthy_direct_project_does_not_reload():
    page = FakePage(progress_html=["<div>Ready</div>"])
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page

    assert page.reload_calls == []
    assert client.browser.open_calls == [("google_flow", None, 30.0)]


def test_google_flow_direct_fatal_recovers_with_one_same_page_reload():
    page = FakePage(
        progress_html=["<main>Application error: a client-side exception has occurred</main>"],
        reload_progress_html=[["<div>Ready</div>"]],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert client.browser.open_calls == [("google_flow", None, 30.0)]
    assert page.actions == []


def test_google_flow_direct_fatal_recovers_with_two_same_page_reloads():
    page = FakePage(
        progress_html=["<main>Application error: a client-side exception has occurred</main>"],
        reload_progress_html=[
            ["<main>Application error: a client-side exception has occurred</main>"],
            ["<div>Ready</div>"],
        ],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}] * 2
    assert client.browser.open_calls == [("google_flow", None, 30.0)]


def test_google_flow_persistent_direct_fatal_fails_after_two_reloads():
    page = FakePage(
        progress_html=["<main>Application error: a client-side exception has occurred</main>"],
        reload_progress_html=[
            ["<main>Application error: a client-side exception has occurred</main>"],
            ["<main>Application error: a client-side exception has occurred</main>"],
        ],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
        with client.acquire_workspace(_job()):
            pass

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}] * 2
    assert page.actions == []


def test_google_flow_reconciliation_persistent_fatal_fails_after_two_reloads():
    page = FakePage(
        progress_html=["<main>Application error: a client-side exception has occurred</main>"],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
        with client.acquire_workspace(_job(flow_generation_unresolved=True)):
            pass

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}] * 2
    assert page.actions == []


def test_google_flow_workspace_warms_home_and_hydrates_project_in_one_context():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        home_progress_html=["<main>Flow home</main>"],
    )
    client, sessions = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page
        assert client.browser.context_is_open is True

    assert sessions.calls == []
    assert client.browser.open_calls == [("google_flow", None, 30.0)]
    assert [url for url, _ in page.goto_calls] == [
        "https://labs.google/fx/tools/flow",
        "https://labs.google/fx/tools/flow/project/demo",
    ]


def test_fenced_workspace_recovers_initial_empty_editor_with_same_page_reload():
    page = FakePage(
        progress_html=["<div>Loading media inventory</div>"],
        clip_names=[f"clip {number}" for number in range(1, 7)],
        agent_available=False,
        agent_text_available=False,
        media_list_available=False,
        empty_state_available=False,
        reload_state_updates=[
            {
                "agent_available": True,
                "agent_text_available": True,
                "media_list_available": True,
                "empty_state_available": True,
            }
        ],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with client.acquire_workspace(_job(flow_generation_unresolved=True)):
        pass

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "generate") not in page.actions
    assert not any(action[0] == "fill" for action in page.actions)
    assert not any(action[1] == "media_card" for action in page.actions)
    assert not any("delete" in str(action) for action in page.actions)


def test_fenced_workspace_recovers_nonfatal_unclassified_project_after_reload():
    page = FakePage(
        progress_html=["<div>Loading media inventory</div>"],
        clip_names=[f"clip {number}" for number in range(1, 7)],
        session_ready=False,
        agent_available=False,
        agent_text_available=False,
        media_list_available=False,
        empty_state_available=False,
        reload_state_updates=[
            {
                "session_ready": True,
                "agent_available": True,
                "agent_text_available": True,
                "media_list_available": True,
                "empty_state_available": True,
            }
        ],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with client.acquire_workspace(_job(flow_generation_unresolved=True)):
        pass

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert page.actions == []


def test_fenced_workspace_recovers_transient_fatal_editor_with_same_page_reload():
    page = FakePage(
        progress_html=[
            "<main>Application error: a client-side exception has occurred "
            "(reading 'service')</main>"
        ],
        reload_progress_html=[["<div>Ready</div>"]],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with client.acquire_workspace(_job(flow_generation_unresolved=True)):
        pass

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert page.actions == []


def test_fenced_workspace_recovers_after_second_same_page_reload():
    page = FakePage(
        progress_html=["<div>Loading media inventory</div>"],
        clip_names=[f"clip {number}" for number in range(1, 7)],
        agent_available=False,
        agent_text_available=False,
        media_list_available=False,
        empty_state_available=False,
        reload_state_updates=[
            {},
            {
                "agent_available": True,
                "agent_text_available": True,
                "media_list_available": True,
                "empty_state_available": True,
            },
        ],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with client.acquire_workspace(_job(flow_generation_unresolved=True)):
        pass

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}] * 2
    assert page.actions == []


def test_fenced_workspace_persistent_empty_fails_after_two_same_page_reloads():
    page = FakePage(
        progress_html=["<div>Loading media inventory</div>"],
        agent_available=False,
        agent_text_available=False,
        media_list_available=False,
        empty_state_available=False,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.02)

    with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
        with client.acquire_workspace(_job(flow_generation_unresolved=True)):
            pass

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}] * 2
    assert page.actions == []


def test_google_flow_workspace_detects_session_expiry_on_its_owned_project_page():
    page = FakePage(
        progress_html=["<main>Sign in</main>"],
        project_navigation_url="https://accounts.google.com/v3/signin/identifier",
    )
    client, sessions = _client(page)

    with pytest.raises(HumanRequiredError, match="google_flow session requires human recovery"):
        with client.acquire_workspace(_job()):
            pass

    assert sessions.calls == []
    assert client.browser.open_calls == [("google_flow", None, 30.0)]
    assert page.reload_calls == []
    assert page.actions == []


def test_google_flow_workspace_detects_security_challenge_on_its_owned_project_page():
    page = FakePage(
        progress_html=["<div>Confirm you're not a robot</div>"],
    )
    client, sessions = _client(page)

    with pytest.raises(HumanRequiredError, match="google_flow session requires human recovery"):
        with client.acquire_workspace(_job()):
            pass

    assert sessions.calls == []
    assert client.browser.open_calls == [("google_flow", None, 30.0)]
    assert page.reload_calls == []
    assert page.actions == []


def test_fresh_workspace_accepts_hydrated_observable_empty_inventory_without_reload():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[],
        media_list_available=False,
        empty_state_available=True,
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page

    assert page.reload_calls == []


def test_google_flow_normal_loading_waits_without_direct_fatal_reload():
    page = FakePage(
        progress_html=["<div>Loading media inventory</div>"],
        document_ready_sequence=[False, True, True, True],
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page

    assert page.reload_calls == []


def test_flow_workspace_transient_empty_inventory_cannot_pass_generation_gate(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        card_count_sequence=[0, 0, 2, 2],
    )
    client, _ = _client(
        page,
        timeout_seconds=1.0,
        editor_ready_timeout_seconds=1.0,
        settled_poll_count=3,
    )
    clock = iter([0.0, 0.0, 0.2, 0.4, 1.1])
    monkeypatch.setattr(google_flow.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_wait_for_settled_editor", lambda _page: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty product workspace"):
            workspace.prepare_for_generation()

    assert ("click", "generate") not in page.actions


def test_flow_workspace_stable_settled_empty_inventory_passes_generation_gate():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        card_count_sequence=[0, 0, 0],
    )
    client, _ = _client(page, settled_poll_count=3)

    with client.acquire_workspace(_job()) as workspace:
        workspace.prepare_for_generation()

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "generate") not in page.actions


def test_flow_workspace_preclean_deletes_stale_clips_then_verifies_empty():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["stale-a", "stale-b", "stale-c"],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        preclean = getattr(workspace, "preclean_and_verify_empty", None)
        assert preclean is not None, "Flow workspace pre-clean is not implemented"
        preclean()

    assert page.actions == [
        ("hover", "media_card", 0),
        ("click", "card_menu", 0),
        ("click", "card_menu_delete"),
        ("hover", "media_card", 0),
        ("click", "card_menu", 0),
        ("click", "card_menu_delete"),
        ("hover", "media_card", 0),
        ("click", "card_menu", 0),
        ("click", "card_menu_delete"),
    ]
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert page.clip_names == []


def test_flow_workspace_preclean_deletes_card_inventory_when_checkboxes_are_absent():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["generated-image-a", "generated-image-b"],
        checkbox_count=0,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.clip_names == []
    assert page.actions.count(("click", "card_menu_delete")) == 2
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]


def test_flow_workspace_deletes_from_the_hovered_card_overflow_menu():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["stale-a", "stale-b"],
        checkbox_count=0,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        workspace._delete_one_current_media_card()

    assert page.clip_names == ["stale-b"]
    assert page.actions[:3] == [
        ("hover", "media_card", 0),
        ("click", "card_menu", 0),
        ("click", "card_menu_delete"),
    ]
    assert not any(action[:2] == ("click", "media_card") for action in page.actions)


def test_flow_workspace_preclean_fails_closed_when_card_delete_is_unavailable():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["generated-image-a"],
        checkbox_count=0,
        card_delete_available=False,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="could not be deleted"):
            workspace.preclean_and_verify_empty()

    assert page.clip_names == ["generated-image-a"]
    assert ("click", "generate") not in page.actions


def test_flow_workspace_preclean_fails_closed_when_card_delete_does_not_remove_card():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["generated-image-a"],
        checkbox_count=0,
        card_delete_removes=False,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="removal"):
            workspace.preclean_and_verify_empty()

    assert page.clip_names == ["generated-image-a"]
    assert page.actions == [
        ("hover", "media_card", 0),
        ("click", "card_menu", 0),
        ("click", "card_menu_delete"),
    ]
    assert ("click", "generate") not in page.actions


def test_flow_workspace_preclean_confirms_card_delete_then_waits_for_removal():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["generated-image-a", "generated-video-b"],
        checkbox_count=0,
        delete_requires_confirmation=True,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.clip_names == []
    assert page.actions.count(("click", "card_menu_delete")) == 2
    assert page.actions.count(("click", "confirm_delete")) == 2


def test_flow_workspace_post_ready_cleanup_uses_card_deletion_without_checkboxes():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["generated-image-a", "generated-video-b"],
        checkbox_count=0,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=0.1)

    with client.acquire_workspace(_job()) as workspace:
        workspace.cleanup_and_verify_empty()

    assert page.clip_names == []
    assert page.actions.count(("click", "card_menu_delete")) == 2


def test_flow_workspace_preclean_refreshes_even_when_already_empty():
    page = FakePage(progress_html=["<div>Ready</div>"], clip_names=[])
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert not any(action[0] == "click" for action in page.actions)


def test_flow_workspace_preclean_rehydrates_after_the_post_delete_reload(
    monkeypatch,
):
    page = FakePage(progress_html=["<div>Ready</div>"], clip_names=[])
    client, _ = _client(page)
    hydrate_calls = []
    original_hydrate = client._hydrate_project_workspace

    def hydrate_after_reload(current_page, *, flow_generation_unresolved):
        hydrate_calls.append((current_page, flow_generation_unresolved))
        return original_hydrate(
            current_page,
            flow_generation_unresolved=flow_generation_unresolved,
        )

    monkeypatch.setattr(client, "_hydrate_project_workspace", hydrate_after_reload)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert hydrate_calls == [(page, False), (page, False)]


def test_flow_workspace_preclean_confirms_observable_delete_dialog():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["stale-a", "stale-b"],
        delete_requires_confirmation=True,
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.actions.count(("click", "card_menu_delete")) == 2
    assert page.actions.count(("click", "confirm_delete")) == 2
    assert page.clip_names == []


def test_flow_workspace_preclean_blocks_generation_when_inventory_is_unverifiable(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[],
        empty_state_available=False,
        media_list_sequence=[True, False, False],
    )
    client, _ = _client(page, timeout_seconds=1.0, settled_poll_count=1)
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty"):
            workspace.preclean_and_verify_empty()

    assert ("click", "generate") not in page.actions


def test_flow_workspace_preclean_waits_for_stable_empty_inventory_after_reload():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[],
        card_count_sequence=[0, 2, 0, 0, 0],
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.card_count_sequence == []


def test_flow_generation_does_not_implicitly_preclean_remote_workspace(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=["stale-remote-asset"],
        generation_completion_names=[f"draft-{number}" for number in range(1, 7)],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job()) as workspace:
        workspace.generate_and_download(_job(), paths)

    assert not any(action[0] == "check" for action in page.actions)
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_renames_out_of_order_results_and_downloads_project_archive(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=[
            "<div>Generation progress 2 / 6</div>",
            "<div>Generation progress 6 / 6</div>",
        ],
        generation_completion_names=[
            "draft-4",
            "draft-1",
            "draft-6",
            "draft-2",
            "draft-5",
            "draft-3",
        ],
        renamed_clip_names=[
            "clip 4",
            "clip 1",
            "clip 6",
            "clip 2",
            "clip 5",
            "clip 3",
        ],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    materializer_calls = []

    def materialize(archive_path, job_paths, **kwargs):
        materializer_calls.append((Path(archive_path), job_paths, kwargs))
        return job_paths.flow_files

    monkeypatch.setattr(google_flow, "materialize_flow_archive", materialize, raising=False)

    with client.acquire_workspace(_job()) as workspace:
        generate = getattr(workspace, "generate_and_download", None)
        assert generate is not None, "Shared Flow generation is not implemented"
        result = generate(_job(), paths)

    assert result == paths.flow_files
    assert ("fill", "prompt", _job().master_prompt) in page.actions
    assert (
        "fill",
        "prompt",
        "เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ",
    ) in page.actions
    assert page.clip_names == [
        "clip 4",
        "clip 1",
        "clip 6",
        "clip 2",
        "clip 5",
        "clip 3",
    ]
    expect_index = page.actions.index(("expect_download", "registered"))
    click_index = page.actions.index(("click", "project_download"))
    assert expect_index < click_index
    assert page.download_attempts == [1, 0, 0, 0, 0, 0]
    assert materializer_calls == [
        (
            paths.flow_archive_file,
            paths,
            {
                "min_size_bytes": 1,
                "expected_width": 1080,
                "expected_height": 1920,
            },
        )
    ]


def test_flow_workspace_reconciles_existing_six_without_new_generation(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job()) as workspace:
        result = workspace.reconcile_and_download(_job(), paths)

    assert result == paths.flow_files
    assert not any(
        action[:3] == ("fill", "prompt", _job().master_prompt)
        for action in page.actions
    )
    assert not any(action[0] == "check" for action in page.actions)
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_reconciliation_skips_rename_when_semantic_names_exist(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert not any(
        action[:3] == ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
        for action in page.actions
    )
    assert ("click", "project_download") in page.actions


def test_flow_workspace_refuses_project_download_when_semantic_names_are_absent_after_rename_refresh(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"draft-{number}" for number in range(1, 7)],
        checkbox_count=0,
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(
        page,
        timeout_seconds=1.0,
        editor_ready_timeout_seconds=5.0,
    )
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        with pytest.raises(
            FlowWorkspaceVerificationError,
            match="^Google Flow semantic clip names could not be verified$",
        ):
            workspace.reconcile_and_download(
                _job(flow_generation_unresolved=True),
                paths,
            )

    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_menu") not in page.actions
    assert ("click", "project_download") not in page.actions
    assert ("click", "bulk_download") not in page.actions


def test_flow_workspace_reuses_existing_rename_confirmation_without_resubmitting(
    monkeypatch, tmp_path
):
    complete_names = [f"clip {number}" for number in range(1, 7)]
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        checkbox_count=0,
        reload_state_updates=[{"clip_names": complete_names}],
        inventory_sequence=[6, 6, 6],
        rename_response_text=(
            "ผมได้เปลี่ยนชื่อคลิปทั้ง 6 เป็น CLIP 1 ถึง CLIP 6 "
            "ตามชื่อที่คุณระบุไว้เรียบร้อยแล้วครับ"
        ),
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert not any(
        action[:3] == ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
        for action in page.actions
    )
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_download") in page.actions


def test_project_download_selector_accepts_observed_icon_prefixed_thai_name():
    assert google_flow._PROJECT_DOWNLOAD_NAME_RE.fullmatch(
        "download ดาวน์โหลดโปรเจ็กต์"
    )


@pytest.mark.parametrize(
    "project_header_menu_name",
    [
        "more_vert More options",
        "more_vert ตัวเลือกเพิ่มเติม",
    ],
)
def test_project_archive_uses_header_menu_not_global_account_menu(
    tmp_path, project_header_menu_name
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        project_header_menu_name=project_header_menu_name,
        global_menu_name="more_vert เพิ่มเติม",
    )
    client, _ = _client(page)
    destination = tmp_path / "project.zip"

    google_flow.FlowWorkspaceRun(client, page)._download_project_archive_to(destination)

    assert ("click", "project_menu") in page.actions
    assert ("click", "global_menu") not in page.actions
    assert ("click", "project_download") in page.actions


def test_rename_response_reloads_once_then_requires_two_stable_semantic_observations_without_checkboxes(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        checkbox_count=0,
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert page.get_by_role("checkbox").count() == 0
    assert all(page.semantic_name_reads[number] >= 2 for number in range(1, 7))
    assert ("click", "project_download") in page.actions


def test_rename_refuses_project_download_when_semantic_names_stay_incomplete(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[
            "clip 1",
            "clip 2",
            "clip 3",
            "clip 4",
            "clip 5",
            "draft-6",
        ],
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        with pytest.raises(
            FlowWorkspaceVerificationError,
            match="semantic clip names could not be verified",
        ):
            workspace.reconcile_and_download(
                _job(flow_generation_unresolved=True),
                paths,
            )

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_menu") not in page.actions
    assert ("click", "project_download") not in page.actions


def test_partial_inventory_refresh_waits_for_five_stable_semantic_names_without_checkboxes(
    monkeypatch, tmp_path
):
    survivor_names = ["clip 1", "clip 2", "clip 4", "clip 5", "clip 6"]
    page = FakePage(
        progress_html=["<div>Generation progress 5 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 6)],
        renamed_clip_names=survivor_names,
        checkbox_count=0,
        inventory_sequence=[5, 5, 5],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    materialization = google_flow.FlowRecoveryMaterialization(
        paths=paths.flow_files,
        source="complete_archive",
    )
    monkeypatch.setattr(
        google_flow,
        "inspect_recovery_flow_archive",
        lambda *_args, **_kwargs: materialization,
    )

    _timeout_clock(monkeypatch)
    result = google_flow.FlowWorkspaceRun(client, page).capture_partial_inventory(
        paths,
        attempt=0,
    )

    assert result is materialization
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert page.get_by_role("checkbox").count() == 0
    assert all(
        page.semantic_name_reads[number] >= 2
        for number in (1, 2, 4, 5, 6)
    )
    assert ("click", "project_download") in page.actions


def test_partial_inventory_refresh_accepts_six_complete_semantic_names(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        checkbox_count=0,
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    materialization = google_flow.FlowRecoveryMaterialization(
        paths=paths.flow_files,
        source="complete_archive",
    )
    monkeypatch.setattr(
        google_flow,
        "inspect_recovery_flow_archive",
        lambda *_args, **_kwargs: materialization,
    )

    _timeout_clock(monkeypatch)
    result = google_flow.FlowWorkspaceRun(client, page).capture_partial_inventory(
        paths,
        attempt=0,
    )

    assert result is materialization
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert all(page.semantic_name_reads[number] >= 2 for number in range(1, 7))
    assert ("click", "project_download") in page.actions


def test_flow_workspace_ignores_agent_history_semantic_names_outside_media_cards(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        page_semantic_names=[f"clip {number}" for number in range(1, 7)],
        renamed_clip_names=[f"draft-{number}" for number in range(1, 7)],
        checkbox_count=0,
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
        rename_response_text=(
            "Agent history: renamed all six clips from clip 1 through clip 6."
        ),
    )
    client, _ = _client(page, timeout_seconds=1.0, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        with pytest.raises(
            FlowWorkspaceVerificationError,
            match="^Google Flow semantic clip names could not be verified$",
        ):
            workspace.reconcile_and_download(
                _job(flow_generation_unresolved=True),
                paths,
            )

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_menu") not in page.actions
    assert ("click", "project_download") not in page.actions


def test_flow_workspace_rename_completes_from_semantic_names_when_send_stays_disabled(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_download") in page.actions


def test_flow_workspace_waits_for_agent_response_before_single_rename_refresh(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert page.agent_response_reads >= 2
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]


def test_flow_workspace_rename_ignores_stale_submit_locator_after_click(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
        send_stale_after_click=True,
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert not any(
        action[:3] == ("fill", "prompt", _job().master_prompt)
        for action in page.actions
    )
    assert ("click", "project_download") in page.actions
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_rename_stale_submit_locator_refuses_download_when_semantic_names_are_incomplete(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=["clip 1", "clip 2", "clip 3", "clip 4", "clip 5", "draft-6"],
        inventory_sequence=[6, 6, 6],
        send_stale_after_click=True,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        with pytest.raises(
            FlowWorkspaceVerificationError,
            match="^Google Flow semantic clip names could not be verified$",
        ):
            workspace.reconcile_and_download(
                _job(flow_generation_unresolved=True),
                paths,
            )

    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert not any(
        action[:3] == ("fill", "prompt", _job().master_prompt)
        for action in page.actions
    )
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_menu") not in page.actions
    assert ("click", "project_download") not in page.actions
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_rename_refuses_download_when_names_are_not_rendered_after_refresh(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 6)],
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        with pytest.raises(
            FlowWorkspaceVerificationError,
            match="^Google Flow semantic clip names could not be verified$",
        ):
            workspace.reconcile_and_download(
                _job(flow_generation_unresolved=True),
                paths,
            )

    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_menu") not in page.actions
    assert ("click", "project_download") not in page.actions


@pytest.mark.parametrize(
    "renamed_clip_names",
    [
        ["clip 1", "clip 2", "clip 3", "clip 4", "clip 5", "draft-6"],
        ["clip 1", "clip 2", "clip 3", "clip 3", "clip 5", "clip 6"],
    ],
)
def test_flow_workspace_rename_refuses_download_when_semantic_names_stay_incomplete(
    monkeypatch, tmp_path, renamed_clip_names
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=renamed_clip_names,
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        with pytest.raises(
            FlowWorkspaceVerificationError,
            match="^Google Flow semantic clip names could not be verified$",
        ):
            workspace.reconcile_and_download(
                _job(flow_generation_unresolved=True),
                paths,
            )

    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert not any(
        action[:3] == ("fill", "prompt", _job().master_prompt)
        for action in page.actions
    )
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "project_menu") not in page.actions
    assert ("click", "project_download") not in page.actions
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_rename_recovers_hydration_in_one_context_after_reload(
    monkeypatch, tmp_path
):
    complete_names = [f"clip {number}" for number in range(1, 7)]
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        reload_progress_html=[
            ["<main>Application error: a client-side exception has occurred</main>"],
            ["<div>Ready</div>"],
        ],
        reload_state_updates=[{}, {"clip_names": complete_names}],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=["clip 1", "clip 2", "clip 3", "clip 4", "clip 5", "draft-6"],
        inventory_sequence=[6, 6, 6],
        agent_enabled_states=[False],
        send_enabled_after_click=False,
    )
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        _timeout_clock(monkeypatch)
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert client.browser.open_calls == [("google_flow", None, 1.0)]
    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}] * 2


def test_flow_workspace_reconciles_completed_video_cards_without_progress_or_generate(
    monkeypatch, tmp_path
):
    cards = _completed_video_cards()
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
    )
    client, _ = _client(page, timeout_seconds=5.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert not any(
        action[:3] == ("fill", "prompt", _job().master_prompt)
        for action in page.actions
    )
    assert ("click", "delete_selected") not in page.actions
    assert (
        "fill",
        "prompt",
        google_flow.RENAME_CLIPS_INSTRUCTION,
    ) in page.actions


def test_flow_workspace_reconciles_completed_hidden_video_cards_without_generate(
    monkeypatch, tmp_path
):
    cards = _completed_video_cards(video_visible=False, ready_state=0)
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
    )
    client, _ = _client(page, timeout_seconds=5.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job(flow_generation_unresolved=True)) as workspace:
        result = workspace.reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert result == paths.flow_files
    assert not any(
        action[:3] == ("fill", "prompt", _job().master_prompt)
        for action in page.actions
    )
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_partial_reconciliation_retains_remote_results(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 2 / 6</div>"],
        clip_names=["partial-a", "partial-b"],
        inventory_sequence=[2, 2, 2],
    )
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowGenerationTimeoutError, match="timed out"):
            workspace.reconcile_and_download(_job(), paths)

    assert not any(action[0] == "check" for action in page.actions)
    assert ("click", "delete_selected") not in page.actions
    assert ("click", "generate") not in page.actions


def test_flow_workspace_reconciliation_requires_stable_exact_six_before_rename(
    monkeypatch,
    tmp_path,
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=["partial-a", "partial-b"],
        inventory_sequence=[2, 2, 2],
    )
    client, _ = _client(
        page,
        editor_ready_timeout_seconds=1.0,
        settled_poll_count=3,
    )
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_wait_for_settled_editor", lambda _page: None)
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda *_args, **_kwargs: paths.flow_files,
    )

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="six product clips"):
            workspace.reconcile_and_download(_job(), paths)

    assert not any(
        action[:3] == ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
        for action in page.actions
    )
    assert ("click", "bulk_download") not in page.actions
    assert ("click", "delete_selected") not in page.actions
    assert ("click", "generate") not in page.actions


def test_flow_workspace_refuses_materialization_when_semantic_names_are_duplicate_after_rename(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        generation_completion_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=["clip 1", "clip 1", "clip 2", "clip 3", "clip 4", "clip 5"],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=5.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    materializer_calls = 0

    def materialize_sentinel(*_args, **_kwargs):
        nonlocal materializer_calls
        materializer_calls += 1
        return paths.flow_files

    monkeypatch.setattr(google_flow, "materialize_flow_archive", materialize_sentinel)

    with client.acquire_workspace(_job()) as workspace:
        _timeout_clock(monkeypatch)
        with pytest.raises(
            FlowWorkspaceVerificationError,
            match="^Google Flow semantic clip names could not be verified$",
        ):
            workspace.generate_and_download(_job(), paths)

    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert materializer_calls == 0
    assert ("click", "project_menu") not in page.actions
    assert ("click", "project_download") not in page.actions


def test_google_flow_agent_prompt_uses_observable_composer_when_locale_has_no_prompt_label():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        prompt_label_available=False,
    )
    client, _ = _client(page)

    client._submit_generation(page, _job().master_prompt)

    assert ("fill", "prompt", _job().master_prompt) in page.actions
    assert ("click", "generate") in page.actions


def test_settled_editor_does_not_require_a_hidden_agent_composer_before_activation(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=False,
    )
    client, _ = _client(page)
    monkeypatch.setattr(
        client,
        "_observable_composer",
        lambda _agent: pytest.fail(
            "editor readiness must not require an Agent composer before activation"
        ),
    )

    assert client._is_editor_actionable(page) is True


def test_ensure_agent_active_clicks_an_inactive_toggle_once_and_returns_composer():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=False,
    )
    client, _ = _client(page)

    composer = client._ensure_agent_active(page)

    assert page.agent_click_count == 1
    assert page.agent_pressed is True
    assert composer.prompt.kind == "prompt"


def test_ensure_agent_active_does_not_toggle_an_already_active_agent_twice():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=True,
    )
    client, _ = _client(page)

    first = client._ensure_agent_active(page)
    second = client._ensure_agent_active(page)

    assert first.prompt.kind == "prompt"
    assert second.prompt.kind == "prompt"
    assert page.agent_click_count == 0
    assert page.agent_pressed is True


def test_ensure_agent_active_uses_visible_fallback_composer_without_agent_button():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_available=False,
        agent_text_available=False,
        fallback_composer_available=True,
    )
    client, _ = _client(page)

    composer = client._ensure_agent_active(page)

    assert composer.prompt.kind == "fallback_prompt"
    assert page.agent_click_count == 0


def test_google_flow_submits_rename_from_fallback_composer_without_agent_button():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_available=False,
        agent_text_available=False,
        fallback_composer_available=True,
    )
    client, _ = _client(page)

    client._submit_agent_prompt(page, google_flow.RENAME_CLIPS_INSTRUCTION)

    assert (
        "fill",
        "fallback_prompt",
        google_flow.RENAME_CLIPS_INSTRUCTION,
    ) in page.actions
    assert ("click", "fallback_generate") in page.actions


def test_google_flow_submits_rename_from_fallback_when_agent_panel_composer_is_missing():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=True,
        agent_prompt_count=0,
        fallback_composer_available=True,
    )
    client, _ = _client(page)

    client._submit_agent_prompt(page, google_flow.RENAME_CLIPS_INSTRUCTION)

    assert (
        "fill",
        "fallback_prompt",
        google_flow.RENAME_CLIPS_INSTRUCTION,
    ) in page.actions
    assert ("click", "fallback_generate") in page.actions
    assert not any(action[0] == "fill" and action[1] == "prompt" for action in page.actions)
    assert page.actions.count(("click", "fallback_generate")) == 1


def test_google_flow_rename_uses_primary_bottom_composer_when_it_is_usable():
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, _ = _client(page)

    client._submit_agent_prompt(page, google_flow.RENAME_CLIPS_INSTRUCTION)

    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.actions.count(("click", "generate")) == 1
    assert not any(
        action[1] == "side_panel_prompt"
        for action in page.actions
        if action[0] == "fill"
    )
    assert ("click", "side_panel_generate") not in page.actions


@pytest.mark.parametrize(
    "prompt_text",
    [
        google_flow.RENAME_CLIPS_INSTRUCTION,
        google_flow.RENAME_SURVIVING_CLIPS_INSTRUCTION,
    ],
)
def test_google_flow_rename_falls_back_to_right_agent_panel_when_primary_is_ambiguous(
    prompt_text,
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_prompt_count=2,
        fallback_composer_x_positions=[400, 1200],
        require_pinned_fallback_identity=True,
    )
    client, _ = _client(page)

    client._submit_agent_prompt(page, prompt_text)

    assert page.actions.count(("fill", "side_panel_prompt", prompt_text)) == 1
    assert page.actions.count(("click", "side_panel_generate")) == 1
    assert not any(
        action[1] in {"prompt", "fallback_prompt"}
        for action in page.actions
        if action[0] == "fill"
    )
    assert ("click", "generate") not in page.actions
    assert ("click", "fallback_generate") not in page.actions


def test_google_flow_rename_uses_bottom_composer_when_right_panel_has_two_rows():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_prompt_count=2,
        fallback_composer_x_positions=[1200, 1200],
        fallback_composer_y_positions=[250, 700],
    )
    client, _ = _client(page)

    client._submit_agent_prompt(page, google_flow.RENAME_CLIPS_INSTRUCTION)

    assert page.actions.count(
        ("fill", "side_panel_prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.actions.count(("click", "side_panel_generate")) == 1
    assert not any(
        action[1] == "upper_panel_prompt"
        for action in page.actions
        if action[0] == "fill"
    )
    assert ("click", "upper_panel_generate") not in page.actions


def test_google_flow_rename_rejects_geometrically_ambiguous_fallback_composers():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_prompt_count=2,
        fallback_composer_x_positions=[1200, 1200],
        fallback_composer_y_positions=[700, 700],
    )
    client, _ = _client(page)

    with pytest.raises(FlowWorkspaceVerificationError, match="could not be verified"):
        client._submit_agent_prompt(page, google_flow.RENAME_CLIPS_INSTRUCTION)

    assert not any(action[0] == "fill" for action in page.actions)
    assert not any(action[0] == "click" for action in page.actions)


def test_google_flow_rename_prefers_primary_bottom_composer_when_both_are_usable():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        fallback_composer_x_positions=[400, 1200],
    )
    client, _ = _client(page)

    client._submit_agent_prompt(page, google_flow.RENAME_CLIPS_INSTRUCTION)

    assert page.actions.count(
        ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
    ) == 1
    assert page.actions.count(("click", "generate")) == 1
    assert not any(
        action[1] in {"fallback_prompt", "side_panel_prompt"}
        for action in page.actions
        if action[0] == "fill"
    )
    assert ("click", "fallback_generate") not in page.actions
    assert ("click", "side_panel_generate") not in page.actions


def test_google_flow_rename_fails_without_clicking_submit_when_no_composer_is_usable():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_available=False,
        agent_text_available=False,
        fallback_composer_available=False,
    )
    client, _ = _client(page)

    with pytest.raises(FlowWorkspaceVerificationError, match="could not be verified"):
        client._submit_agent_prompt(page, google_flow.RENAME_CLIPS_INSTRUCTION)

    assert not any(action[0] == "fill" for action in page.actions)
    assert ("click", "generate") not in page.actions
    assert ("click", "fallback_generate") not in page.actions


def test_ensure_agent_active_fails_closed_for_unknown_toggle_state():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=None,
    )
    client, _ = _client(page)

    with pytest.raises(FlowWorkspaceVerificationError, match="Agent state"):
        client._ensure_agent_active(page)

    assert page.agent_click_count == 0
    assert not any(action[0] == "fill" for action in page.actions)
    assert ("click", "generate") not in page.actions


def test_google_flow_submit_waits_for_delayed_agent_activation_before_prompt_fill():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=False,
        agent_activation_delay_reads=2,
    )
    client, _ = _client(page)

    client._submit_generation(page, _job().master_prompt)

    assert page.agent_click_count == 1
    assert page.fill_agent_pressed_states == [True]
    assert ("click", "generate") in page.actions


def test_google_flow_submit_fails_before_fill_when_agent_never_activates(monkeypatch):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=False,
        agent_activation_delay_reads=99,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=1.0)
    clock = iter([0.0, 1.1])
    monkeypatch.setattr(google_flow.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(FlowWorkspaceVerificationError, match="Agent activation"):
        client._submit_generation(page, _job().master_prompt)

    assert page.agent_click_count == 1
    assert not any(action[0] == "fill" for action in page.actions)
    assert ("click", "generate") not in page.actions


def test_google_flow_submit_never_fills_default_image_prompt_when_agent_composer_exists():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=True,
        default_prompt_visible=True,
    )
    client, _ = _client(page)

    client._submit_generation(page, _job().master_prompt)

    assert ("fill", "prompt", _job().master_prompt) in page.actions
    assert ("fill", "default_prompt", _job().master_prompt) not in page.actions


def test_google_flow_submit_rejects_multiple_prompt_fields_in_agent_container():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=True,
        agent_prompt_count=2,
    )
    client, _ = _client(page)

    with pytest.raises(FlowWorkspaceVerificationError, match="Agent prompt"):
        client._submit_generation(page, _job().master_prompt)

    assert not any(action[0] == "fill" for action in page.actions)
    assert ("click", "generate") not in page.actions


def test_google_flow_submit_aborts_when_agent_turns_inactive_after_prompt_fill():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        agent_pressed=True,
        agent_deactivates_on_prompt_fill=True,
    )
    client, _ = _client(page)

    with pytest.raises(FlowWorkspaceVerificationError, match="Agent state"):
        client._submit_generation(page, _job().master_prompt)

    assert ("fill", "prompt", _job().master_prompt) in page.actions
    assert ("click", "generate") not in page.actions


def test_flow_workspace_cleanup_reuses_delete_and_observable_empty_verification():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[f"clip {number}" for number in range(1, 7)],
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        cleanup = getattr(workspace, "cleanup_and_verify_empty", None)
        assert cleanup is not None, "Flow post-job cleanup is not implemented"
        cleanup()

    assert page.clip_names == []
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]


def test_flow_workspace_cleanup_raises_when_zero_state_cannot_be_verified(monkeypatch):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[],
        empty_state_available=False,
        inventory_sequence=[1, 1, 1],
    )
    client, _ = _client(
        page,
        timeout_seconds=30.0,
        editor_ready_timeout_seconds=1.0,
        settled_poll_count=3,
    )
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_wait_for_settled_editor", lambda _page: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty"):
            workspace.cleanup_and_verify_empty()


def test_google_flow_generation_stops_early_when_observable_generated_image_appears(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Generation progress 0 / 6</div>"],
        generated_image_alts=["รูปภาพที่สร้างขึ้น"],
    )
    client, _ = _client(page, timeout_seconds=1800.0)
    clock = iter([0.0, 1800.1])
    monkeypatch.setattr(google_flow.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(FlowWorkspaceVerificationError, match="generated image"):
        client._wait_for_generation(page, expected_count=6)


def _completed_video_cards(*, fingerprints=("a", "b", "c", "d", "e", "f"), **overrides):
    return [
        {
            "fingerprint": fingerprint,
            "media_type": "video",
            "playable": True,
            "has_video_element": True,
            "ready_state": 0,
            **overrides,
        }
        for fingerprint in fingerprints
    ]


def _timeout_clock(monkeypatch):
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)


def test_google_flow_completed_video_detection_preserves_trusted_text_progress():
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, _ = _client(page)

    client._wait_for_generation(page, expected_count=6)


def test_google_flow_completed_video_cards_complete_after_three_stable_polls(
    monkeypatch,
):
    cards = _completed_video_cards()
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
    )
    client, _ = _client(page, timeout_seconds=5.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    client._wait_for_generation(page, expected_count=6)


def test_google_flow_completed_hidden_video_cards_complete_after_three_stable_polls(
    monkeypatch,
):
    cards = _completed_video_cards(video_visible=False, ready_state=0)
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
    )
    client, _ = _client(page, timeout_seconds=5.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    client._wait_for_generation(page, expected_count=6)


def test_google_flow_completed_video_cards_do_not_complete_after_only_two_polls(
    monkeypatch,
):
    cards = _completed_video_cards()
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, []],
    )
    client, _ = _client(page, timeout_seconds=1.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    with pytest.raises(FlowWorkspaceVerificationError, match="timed out"):
        client._wait_for_generation(page, expected_count=6)


def test_google_flow_completed_hidden_video_cards_do_not_complete_after_two_polls(
    monkeypatch,
):
    cards = _completed_video_cards(video_visible=False)
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, []],
    )
    client, _ = _client(page, timeout_seconds=1.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    with pytest.raises(FlowWorkspaceVerificationError, match="timed out"):
        client._wait_for_generation(page, expected_count=6)


def test_google_flow_completed_video_cards_require_card_local_video_descendant(
    monkeypatch,
):
    cards = _completed_video_cards()
    cards[0]["extra_visible_videos"] = 1
    cards[-1]["has_video_element"] = False
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
    )
    client, _ = _client(page, timeout_seconds=5.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    with pytest.raises(FlowWorkspaceVerificationError, match="timed out"):
        client._wait_for_generation(page, expected_count=6)


@pytest.mark.parametrize(
    "cards",
    [
        _completed_video_cards(processing=True),
        _completed_video_cards(
            fingerprints=("a", "b", "c", "d", "e", "unknown"),
        )[:-1]
        + [
            {
                "fingerprint": "unknown",
                "media_type": "unknown",
                "playable": True,
                "has_video_element": False,
            }
        ],
    ],
    ids=("processing", "unknown_media"),
)
def test_google_flow_completed_video_cards_fail_closed_on_unsafe_card_evidence(
    monkeypatch,
    cards,
):
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
    )
    client, _ = _client(page, timeout_seconds=1.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    with pytest.raises(FlowWorkspaceVerificationError, match="timed out"):
        client._wait_for_generation(page, expected_count=6)


def test_visible_failed_output_card_stops_wait_before_generation_timeout(
    monkeypatch,
):
    cards = _completed_video_cards()
    cards[-1].update(failed=True, has_video_element=False)
    page = FakePage(
        progress_html=["<main>Generating</main>"],
        completed_video_polls=[cards],
    )
    client, _ = _client(page, timeout_seconds=1800)
    sleep_calls = []
    monkeypatch.setattr(google_flow.time, "sleep", sleep_calls.append)

    with pytest.raises(FlowBatchIncompleteError) as error:
        client._wait_for_generation(page, expected_count=6)

    assert error.value.completed_count == 5
    assert error.value.failed_count == 1
    assert sleep_calls == []


def test_agent_panel_failure_text_does_not_count_as_failed_output_card():
    page = FakePage(
        progress_html=["<aside>Audio Generation Failed</aside>"],
        completed_video_polls=[[]],
    )
    client, _ = _client(page)

    assert client._failed_output_card_count(page) == 0


def test_targeted_submit_uses_exact_prepared_prompt_and_one_generate_click():
    page = FakePage(progress_html=["<div>Ready</div>"])
    client, _ = _client(page)
    workspace = google_flow.FlowWorkspaceRun(client, page)
    exact_wrapper = "Name only the new completed video clip 2.\n\nEXACT PROMPT"

    workspace.prepare_targeted_replacement(exact_wrapper, missing_index=2)
    workspace.submit_targeted_replacement(exact_wrapper, missing_index=2)

    assert page.last_filled == exact_wrapper
    assert page.actions.count(("click", "generate")) == 1


def test_reconcile_never_clicks_generate(tmp_path):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        completed_video_polls=[[]],
    )
    client, _ = _client(page)
    workspace = google_flow.FlowWorkspaceRun(client, page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")

    result = workspace.reconcile_targeted_replacement(
        paths,
        missing_index=2,
        attempt=1,
    )

    assert result.state in set(google_flow.FlowRecoveryRemoteState)
    assert page.actions.count(("click", "generate")) == 0


@pytest.mark.parametrize(
    (
        "clip_names",
        "completed_video_polls",
        "missing_index",
        "expected_state",
        "expected_numbers",
    ),
    [
        (
            [f"clip {number}" for number in range(1, 7)],
            [_completed_video_cards()],
            2,
            google_flow.FlowRecoveryRemoteState.COMPLETE_PROJECT,
            tuple(range(1, 7)),
        ),
        (
            ["clip 2"],
            [_completed_video_cards(fingerprints=("replacement",))],
            2,
            google_flow.FlowRecoveryRemoteState.REPLACEMENT_ONLY,
            (2,),
        ),
    ],
    ids=("complete_project", "replacement_only"),
)
def test_reconcile_targeted_replacement_waits_for_stable_media_card_names_before_download(
    tmp_path,
    clip_names,
    completed_video_polls,
    missing_index,
    expected_state,
    expected_numbers,
):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=clip_names,
        completed_video_polls=completed_video_polls,
    )
    client, _ = _client(page, editor_ready_timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")

    result = google_flow.FlowWorkspaceRun(client, page).reconcile_targeted_replacement(
        paths,
        missing_index=missing_index,
        attempt=1,
    )

    assert result.state is expected_state
    assert page.semantic_name_reads_at_project_download
    reads_before_download = page.semantic_name_reads_at_project_download[-1]
    assert all(reads_before_download[number] >= 2 for number in expected_numbers)


def test_google_flow_completed_video_cards_preserve_generated_image_failure_gate(
    monkeypatch,
):
    cards = _completed_video_cards()
    cards[-1] = {
        "fingerprint": "image",
        "media_type": "image",
        "playable": True,
        "has_video_element": False,
    }
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
        generated_image_alts=["generated image"],
    )
    client, _ = _client(page, timeout_seconds=5.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    with pytest.raises(FlowWorkspaceVerificationError, match="generated image"):
        client._wait_for_generation(page, expected_count=6)


@pytest.mark.parametrize(
    ("cards", "page_kwargs"),
    [
        (_completed_video_cards(busy=True), {}),
        (_completed_video_cards(), {"busy": True}),
        (_completed_video_cards(), {"progressbar": True}),
    ],
    ids=("card_busy", "page_busy", "progressbar"),
)
def test_google_flow_completed_video_cards_require_idle_page_state(
    monkeypatch,
    cards,
    page_kwargs,
):
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
        **page_kwargs,
    )
    client, _ = _client(page, timeout_seconds=1.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    with pytest.raises(FlowWorkspaceVerificationError, match="timed out"):
        client._wait_for_generation(page, expected_count=6)


def test_google_flow_completed_video_card_fingerprint_change_resets_stability(
    monkeypatch,
):
    stable = _completed_video_cards()
    changed = _completed_video_cards(fingerprints=("a", "b", "c", "d", "e", "x"))
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[stable, changed, stable],
    )
    client, _ = _client(page, timeout_seconds=1.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    with pytest.raises(FlowWorkspaceVerificationError, match="timed out"):
        client._wait_for_generation(page, expected_count=6)


def test_google_flow_completed_video_cards_allow_lazy_video_metadata(monkeypatch):
    cards = _completed_video_cards(ready_state=0)
    page = FakePage(
        progress_html=["<div>Generation complete</div>"],
        completed_video_polls=[cards, cards, cards],
    )
    client, _ = _client(page, timeout_seconds=5.0, settled_poll_count=3)
    _timeout_clock(monkeypatch)

    client._wait_for_generation(page, expected_count=6)


def test_google_flow_generation_timeout_is_bounded(monkeypatch, tmp_path):
    page = FakePage(progress_html=["<div>Generation progress 2 / 6</div>"])
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(MediaValidationError, match="timed out"):
        with client.acquire_workspace(_job()) as workspace:
            workspace.generate_and_download(_job(), paths)

    assert ("click", "bulk_download") not in page.actions


def test_google_flow_generation_timeout_is_typed_recovery_signal_after_submit(
    monkeypatch,
    tmp_path,
):
    page = FakePage(
        progress_html=["<div>Generation progress 5 / 6</div>"],
        completed_video_polls=[_completed_video_cards()[:5]],
    )
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        page.progressbar = True
        with pytest.raises(FlowGenerationTimeoutError, match="timed out"):
            workspace.generate_and_download(_job(), paths)

    assert ("click", "generate") in page.actions
    assert ("click", "bulk_download") not in page.actions


def test_google_flow_reconciliation_preserves_typed_generation_timeout(
    monkeypatch,
    tmp_path,
):
    page = FakePage(progress_html=["<div>Generation progress 5 / 6</div>"])
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")

    def timeout(_page, _expected_count):
        raise FlowGenerationTimeoutError(
            "Google Flow generation timed out before 6/6"
        )

    monkeypatch.setattr(client, "_wait_for_generation", timeout)

    with pytest.raises(FlowGenerationTimeoutError, match="timed out"):
        google_flow.FlowWorkspaceRun(client, page).reconcile_and_download(
            _job(flow_generation_unresolved=True),
            paths,
        )

    assert ("click", "generate") not in page.actions


def test_post_reload_settled_inventory_uses_editor_timeout_not_generation_timeout(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Loading</div>"],
        media_list_sequence=[True, False],
        inventory_sequence=[0, 0, 0],
    )
    client, _ = _client(
        page,
        timeout_seconds=1800.0,
        editor_ready_timeout_seconds=1.0,
        settled_poll_count=3,
    )
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_wait_for_settled_editor", lambda _page: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty"):
            workspace.prepare_for_generation()

    assert clock[0] <= 1.5
