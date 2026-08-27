# Cloud Agent Modern UI Design

**Status:** Approved from the generated visual reference on 2026-08-27.

**Visual reference:** `docs/ui-reference/cloud-agent-modern-ui-approved.png`

## 1. Goal

Redesign the existing VideosTurbo Cloud Agent Streamlit page so its visual
hierarchy, spacing, cards, colors, workflow rail, two-column workspace, and
production status closely match the approved reference while preserving the
existing FastAPI contracts, session-state behavior, Research safety rules, TTS
behavior, and worker workflow.

This is a presentation and interaction-organization change. It does not add a
second frontend, change generation providers, change the API, or alter worker
execution.

## 2. Visual Direction

- Use a bright, premium SaaS appearance rather than the default Streamlit look.
- Use deep navy for primary text, white surfaces, a cool blue-gray page
  background, cobalt/electric indigo for primary actions, emerald for success,
  and restrained gray for pending states.
- Use 12–16 px card radii, subtle 1 px borders, soft low-elevation shadows, and
  generous but efficient spacing.
- Use the system font stack with Inter-style proportions; do not add a web-font
  network dependency.
- Keep the page dense enough for professional work while making the primary
  path immediately obvious.
- Keep animation minimal and honor `prefers-reduced-motion`.

## 3. Information Architecture

### Sidebar

The custom sidebar contains the VideosTurbo wordmark, Cloud Agent as the active
destination, Music Batch as the existing functional destination, and a bottom
health-style label reading `All systems operational`. The approved mockup's
Projects and Settings destinations may be shown only as visibly disabled future
items; the implementation must not create dead clickable controls or invent new
backend behavior.

### Header

The header contains:

- breadcrumb: `Workspace / Cloud Agent`;
- title: `Create a video`;
- subtitle: `Research, write, narrate, and produce — all in one flow.`;
- a saved-state indicator derived from local session state, without claiming a
  server save that did not happen.

### Workflow rail

The rail has three stages:

1. `Script & Research`
2. `Voice`
3. `Produce`

The active stage is derived locally:

- no accepted script: stage 1 active;
- accepted script but no matching prepared voice: stage 1 complete and stage 2
  active;
- matching prepared voice or a created job: stages 1–2 complete and stage 3
  active;
- completed job: all stages complete.

No API request is made solely to calculate the rail.

## 4. Main Workspace

Use a wide left column and a narrower right column at desktop widths.

### Left column

The `Video brief` card contains the existing Video Subject, Target Words,
Language, Standard/Research mode, and Custom System Prompt controls. Standard
and Research become a single segmented control. In Research mode the source URL
rows, citation toggle, provider generation limit notice, and Generate Research
Script action remain available. One to three URL rows are still supported.

The `Script editor` card contains the existing editable script and draft refresh
behavior. A successful Research draft shows `Research complete`, source count,
source links, and accounting in a compact disclosure. Standard drafts never
display Research-only provenance. A failed Research attempt never overwrites the
editor.

### Right column

The `Generation setup` card contains the selected Research provider/model when
Research mode is active, plus the existing TTS provider, voice, and speed
controls. Research API key, Research defaults, TTS secrets, voice refresh, and
Cloud Agent defaults live inside a collapsed `Advanced settings` disclosure.
Secrets remain write-only and are never rendered back into the page.

If a prepared voice matches the current script/provider/voice/speed tuple, show
the native audio player in an `Audio preview` area. The existing `Create Voice`
action remains explicit. The main production action is labeled
`Continue to production` while retaining the existing `cloud_agent_start`
widget key and start-job contract.

## 5. Production Status

The bottom card shows five presentation stages:

- Script
- Voice
- Flow
- Canva
- Export

Stage state is derived from existing script state, prepared voice state, job
`status`, job `checkpoint`, and `current_step`. The UI does not introduce new
job statuses. Failed, human-required, paused, and cancelled states are shown
without hiding the existing safe error message or actions.

After Start succeeds, save the returned job ID and safe job snapshot in
Streamlit session state and render it in the status card. Existing-job lookup
remains available inside a compact disclosure. Pause, Resume, Retry, Cancel,
Google Flow readiness, Canva readiness, and browser-open controls remain
available but secondary to the primary workflow.

## 6. Responsive Behavior

- At widths at or above 1100 px, use the approved two-column workspace.
- Below 1100 px, stack Generation setup below the left workspace.
- Below 760 px, reduce page/card padding, allow the workflow rail to wrap, and
  make primary actions full width.
- Never create horizontal scrolling at 360 px viewport width.
- Native labels remain associated with widgets; icon-only actions retain an
  accessible name and visible keyboard focus.

## 7. Technical Boundaries

- Keep Streamlit 1.59.1 and the existing Python/FastAPI architecture.
- Add no JavaScript, frontend framework, CSS framework, remote font, analytics,
  or new runtime dependency.
- Scope custom CSS to Cloud Agent keys/classes so Music Batch is not restyled.
- Preserve existing widget keys unless this spec explicitly names a retained
  key; preserving session values across reruns is mandatory.
- Preserve `API_PREFIX`, request bodies, timeouts, typed error rendering,
  Research limits, citation toggle, provider no-fallback behavior, and separate
  provider keys.
- Do not touch API controllers, Research service/runtime/adapters, TTS service,
  browser profiles, worker code, SQLite schemas, or deployment units.
- Do not call paid providers, TTS generation, Flow generation, Canva mutation,
  or browser sessions during automated tests or visual smoke verification.

## 8. Accessibility and Copy

- Target WCAG AA contrast for normal text and visible focus states.
- Use sentence case and the approved English primary labels.
- Keep validation and operational error messages in their existing language;
  visual redesign must not replace typed errors with generic copy.
- Do not use color as the only status indicator; combine color with icon/text.
- Keep touch targets at least 40 px high for primary controls.

## 9. Acceptance Criteria

- At 1536 × 1024, the page visually follows the approved reference: custom
  sidebar, header, three-step rail, wide-left/narrow-right cards, and bottom
  production status are all visible above or close to the initial fold.
- Standard Script, Research Script, source limits, citation toggle, settings
  persistence, API-key write/removal, draft refresh, Create Voice, audio reuse,
  Start, job controls, and final status remain functional.
- A Research success enters the same Script Editor and a Research failure keeps
  the prior editor content.
- No backend/API/worker behavior changes are present in the diff.
- Focused UI tests, Cloud Agent regression tests, Ruff, `git diff --check`, and
  Streamlit AppTest pass.
- A fresh 1536 × 1024 local screenshot is reviewed side by side with
  `docs/ui-reference/cloud-agent-modern-ui-approved.png` before deployment.
