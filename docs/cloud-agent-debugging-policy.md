# Cloud Agent Debugging Policy

When the same implementation path fails repeatedly, do not keep retrying it.

1. Stop after collecting the evidence from the failed attempts.
2. Identify the proven failure boundary and root-cause hypotheses.
3. Propose multiple materially different approaches.
4. Try the highest-confidence approach first, with a measurable success condition.
5. If it fails, record why and move to the next approach rather than repeating the same one.
6. Preserve durable artifacts and do not repeat paid operations without explicit authorization.

Every live Canva/Flow operation must report its active checkpoint, observable
success condition, and durable failure evidence so an interrupted run can resume
from facts rather than guesswork.
