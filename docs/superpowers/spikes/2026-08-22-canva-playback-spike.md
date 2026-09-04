# Canva Playback Automation Spike — PASS

**Date:** 2026-08-23
**Branch / commit:** `feature/cloud-video-agent` / `de4bc436c867721829128743f21b03580bcb0e40`
**Scope:** Task 9 live, disposable Canva design only. No production Canva assembly code was added.

## Environment

- Ubuntu 24.04.4 LTS; headed Google Chrome Stable 151.0.7922.173.
- Playwright 1.62.0, Xvfb `DISPLAY=:99`, Openbox, x11vnc, and noVNC.
- Canva persistent profile: `storage/browser-profiles/canva`.
- Chrome was launched with Playwright's default `--no-sandbox` argument explicitly removed. The live Chrome root process was verified not to contain `--no-sandbox`; the installed Chrome sandbox has the expected setuid-root mode.
- noVNC and VNC listeners remained loopback-only.

## Selector strategy and observable evidence

Canva's current editor labels its playback control **Speed**, rather than **Playback**. The following selectors were exercised against the live editor without coordinate-only actions:

1. Video clips were selected from the six-item timeline row through the accessible trim-handle structure:
   - `[role="slider"][aria-label="Trimming, start edge"]`
   - disposable-design video-track entries: indices 9 through 14, each using its parent clip container.
   - Selection was verified by the video toolbar (`Edit`, `Trim a 10 seconds video`, `Volume`, `Speed`, `Crop`, `Flip`, `Animate`, `Position`), distinct from the text-toolbar state.
2. The video playback control opened through `get_by_role("button", name="Speed", exact=True)`.
3. The resulting panel was observable as `[aria-label="Video Speed"]`, with its custom control at `input[role="spinbutton"]`.
4. The custom value was set with `fill("0.95")` and committed with `press("Enter")`.

The initial live session check reached an authenticated Canva editor. It exposed a visible Share control and editor surface; it did not show a login form, CAPTCHA, verification prompt, or other authentication challenge. The repository's current session classifier initially returned `ERROR` because it checked before the editor had finished rendering its Share control; this was treated as a timing/selector observation only and was not changed during this spike.

## Results

| Requirement | Live result |
| --- | --- |
| Select an uploaded video without coordinate-only automation | PASS — accessible timeline selector selected a video toolbar, not a text toolbar. |
| Open Playback | PASS — current Canva UI exposes the equivalent as the observable `Speed` control and `Video Speed` panel. |
| Apply approximately 0.95x | PASS — each of the six clips reported `0.95` from the UI spinbutton. |
| Verify playback/duration change | PASS — clip 1 timeline width changed from `239` to `252` after applying 0.95x. |
| Repeat all six clips | PASS — clips 1–6 were independently reselected and read back as `0.95`. |
| Bound final visual end to narration target | PASS — visual and narration end sliders both reported `63,189,468` microseconds (`63.189468s`). A keyboard `ArrowLeft` on the final visual end-edge changed it to `63,147,800`; `ArrowRight` restored it exactly to `63,189,468`, within the configured 1-second tolerance. |
| Headed Xvfb/noVNC production-style browser | PASS — all live actions ran in headed Chrome on `DISPLAY=:99`; VNC/noVNC services were active and loopback-only. |

## Gate decision

**Task 9 PASS.** The live spike demonstrated a selector-driven, observable Canva playback workflow for all six clips and a keyboard-accessible final-end bound. Task 10 may proceed, but its production adapter must use only the observed selector/action strategy and must preserve sandboxed headed Chrome behavior. No fallback to FFmpeg final assembly is authorized by this result.
