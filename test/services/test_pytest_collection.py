import tomllib
from pathlib import Path


def test_pytest_ignores_local_backup_and_worktree_trees():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    ignored = set(config["tool"]["pytest"]["ini_options"]["norecursedirs"])

    assert {"backups", ".worktrees"}.issubset(ignored)
