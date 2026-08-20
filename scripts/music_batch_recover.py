from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.music_batch.recovery import resume_incomplete_batches  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume non-terminal Music Batch jobs from an output root."
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Folder containing batch_*/batch_state.json directories.",
    )
    args = parser.parse_args()

    root = Path(args.output_root).expanduser().resolve()
    result = resume_incomplete_batches(root)
    print(
        f"Music Batch recovery finished: resumed={len(result.resumed)} "
        f"failed={len(result.failed)} root={root}"
    )
    for batch_dir, error in result.failed:
        print(f"FAILED {batch_dir}: {error}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
