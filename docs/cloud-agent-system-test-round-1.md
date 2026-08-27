# Cloud Agent system test — round 1

**Status: passed end-to-end**

This record captures the first successful full Cloud Agent production-path test.

| Item | Evidence |
| --- | --- |
| Job | `f2c2b925-cbcc-406f-bb59-e3268a0f3890` |
| Final status | `COMPLETED` |
| Final checkpoint | `COMPLETED` |
| Progress | `100` |
| Final validation | Passed |
| Final artifact | `storage/jobs/f2c2b925-cbcc-406f-bb59-e3268a0f3890/final/final.mp4` |
| Artifact size | 57,970,252 bytes |
| Observation time | 2026-08-27 UTC |

The run completed the active production path: TTS, Google Flow, Canva assembly,
Classic captions, MP4 export, server-side final validation, and job completion.

## Scope note

The job retained `flow_cleanup_unresolved=true`, and the worker recorded a
post-job Canva workspace cleanup verification warning. These non-blocking
post-completion cleanup states did not invalidate the final artifact or prevent
the job from reaching `COMPLETED`; they remain operational follow-up items and
are not claimed as verified by this round.
