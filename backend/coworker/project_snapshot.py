"""Git-backed project snapshots for session-scoped file rollback (opencode-style).

The ``ProjectSnapshotManager`` keeps, for every git-repo workspace, a *separate*
git repository (``data_dir/snapshot-repos/<hash>/``) whose ``--work-tree`` is the
real workspace. Per agent turn it records lightweight tree hashes (``git
write-tree``) before (``pre``) and after (``post``) the turn so that editing a
user message can restore the files that turn changed — including shell-driven
changes the change store never sees.

Session isolation is a hard invariant:

* Rollback scope is ALWAYS decided by the session's own ``ChangeStore`` records
  (the authoritative, per-session ledger). Git only supplies *file content* for
  records whose full content was too large to capture, and a *shell-change
  fallback* for files that changed during the turn but have no change record.
* The shell fallback only runs while the workspace has a single active session
  (no other session is concurrently writing), and every restore is gated on
  ``current content == post content`` so a file edited by another session or the
  user after the turn is reported as a conflict and left untouched.
* Cross-workspace pairs are keyed by ``(session_id, user_message_id)`` and stored
  per-session, so one session can never revert or even see another session's
  snapshot state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

SNAPSHOT_GC_PRUNE = "7.days"
SNAPSHOT_GIT_TIMEOUT = 30.0
PAIR_MAX_PER_SESSION = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _SnapshotRepo:
    """A standalone git repo whose work-tree is the real workspace."""

    def __init__(self, repo_dir: Path, workspace_root: Path):
        self.repo_dir = repo_dir
        self.workspace_root = Path(workspace_root)
        self._initialized = False

    # -- git plumbing ----------------------------------------------------- #

    def _git(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        stdin: str | None = None,
    ) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["git", "--git-dir", str(self.repo_dir), "--work-tree", str(self.workspace_root), *args],
                cwd=str(cwd or self.workspace_root),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=SNAPSHOT_GIT_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("snapshot git failed: %s", exc)
            return 1, "", str(exc)
        return proc.returncode, proc.stdout, proc.stderr

    def ensure_initialized(self) -> bool:
        if self._initialized:
            return True
        try:
            self.repo_dir.mkdir(parents=True, exist_ok=True)
            for cmd in (
                ["init"],
                ["config", "core.autocrlf", "false"],
                ["config", "core.longpaths", "true"],
                ["config", "core.symlinks", "true"],
                ["config", "core.fsmonitor", "false"],
                ["config", "feature.manyFiles", "true"],
                ["config", "index.version", "4"],
            ):
                code, _, _ = self._git(cmd, cwd=self.workspace_root)
                if code != 0:
                    return False
            self._initialized = True
            return True
        except OSError:
            return False

    def track(self) -> str | None:
        """Stage the changed/untracked files (respecting the workspace's
        ``.gitignore``) and return a lightweight tree hash (no commit)."""
        if not self.ensure_initialized():
            return None
        # Only files that differ from the snapshot index are staged, so the
        # per-turn cost stays bounded on large repositories.
        code, diff, _ = self._git(["diff-files", "--name-only", "-z", "--", "."], cwd=self.workspace_root)
        if code != 0:
            return None
        code, others, _ = self._git(["ls-files", "--others", "--exclude-standard", "-z", "--", "."], cwd=self.workspace_root)
        if code != 0:
            return None
        files = sorted(set(filter(None, diff.split("\0"))) | set(filter(None, others.split("\0"))))
        if files:
            pathspecs = "".join(f":(top,literal){f}\0" for f in files)
            code, _, _ = self._git(
                ["add", "--pathspec-from-file=-", "--pathspec-file-nul"],
                cwd=self.workspace_root,
                stdin=pathspecs,
            )
            if code != 0:
                return None
        code, tree, _ = self._git(["write-tree"], cwd=self.workspace_root)
        if code != 0:
            return None
        hash_ = tree.strip()
        return hash_ or None

    def blob_for(self, tree: str, rel_path: str) -> str | None:
        """Return the blob hash of ``rel_path`` in ``tree`` (None if absent)."""
        code, out, _ = self._git(["rev-parse", f"{tree}:{rel_path}"], cwd=self.workspace_root)
        if code != 0:
            return None
        return out.strip() or None

    def current_blob(self, rel_path: str) -> str | None:
        """Return the blob hash of the work-tree file at ``rel_path``."""
        target = self.workspace_root / rel_path
        if not target.is_file():
            return None
        try:
            proc = subprocess.run(
                ["git", "--git-dir", str(self.repo_dir), "--work-tree", str(self.workspace_root), "hash-object", str(target)],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=SNAPSHOT_GIT_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout.strip() or None

    def diff_paths(self, from_tree: str, to_tree: str) -> list[str]:
        """List files changed between two trees (path only)."""
        code, out, _ = self._git(["diff-tree", "--no-commit-id", "--name-only", "-r", from_tree, to_tree], cwd=self.workspace_root)
        if code != 0:
            return []
        return [line for line in out.splitlines() if line.strip()]

    def restore_file(self, tree: str, rel_path: str) -> bool:
        """Restore ``rel_path`` from ``tree`` (writes into the work-tree)."""
        target = self.workspace_root / rel_path
        if self.blob_for(tree, rel_path) is None:
            # The path does not exist in the target tree: delete it.
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("snapshot delete failed for %s: %s", rel_path, exc)
                return False
            return True
        code, _, _ = self._git(["checkout", tree, "--", rel_path], cwd=self.workspace_root)
        return code == 0

    def gc(self) -> dict[str, Any]:
        """Prune loose objects older than the retention window."""
        code, _, _ = self._git(["gc", f"--prune={SNAPSHOT_GC_PRUNE}"], cwd=self.workspace_root)
        return {"pruned": code == 0}


class ProjectSnapshotManager:
    """Per-session snapshot pairing with strict cross-session isolation."""

    def __init__(self, data_dir: Path):
        self.repo_root = Path(data_dir) / "snapshot-repos"
        self.index_dir = Path(data_dir) / "snapshot-index"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._repos: dict[str, _SnapshotRepo] = {}
        self._active: dict[str, set[str]] = {}  # workspace_key -> active session ids
        self._lock = threading.RLock()

    # -- workspace helpers ------------------------------------------------ #

    @staticmethod
    def workspace_key(workspace: Any) -> str:
        root = str(Path(workspace.root).resolve())
        return hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]

    def repo_for(self, workspace: Any) -> _SnapshotRepo:
        key = self.workspace_key(workspace)
        with self._lock:
            repo = self._repos.get(key)
            if repo is None:
                repo = _SnapshotRepo(self.repo_root / key, Path(workspace.root))
                self._repos[key] = repo
            return repo

    def enabled_for(self, workspace: Any) -> bool:
        from .workspace import git_is_repo

        try:
            return git_is_repo(Path(workspace.root))
        except OSError:
            return False

    def is_exclusive(self, workspace: Any) -> bool:
        """True when no session is currently streaming on this workspace."""
        with self._lock:
            return not bool(self._active.get(self.workspace_key(workspace)))

    # -- pair persistence ------------------------------------------------- #

    def _pair_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
        return self.index_dir / f"{safe}.jsonl"

    def _read_pairs(self, session_id: str) -> list[dict[str, Any]]:
        path = self._pair_path(session_id)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        pairs: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                pairs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return pairs

    def _write_pairs(self, session_id: str, pairs: list[dict[str, Any]]) -> None:
        path = self._pair_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    # -- turn lifecycle --------------------------------------------------- #

    def begin_turn(self, session_id: str, user_message_id: str, workspace: Any, *, preserve_existing: bool = False) -> str | None:
        """Capture the pre-turn tree hash and register the session as active.

        When ``preserve_existing`` is true (goal resume) an existing active pair
        for the same key is reused so the whole goal shares one pre baseline.
        Returns the pre-tree hash, or None when snapshots are unavailable.
        """
        if not self.enabled_for(workspace):
            return None
        repo = self.repo_for(workspace)
        pre = repo.track()
        if pre is None:
            return None
        with self._lock:
            self._active.setdefault(self.workspace_key(workspace), set()).add(session_id)
        pairs = self._read_pairs(session_id)
        if preserve_existing:
            active = [p for p in pairs if p.get("user_message_id") == user_message_id and p.get("state") == "active" and p.get("pre")]
            if active:
                return str(active[-1]["pre"])
        pairs.append(
            {
                "id": uuid4().hex,
                "user_message_id": user_message_id,
                "pre": pre,
                "post": None,
                "state": "active",
                "ts": _now(),
            }
        )
        pairs = pairs[-PAIR_MAX_PER_SESSION:]
        self._write_pairs(session_id, pairs)
        return pre

    def end_turn(self, session_id: str, user_message_id: str, workspace: Any) -> None:
        """Capture the post-turn tree hash onto the latest active pair."""
        with self._lock:
            self._active.get(self.workspace_key(workspace), set()).discard(session_id)
        if not self.enabled_for(workspace):
            return
        repo = self.repo_for(workspace)
        post = repo.track()
        if post is None:
            return
        pairs = self._read_pairs(session_id)
        updated = False
        for pair in reversed(pairs):
            if pair.get("user_message_id") == user_message_id and pair.get("state") == "active":
                pair["post"] = post
                updated = True
                break
        if updated:
            self._write_pairs(session_id, pairs)

    # -- revert / redo ---------------------------------------------------- #

    def revert_turn(self, session_id: str, user_message_id: str, workspace: Any, *, too_large_paths: set[str] | None = None, record_paths: set[str] | None = None) -> dict[str, Any]:
        """Restore the files that the given user message's turn changed.

        Only files from the session's own snapshot pair are touched. Records
        whose content was too large for the change store are restored from the
        git ``pre`` tree; files with no change record at all (shell-driven) are
        restored only while the workspace is exclusive. Every restore is gated on
        the file still matching the ``post`` tree; otherwise it is a conflict.
        """
        result: dict[str, Any] = {"reverted": [], "conflicts": [], "reverted_count": 0, "conflict_count": 0}
        if not self.enabled_for(workspace):
            return result
        pair = self._latest_pair(session_id, user_message_id, state="active")
        if pair is None or not pair.get("pre") or not pair.get("post"):
            return result
        repo = self.repo_for(workspace)
        pre, post = str(pair["pre"]), str(pair["post"])
        record_paths = record_paths or set()
        touched: set[str] = set()
        for rel in sorted(too_large_paths or []):
            if not rel:
                continue
            touched.add(rel)
            status = self._restore_to_pre(repo, pre, post, rel)
            self._append_result(result, status, rel, pair)
        if self.is_exclusive(workspace):
            changed = repo.diff_paths(pre, post)
            for rel in changed:
                if rel in touched or rel in record_paths:
                    continue
                touched.add(rel)
                status = self._restore_to_pre(repo, pre, post, rel)
                self._append_result(result, status, rel, pair)
        if result["reverted_count"] > 0 or result["conflict_count"] > 0:
            self._mark_reverted(session_id, pair["id"], sorted(touched))
        return result

    def redo_turn(self, session_id: str, user_message_id: str, workspace: Any) -> dict[str, Any]:
        """Re-apply the changes that ``revert_turn`` restored (undo-the-undo)."""
        result: dict[str, Any] = {"restored": [], "conflicts": [], "restored_count": 0, "conflict_count": 0}
        if not self.enabled_for(workspace):
            return result
        pairs = self._read_pairs(session_id)
        reverted = [p for p in pairs if p.get("user_message_id") == user_message_id and p.get("state") == "reverted"]
        if not reverted:
            return result
        pair = reverted[-1]
        pre, post = str(pair.get("pre") or ""), str(pair.get("post") or "")
        if not pre or not post:
            return result
        repo = self.repo_for(workspace)
        touched: set[str] = set()
        for rel in pair.get("reverted_paths") or []:
            if not rel:
                continue
            touched.add(rel)
            status = self._restore_to_post(repo, pre, post, rel)
            if status.get("status") == "restored":
                result["restored"].append(status)
                result["restored_count"] += 1
            else:
                result["conflicts"].append(status)
                result["conflict_count"] += 1
        if touched:
            self._mark_done(session_id, pair["id"])
        return result

    def _latest_pair(self, session_id: str, user_message_id: str, *, state: str) -> dict[str, Any] | None:
        pairs = self._read_pairs(session_id)
        for pair in reversed(pairs):
            if pair.get("user_message_id") == user_message_id and pair.get("state") == state:
                return pair
        return None

    def _restore_to_pre(self, repo: _SnapshotRepo, pre: str, post: str, rel: str) -> dict[str, Any]:
        """Restore ``rel`` to its ``pre`` version if it still equals ``post``."""
        current = repo.current_blob(rel)
        post_blob = repo.blob_for(post, rel)
        if current != post_blob:
            return {"status": "conflict", "path": rel, "reason": "file changed after this turn; refusing to overwrite"}
        if repo.restore_file(pre, rel):
            return {"status": "reverted", "path": rel}
        return {"status": "conflict", "path": rel, "reason": "git restore failed"}

    def _restore_to_post(self, repo: _SnapshotRepo, pre: str, post: str, rel: str) -> dict[str, Any]:
        """Restore ``rel`` to its ``post`` version if it still equals ``pre``."""
        current = repo.current_blob(rel)
        pre_blob = repo.blob_for(pre, rel)
        if current != pre_blob:
            return {"status": "conflict", "path": rel, "reason": "file changed after the revert; refusing to overwrite"}
        if repo.restore_file(post, rel):
            return {"status": "restored", "path": rel}
        return {"status": "conflict", "path": rel, "reason": "git restore failed"}

    def _append_result(self, result: dict[str, Any], status: dict[str, Any], rel: str, pair: dict[str, Any]) -> None:
        status = {**status, "path": rel}
        if status.get("status") == "reverted":
            result["reverted"].append(status)
            result["reverted_count"] += 1
        else:
            result["conflicts"].append(status)
            result["conflict_count"] += 1

    def _mark_reverted(self, session_id: str, pair_id: str, touched: list[str]) -> None:
        pairs = self._read_pairs(session_id)
        for pair in pairs:
            if pair.get("id") == pair_id and pair.get("state") == "active":
                pair["state"] = "reverted"
                pair["reverted_paths"] = touched
                break
        self._write_pairs(session_id, pairs)

    def _mark_done(self, session_id: str, pair_id: str) -> None:
        pairs = self._read_pairs(session_id)
        remaining = [p for p in pairs if p.get("id") != pair_id]
        self._write_pairs(session_id, remaining)

    # -- lifecycle -------------------------------------------------------- #

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            for key in list(self._active):
                self._active[key].discard(session_id)
        try:
            self._pair_path(session_id).unlink(missing_ok=True)
        except OSError:
            pass

    def gc(self) -> dict[str, Any]:
        """Prune snapshot object databases (best effort, per repo)."""
        stats: dict[str, Any] = {"repos": 0, "pruned": 0}
        with self._lock:
            repos = list(self._repos.values())
        for repo in repos:
            res = repo.gc()
            stats["repos"] += 1
            if res.get("pruned"):
                stats["pruned"] += 1
        return stats
