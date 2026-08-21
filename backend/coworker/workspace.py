from __future__ import annotations

import mimetypes
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomicio import append_jsonl_retained, trim_jsonl


DEFAULT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".cache",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".parcel-cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
}
DEFAULT_SEARCH_MAX_RESULTS = 80
DEFAULT_SEARCH_MAX_FILE_BYTES = 1_000_000
DEFAULT_COMMAND_TIMEOUT_SECONDS = 20
MAX_COMMAND_TIMEOUT_SECONDS = 60
MAX_COMMAND_OUTPUT_CHARS = 12_000
# Agent-facing file reads (`read_file` tool) are truncated at the source so a
# single oversized file never floods the model context. Matches the spirit of
# opencode's TOOL_OUTPUT_MAX_CHARS=2000 and run_command's MAX_COMMAND_OUTPUT_CHARS.
READ_FILE_MAX_CHARS = 50_000
TOOL_AUDIT_FILENAME = "tool_audit.jsonl"
COMMAND_APPROVAL_FILENAME = "command_approvals.json"

# ── Sensitive file patterns for read-path blocking ──────────────────────────
SENSITIVE_BASENAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.staging", ".env.development",
    ".pem", ".key", "id_rsa", "id_ed25519",
    ".secret", ".secrets", "secrets.json", "secrets.yaml",
    ".credentials", ".htpasswd", ".netrc",
    "wp-config.php", "web.config",
})
SENSITIVE_SUFFIXES = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".jks", ".env", ".secret", ".secrets",
})


# ── Path boundary violation exception classes ─────────────────────────────
class PathBoundaryError(ValueError):
    """Raised when a path violates workspace security policy."""

    REASONS = {
        "relative_escape": "Access denied: {} escapes workspace boundary (path traversal)",
        "external_write": "Write denied: {} is outside the workspace sandbox",
        "sensitive_read": "Access denied: {} is a sensitive file",
    }

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        msg = self.REASONS.get(reason, "Access denied: {}")
        super().__init__(msg.format(path))


class ExternalWriteError(PathBoundaryError):
    """Marker exception for external write — used by HITL approval flow."""

    def __init__(self, path: str):
        super().__init__(path, "external_write")


READ_ONLY_COMMANDS = frozenset({
    "cat", "ls", "pwd", "head", "tail", "more", "less",
    "wc", "grep", "rg", "find", "file", "stat", "du", "df",
    "id", "whoami", "uname", "date", "echo",
})


def fingerprint_path_for(data_dir: Path, workspace_root: Path) -> Path:
    """Stable per-workspace path for the persisted staleness fingerprints.

    Keyed by the resolved workspace root so different projects keep separate
    fingerprint files even when they live under the same parent directory."""
    root_key = hashlib.sha256(str(Path(workspace_root).resolve()).encode("utf-8")).hexdigest()[:24]
    return Path(data_dir) / "fingerprints" / f"{root_key}.json"


# Retention policy for the run-observation stores: these grow unboundedly if
# never pruned, so terminal-state approvals and the rolling JSONL logs are
# capped at the most recent N entries.
MAX_APPROVAL_HISTORY = 100
# 无论 active（pending/approved）数量是否已超过上限，都至少保留最近这 N 条
# terminal（answered/denied/consumed）记录。刚被回答的审批在
# `_resume_in_background` 消费（mark_consumed）之前必须存活：否则
# resolve_command_approval 的 siblings 查询会读不到它，导致回答后 agent 不恢复。
MIN_TERMINAL_HISTORY = 25
MAX_TOOL_AUDIT_LINES = 100

# Runtime-adjustable retention (Settings page overrides the default; applied on
# every audit write so the change takes effect without a restart).
ACTIVE_TOOL_AUDIT_RETENTION = MAX_TOOL_AUDIT_LINES


def set_tool_audit_retention(lines: int) -> None:
    global ACTIVE_TOOL_AUDIT_RETENTION
    ACTIVE_TOOL_AUDIT_RETENTION = max(1, min(int(lines or MAX_TOOL_AUDIT_LINES), 10_000))

ALLOWED_COMMANDS = {
    "cat",
    "chmod",
    "find",
    "git",
    "ls",
    "node",
    "npm",
    "npx",
    "pwd",
    "python",
    "python3",
    "pytest",
    "rg",
    "sed",
}


class CommandApprovalStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        approvals = self.load()
        return sorted(approvals, key=lambda item: item.get("updated_at", ""), reverse=True)

    def require(self, approval_id: str) -> dict[str, Any]:
        for approval in self.load():
            if approval.get("id") == approval_id:
                return approval
        raise KeyError(f"command approval {approval_id} not found")

    def request(
        self,
        digest: str,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        approvals = self.load()
        now = datetime.now(timezone.utc).isoformat()
        for approval in approvals:
            if approval.get("digest") == digest and approval.get("status") in {"pending", "approved", "denied"}:
                return approval

        approval = {
            "id": self._unique_id(approvals, digest),
            "digest": digest,
            "status": "pending",
            "command": command,
            "cwd": cwd,
            "timeout_seconds": timeout_seconds,
            "context": context or {},
            "created_at": now,
            "updated_at": now,
        }
        approvals.append(approval)
        self.save(approvals)
        return approval

    @staticmethod
    def _unique_id(approvals: list[dict[str, Any]], digest: str) -> str:
        """Derive a stable-but-unique approval id from the digest, bumping the
        numeric suffix until it no longer collides with an existing record."""
        base = digest[:12]
        existing = {str(item.get("id", "")) for item in approvals}
        suffix = len(approvals) + 1
        approval_id = f"{base}-{suffix}"
        while approval_id in existing:
            suffix += 1
            approval_id = f"{base}-{suffix}"
        return approval_id

    def request_runtime_interrupt(
        self,
        interrupt_id: str,
        action_index: int,
        kind: str,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        digest = f"langgraph:{context.get('session_id', '')}:{interrupt_id}:{action_index}"
        return self.request(digest, command, cwd, timeout_seconds, {**context, "kind": kind})

    def approve(self, approval_id: str) -> dict[str, Any]:
        return self.update_status(approval_id, "approved")

    def deny(self, approval_id: str) -> dict[str, Any]:
        return self.update_status(approval_id, "denied")

    def consume(self, digest: str) -> dict[str, Any] | None:
        approvals = self.load()
        now = datetime.now(timezone.utc).isoformat()
        consumed: dict[str, Any] | None = None
        for approval in approvals:
            if approval.get("digest") == digest and approval.get("status") == "approved":
                approval["status"] = "consumed"
                approval["updated_at"] = now
                consumed = approval
                break
        if consumed:
            self.save(approvals)
        return consumed

    def update_status(self, approval_id: str, status: str) -> dict[str, Any]:
        approvals = self.load()
        now = datetime.now(timezone.utc).isoformat()
        for approval in approvals:
            if approval.get("id") == approval_id:
                if approval.get("status") not in {"pending", "approved"}:
                    raise ValueError(f"command approval {approval_id} is already {approval.get('status')}")
                approval["status"] = status
                approval["updated_at"] = now
                self.save(approvals)
                return approval
        raise KeyError(f"command approval {approval_id} not found")

    def set_decision(self, approval_id: str, status: str, decision: dict[str, Any]) -> dict[str, Any]:
        approvals = self.load()
        now = datetime.now(timezone.utc).isoformat()
        for approval in approvals:
            if approval.get("id") == approval_id:
                if approval.get("status") not in {"pending", "approved"}:
                    raise ValueError(f"command approval {approval_id} is already {approval.get('status')}")
                approval["status"] = status
                approval["decision"] = decision
                approval["updated_at"] = now
                self.save(approvals)
                return approval
        raise KeyError(f"command approval {approval_id} not found")

    def mark_consumed(self, approval_id: str) -> None:
        approvals = self.load()
        now = datetime.now(timezone.utc).isoformat()
        for approval in approvals:
            if approval.get("id") == approval_id:
                if approval.get("status") not in {"pending", "approved", "denied", "answered"}:
                    return
                approval["status"] = "consumed"
                approval["updated_at"] = now
                self.save(approvals)
                return

    def load_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"approvals": [], "allowlist": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"approvals": [], "allowlist": []}
        if not isinstance(payload, dict):
            return {"approvals": [], "allowlist": []}
        return payload

    def save_payload(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def load(self) -> list[dict[str, Any]]:
        approvals = self.load_payload().get("approvals")
        return approvals if isinstance(approvals, list) else []

    @staticmethod
    def _prune_approvals(approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Retention policy: keep active approvals (pending/approved, needed for
        resume) plus the most recent terminal-state records, capped at
        ``MAX_APPROVAL_HISTORY`` total. The allowlist is separate and untouched.
        """
        active = [a for a in approvals if a.get("status") in ("pending", "approved")]
        terminal = [a for a in approvals if a.get("status") not in ("pending", "approved")]
        terminal.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        keep_terminal = terminal[: max(MIN_TERMINAL_HISTORY, MAX_APPROVAL_HISTORY - len(active))]
        return active + keep_terminal

    def save(self, approvals: list[dict[str, Any]]) -> None:
        payload = self.load_payload()
        payload["approvals"] = self._prune_approvals(approvals)
        self.save_payload(payload)

    def prune(self) -> None:
        """Shrink an existing (pre-policy) store to the retention cap. Safe to
        call repeatedly: it only rewrites when the terminal history is over cap.
        """
        approvals = self.load()
        pruned = self._prune_approvals(approvals)
        if len(pruned) != len(approvals):
            payload = self.load_payload()
            payload["approvals"] = pruned
            self.save_payload(payload)

    def allowlist(self) -> list[str]:
        allowlist = self.load_payload().get("allowlist")
        return allowlist if isinstance(allowlist, list) else []

    def always_allow(self, digest: str) -> None:
        payload = self.load_payload()
        allowlist = payload.get("allowlist")
        if not isinstance(allowlist, list):
            allowlist = []
        if digest not in allowlist:
            allowlist.append(digest)
        payload["allowlist"] = allowlist
        self.save_payload(payload)

    def is_always_allowed(self, digest: str) -> bool:
        return digest in self.allowlist()


class Workspace:
    def __init__(self, root: Path, audit_path: Path | None = None, fingerprint_path: Path | None = None):
        self.root = root.resolve()
        self.audit_path = audit_path
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        # Persisted staleness fingerprints: loading them at construction time makes
        # the "File changed since it was last read" guard work ACROSS turns and
        # sessions (each request builds a fresh Workspace, so an in-memory dict
        # alone only protected a single turn).
        self._fingerprints: dict[str, tuple[int, int, str]] = {}
        self._fingerprint_path = fingerprint_path
        if fingerprint_path is not None:
            self._load_fingerprints()

        # Flag set by resume_interrupt when HITL approves an external write.
        # Only one tool executes per resume step, so a plain instance variable
        # is safe against concurrent access.
        self._allow_external_write: bool = False

    def _resolve(self, file_path: str) -> Path:
        """Resolve a path against workspace root without boundary check."""
        return (self.root / file_path).resolve()

    def resolve_read_path(self, file_path: str) -> Path:
        """Resolve path for reading — external paths allowed.

        Security guards:
        1. Symlink-based path traversal (relative + absolute escape)
        2. Sensitive file detection (secrets, keys, env vars)
        """
        candidate = self._resolve(file_path)

        # Guard 1a: relative path must not lexically escape the workspace root
        # before filesystem resolution (user supplied ../foo, catch early).
        is_relative = not file_path.startswith("/") and not file_path.startswith("~")
        if is_relative:
            if candidate != self.root and self.root not in candidate.parents:
                raise PathBoundaryError(file_path, "relative_escape")

        # Guard 2: sensitive files blocked regardless of path location.
        if self._is_sensitive_file(candidate):
            raise PathBoundaryError(file_path, "sensitive_read")

        return candidate

    def resolve_write_path(self, file_path: str) -> Path:
        """Resolve path for writing — internal paths always allowed, external
        paths allowed only when _allow_external_write is True (set by
        resume_interrupt after HITL approval).
        """
        candidate = self._resolve(file_path)
        if candidate == self.root or self.root in candidate.parents:
            return candidate
        if self._allow_external_write:
            return candidate
        raise ExternalWriteError(file_path)

    @staticmethod
    def _is_sensitive_file(path: Path) -> bool:
        """Check if a file is potentially sensitive (secrets, keys, env vars)."""
        name = path.name
        if name in SENSITIVE_BASENAMES:
            return True
        name_lower = name.lower()
        return any(name_lower.endswith(s.lower()) for s in SENSITIVE_SUFFIXES)

    def _safe_rel_path(self, path: Path) -> str:
        """Return workspace-relative path, or absolute if path is outside workspace."""
        try:
            return self.rel_path(path)
        except ValueError:
            return str(path)

    def normalize_rel_path(self, file_path: str) -> str:
        """Best-effort conversion of a tool-supplied path to a workspace-relative path."""
        try:
            return self.rel_path(self.resolve_write_path(file_path))
        except (ValueError, OSError):
            return str(file_path).lstrip("./").replace("\\", "/")

    def resolve_path(self, file_path: str) -> Path:
        """DEPRECATED: Use resolve_read_path() for reads or resolve_write_path() for writes.
        
        Kept for backward compatibility — new code should use the specific methods.
        This delegates to resolve_write_path().
        """
        return self.resolve_write_path(file_path)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            return ""
        return digest.hexdigest()

    def _fingerprint(self, path: Path) -> tuple[int, int, str] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size, self._sha256(path)

    def _load_fingerprints(self) -> None:
        """Load the persisted staleness fingerprints for this workspace."""
        if self._fingerprint_path is None or not self._fingerprint_path.exists():
            return
        try:
            raw = json.loads(self._fingerprint_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return
        loaded: dict[str, tuple[int, int, str]] = {}
        for key, value in raw.items() if isinstance(raw, dict) else []:
            if isinstance(value, list) and len(value) == 3:
                try:
                    loaded[str(key)] = (int(value[0]), int(value[1]), str(value[2]))
                except (TypeError, ValueError):
                    continue
        self._fingerprints = loaded

    def _persist_fingerprints(self) -> None:
        """Write the staleness fingerprints to disk so future turns/sessions reuse
        them. Atomic replace; read-captured fingerprints survive a restart."""
        if self._fingerprint_path is None:
            return
        try:
            from .atomicio import atomic_write_text

            payload = json.dumps(
                {k: list(v) for k, v in self._fingerprints.items()},
                ensure_ascii=False,
                sort_keys=True,
            )
            atomic_write_text(self._fingerprint_path, payload)
        except OSError:
            pass

    def _record_fingerprint(self, target: Path) -> None:
        try:
            self._fingerprints[self._safe_rel_path(target)] = self._fingerprint(target)
        except (OSError, ValueError):
            return
        self._persist_fingerprints()

    def _ensure_fresh(self, target: Path) -> None:
        """Reject edits to files that changed since the agent last read them.

        Mirrors the staleness guard used by leading coding agents: writing over
        content the model has not seen can silently clobber newer changes.
        """
        try:
            rel = self.rel_path(target)
        except ValueError:
            return
        recorded = self._fingerprints.get(rel)
        if recorded is None:
            return
        current = self._fingerprint(target)
        if current is None:
            # File was deleted since the agent read it: there is no content to
            # clobber, so (re)creating it is safe. Drop the stale fingerprint.
            self._fingerprints.pop(rel, None)
            self._persist_fingerprints()
            return
        if current != recorded:
            raise ValueError(
                f"File changed since it was last read: {rel}. "
                "Re-read the file before editing to avoid overwriting newer content."
            )

    def _capture_change(
        self,
        change_store: Any,
        audit_context: dict[str, Any] | None,
        turn_index: int,
        tool_name: str,
        file_path: str,
        kind: str,
        before: str | None,
        after: str | None,
        file_existed: bool = True,
    ) -> None:
        if change_store is None:
            return
        session_id = str((audit_context or {}).get("session_id") or "")
        if not session_id:
            return
        try:
            change_store.record(
                session_id=session_id,
                turn_index=int(turn_index or 1),
                tool_name=tool_name,
                file_path=file_path,
                kind=kind,
                before=before,
                after=after,
                file_existed=bool(file_existed),
            )
        except Exception:
            # Change capture must never mask the tool's real outcome.
            return

    def write_text(
        self,
        file_path: str,
        content: str,
        audit_context: dict[str, Any] | None = None,
        change_store: Any = None,
        turn_index: int = 1,
    ) -> None:
        details: dict[str, Any] = {"path": file_path, "bytes": len(content.encode("utf-8"))}
        before: str | None = None
        file_existed = False
        try:
            target = self.resolve_write_path(file_path)

            details["path"] = self._safe_rel_path(target)
            self._ensure_fresh(target)
            if target.is_file():
                before = target.read_text(encoding="utf-8", errors="replace")
                file_existed = True
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._capture_change(change_store, audit_context, turn_index, "write_file", details["path"], "write", before, content, file_existed=file_existed)
            self._record_fingerprint(target)
            self.audit_tool_action("write_file", "success", details, audit_context)
        except Exception as exc:
            self.audit_tool_action("write_file", "error", {**details, "error": str(exc)[:240]}, audit_context)
            raise

    def replace_text(
        self,
        file_path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        audit_context: dict[str, Any] | None = None,
        change_store: Any = None,
        turn_index: int = 1,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {
            "path": file_path,
            "old_text_chars": len(old_text),
            "new_text_chars": len(new_text),
            "replace_all": replace_all,
        }
        try:
            if not old_text:
                raise ValueError("old_text is required")

            target = self.resolve_write_path(file_path)
            details["path"] = self._safe_rel_path(target)
            if not target.is_file():
                raise ValueError(f"Not a file: {file_path}")
            if not self.is_text_file(target):
                raise ValueError(f"Not a text file: {file_path}")
            self._ensure_fresh(target)

            content = target.read_text(encoding="utf-8", errors="replace")
            occurrences = content.count(old_text)
            details["occurrences"] = occurrences
            if occurrences == 0:
                raise ValueError(f"old_text was not found in {file_path}")
            if occurrences > 1 and not replace_all:
                raise ValueError(f"old_text appears {occurrences} times in {file_path}; set replace_all to true to replace every occurrence")

            updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
            target.write_text(updated, encoding="utf-8")
            result = {
                "path": self._safe_rel_path(target),
                "replacements": occurrences if replace_all else 1,
                "remaining_occurrences": 0 if replace_all else max(occurrences - 1, 0),
            }
            self._capture_change(change_store, audit_context, turn_index, "replace_in_file", details["path"], "edit", content, updated)
            self._record_fingerprint(target)
            self.audit_tool_action("replace_in_file", "success", {**details, **result}, audit_context)
            return result
        except Exception as exc:
            self.audit_tool_action("replace_in_file", "error", {**details, "error": str(exc)[:240]}, audit_context)
            raise

    def apply_text_edits(
        self,
        file_path: str,
        edits: list[dict[str, Any]],
        audit_context: dict[str, Any] | None = None,
        change_store: Any = None,
        turn_index: int = 1,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {"path": file_path, "edit_count": len(edits) if isinstance(edits, list) else 0}
        try:
            if not edits:
                 raise ValueError("edits must be a non-empty array")

            target = self.resolve_write_path(file_path)
            details["path"] = self._safe_rel_path(target)
            if not target.is_file():
                raise ValueError(f"Not a file: {file_path}")
            if not self.is_text_file(target):
                raise ValueError(f"Not a text file: {file_path}")
            self._ensure_fresh(target)

            content = target.read_text(encoding="utf-8", errors="replace")
            updated = content
            results: list[dict[str, Any]] = []
            for index, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    raise ValueError(f"edit {index + 1} must be an object")
                old_text = str(edit.get("old_text") or "")
                new_text = str(edit.get("new_text") or "")
                replace_all = bool(edit.get("replace_all") or False)
                if not old_text:
                    raise ValueError(f"edit {index + 1} old_text is required")

                occurrences = updated.count(old_text)
                if occurrences == 0:
                    raise ValueError(f"edit {index + 1} old_text was not found in {file_path}")
                if occurrences > 1 and not replace_all:
                    raise ValueError(f"edit {index + 1} old_text appears {occurrences} times in {file_path}; set replace_all to true")

                replacements = occurrences if replace_all else 1
                updated = updated.replace(old_text, new_text) if replace_all else updated.replace(old_text, new_text, 1)
                results.append(
                    {
                        "index": index + 1,
                        "old_text_chars": len(old_text),
                        "new_text_chars": len(new_text),
                        "replace_all": replace_all,
                        "replacements": replacements,
                    }
                )

            target.write_text(updated, encoding="utf-8")
            result = {
                "path": self._safe_rel_path(target),
                "edit_count": len(edits),
                "replacements": sum(item["replacements"] for item in results),
                "edits": results,
            }
            self._capture_change(change_store, audit_context, turn_index, "apply_text_edits", details["path"], "edit", content, updated)
            self._record_fingerprint(target)
            self.audit_tool_action("apply_text_edits", "success", {**details, **result}, audit_context)
            return result
        except Exception as exc:
            self.audit_tool_action("apply_text_edits", "error", {**details, "error": str(exc)[:240]}, audit_context)
            raise

    def run_command(
        self,
        command: list[str],
        cwd: str = "",
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        audit_context: dict[str, Any] | None = None,
        approval_store: CommandApprovalStore | None = None,
        require_approval: bool = False,
    ) -> dict[str, Any]:
        """Run an allowlisted command, optionally gated by a synchronous
        approval check.

        ``require_approval`` is a *synchronous* approval flow used only by the
        bottom-panel manual terminal (``/workspace/command``): the caller
        issues the command, and if no approved digest exists it returns an
        ``approval_required`` payload the UI resolves via the same
        ``CommandApprovalStore`` as the agent HITL flow.

        The agent path does NOT use this flag — LangChain's
        ``HumanInTheLoopMiddleware`` interrupts the graph *before* the tool
        runs, so the command here always executes with
        ``require_approval=False`` and a digested, already-approved store.
        The two mechanisms intentionally share the one approval store.
        """
        details: dict[str, Any] = {
            "command": self.redact_command(command) if isinstance(command, list) else command,
            "cwd": cwd,
            "timeout_seconds": timeout_seconds,
        }
        try:
            if not command or not all(isinstance(part, str) and part for part in command):
                 raise ValueError("command must be a non-empty string array")

            working_dir = self.resolve_write_path(cwd)
            if not working_dir.is_dir():
                raise ValueError(f"Command cwd is not a directory: {cwd or '.'}")

            executable = self.resolve_executable(command[0], working_dir)
            safe_timeout = max(1, min(int(timeout_seconds or DEFAULT_COMMAND_TIMEOUT_SECONDS), MAX_COMMAND_TIMEOUT_SECONDS))
            safe_command = [executable, *command[1:]]
            details["cwd"] = self._safe_rel_path(working_dir) if working_dir != self.root else ""
            details["timeout_seconds"] = safe_timeout

            if require_approval:
                if not approval_store:
                    raise ValueError("command approval store is required")
                digest = self.command_digest(command, details["cwd"])
                if approval_store.is_always_allowed(digest):
                    details["approval_id"] = "always_allowed"
                else:
                    approval = approval_store.consume(digest)
                    if not approval:
                        approval = approval_store.request(
                            digest,
                            self.redact_command(command),
                            details["cwd"],
                            safe_timeout,
                            audit_context,
                        )
                        if approval["status"] == "denied":
                            result = {
                                "approval_required": False,
                                "approval_id": approval["id"],
                                "approval_status": approval["status"],
                                "command": command,
                                "cwd": details["cwd"],
                                "return_code": None,
                                "timed_out": False,
                                "stdout": "",
                                "stderr": f"Command approval denied: {approval['id']}",
                                "stdout_truncated": False,
                                "stderr_truncated": False,
                            }
                            self.audit_tool_action("run_command", "approval_denied", {**details, "approval_id": approval["id"]}, audit_context)
                            return result
                        result = {
                            "approval_required": True,
                            "approval_id": approval["id"],
                            "approval_status": approval["status"],
                            "command": command,
                            "cwd": details["cwd"],
                            "return_code": None,
                            "timed_out": False,
                            "stdout": "",
                            "stderr": f"Command approval required: {approval['id']}",
                            "stdout_truncated": False,
                            "stderr_truncated": False,
                        }
                        self.audit_tool_action("run_command", "approval_required", {**details, "approval_id": approval["id"]}, audit_context)
                        return result
                    details["approval_id"] = approval["id"]

            try:
                completed = subprocess.run(
                    safe_command,
                    cwd=str(working_dir),
                    capture_output=True,
                    text=True,
                    timeout=safe_timeout,
                    shell=False,
                )
                timed_out = False
                return_code = completed.returncode
                stdout = completed.stdout
                stderr = completed.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                return_code = None
                stdout = self.decode_process_output(exc.stdout)
                stderr = self.decode_process_output(exc.stderr) or f"Command timed out after {safe_timeout} seconds"

            result = {
                "command": command,
                "cwd": details["cwd"],
                "return_code": return_code,
                "timed_out": timed_out,
                "stdout": stdout[:MAX_COMMAND_OUTPUT_CHARS],
                "stderr": stderr[:MAX_COMMAND_OUTPUT_CHARS],
                "stdout_truncated": len(stdout) > MAX_COMMAND_OUTPUT_CHARS,
                "stderr_truncated": len(stderr) > MAX_COMMAND_OUTPUT_CHARS,
            }
            self.audit_tool_action(
                "run_command",
                "success",
                {
                    **details,
                    "return_code": return_code,
                    "timed_out": timed_out,
                    "stdout_chars": len(stdout),
                    "stderr_chars": len(stderr),
                    "stdout_truncated": result["stdout_truncated"],
                    "stderr_truncated": result["stderr_truncated"],
                },
                audit_context,
            )
            return result
        except Exception as exc:
            self.audit_tool_action("run_command", "error", {**details, "error": str(exc)[:240]}, audit_context)
            raise

    @staticmethod
    def command_digest(command: list[str], cwd: str) -> str:
        # Canonicalize the cwd so "always allow" given for "." / "./" / "" matches
        # the digest computed at run time (which normalizes the resolved cwd to ""
        # at the workspace root). Without this, the middleware's pre-check (raw
        # tool-call cwd) never matches the allowlist entry recorded with a
        # normalized cwd, so always-allow silently fails.
        normalized_cwd = str(cwd or "").strip()
        if normalized_cwd in ("", ".", "./"):
            normalized_cwd = ""
        payload = json.dumps(
            {"command": command, "cwd": normalized_cwd}, ensure_ascii=False, sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def resolve_executable(self, executable: str, cwd: Path) -> str:
        executable_path = Path(executable)
        command_name = executable_path.name
        if command_name not in ALLOWED_COMMANDS:
            raise ValueError(
                f"Command is not allowed: {command_name}. "
                "This is a fixed workspace restriction that user approval cannot override. "
                f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}."
            )

        if executable_path.is_absolute():
            resolved = executable_path.resolve()
            if resolved != self.root and self.root not in resolved.parents:
                raise ValueError(f"Executable is outside the workspace: {executable}")
            return str(resolved)

        if "/" in executable or "\\" in executable:
            resolved = (cwd / executable_path).resolve()
            if resolved != self.root and self.root not in resolved.parents:
                raise ValueError(f"Executable is outside the workspace: {executable}")
            return str(resolved)

        return command_name

    @staticmethod
    def decode_process_output(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def revert_change(self, change: dict[str, Any]) -> dict[str, Any]:
        """Safely revert a single recorded change.

        Prefers an exact whole-file restore when the file still matches the
        recorded ``after`` state. Otherwise it falls back to a hunk-level
        inverse edit so that edits made by *other* sessions to unrelated
        regions of the same file are preserved. If the recorded hunks cannot
        be matched (e.g. another session edited the same region), a conflict is
        reported and the file is left untouched — never clobbering others'
        changes.

        Returns ``{"status": "reverted"|"conflict", path, ...}``.
        """
        file_path = str(change.get("file_path") or "")
        change_id = str(change.get("id") or "")
        result: dict[str, Any] = {"status": "conflict", "path": file_path, "reason": ""}
        if change_id:
            result["id"] = change_id
        if not file_path:
            result["reason"] = "missing file path"
            return result

        kind = str(change.get("kind") or "edit")
        file_existed = bool(change.get("file_existed"))
        too_large = bool(change.get("too_large"))
        after = change.get("after")
        before = change.get("before")
        hunks = change.get("hunks") or []

        try:
            target = self.resolve_write_path(file_path)
        except (PathBoundaryError, OSError) as exc:
            result["reason"] = str(exc)[:200]
            return result

        if file_existed and not target.is_file():
            result["reason"] = "file was deleted after this change"
            return result
        if not file_existed and not target.is_file():
            # File never existed and still does not: nothing to revert.
            return {"status": "reverted", "path": file_path, "kind": kind, "added": 0, "removed": 0, "noop": True, **({"id": change_id} if change_id else {})}

        current = target.read_text(encoding="utf-8", errors="replace")

        # New file: only a clean delete is safe.
        if not file_existed:
            if after is not None and current == after:
                target.unlink(missing_ok=True)
                self._fingerprints.pop(self._safe_rel_path(target), None); self._persist_fingerprints()
                return {"status": "reverted", "path": file_path, "kind": kind, "added": int(change.get("added") or 0), "removed": int(change.get("removed") or 0), "deleted": True, **({"id": change_id} if change_id else {})}
            result["reason"] = "file changed after it was created; refusing to delete"
            return result

        # Exact whole-file restore (the common single-session case).
        if after is not None and before is not None and current == after:
            try:
                if before == "":
                    target.unlink(missing_ok=True)
                else:
                    target.write_text(before, encoding="utf-8")
            except OSError as exc:
                result["reason"] = str(exc)[:200]
                return result
            self._fingerprints.pop(self._safe_rel_path(target), None); self._persist_fingerprints()
            return {"status": "reverted", "path": file_path, "kind": kind, "added": int(change.get("added") or 0), "removed": int(change.get("removed") or 0), "deleted": before == "", **({"id": change_id} if change_id else {})}

        # File diverged (another session/user edited it): try hunk-level inverse
        # so unrelated regions stay untouched. Whole-file overwrites and
        # oversized changes cannot be partially reverted safely.
        if too_large or kind == "write" or not hunks:
            result["reason"] = "file changed since this session edited it; refusing to overwrite"
            return result

        reverted_text = self._apply_inverse_hunks(current, hunks)
        if reverted_text is None:
            result["reason"] = "another session changed the same region; refusing to overwrite"
            return result
        try:
            target.write_text(reverted_text, encoding="utf-8")
        except OSError as exc:
            result["reason"] = str(exc)[:200]
            return result
        self._fingerprints.pop(self._safe_rel_path(target), None); self._persist_fingerprints()
        return {"status": "reverted", "path": file_path, "kind": kind, "added": int(change.get("added") or 0), "removed": int(change.get("removed") or 0), **({"id": change_id} if change_id else {})}

    def redo_change(self, change: dict[str, Any]) -> dict[str, Any]:
        """Re-apply a change that was reverted by editing a message (undo-the-undo).

        Restores the recorded ``after`` content for a reverted change. The file
        must still match the recorded ``before`` state (the state the revert left
        it in); otherwise a conflict is reported and the file is left untouched.
        Records whose full content was too large to capture cannot be restored
        safely and are reported as conflicts.

        Returns ``{"status": "restored"|"conflict", path, ...}``.
        """
        file_path = str(change.get("file_path") or "")
        change_id = str(change.get("id") or "")
        result: dict[str, Any] = {"status": "conflict", "path": file_path, "reason": ""}
        if change_id:
            result["id"] = change_id
        if not file_path:
            result["reason"] = "missing file path"
            return result

        kind = str(change.get("kind") or "edit")
        file_existed = bool(change.get("file_existed"))
        too_large = bool(change.get("too_large"))
        before = change.get("before")
        after = change.get("after")

        if too_large or after is None:
            result["reason"] = "oversized change cannot be re-applied safely"
            return result

        try:
            target = self.resolve_write_path(file_path)
        except (PathBoundaryError, OSError) as exc:
            result["reason"] = str(exc)[:200]
            return result

        if file_existed:
            if not target.is_file():
                if before == "":
                    # The file existed but was empty; revert deleted it. Recreate.
                    pass
                else:
                    result["reason"] = "file was deleted after the revert"
                    return result
            else:
                current = target.read_text(encoding="utf-8", errors="replace")
                if before is not None and current != before:
                    result["reason"] = "file changed since it was reverted; refusing to overwrite"
                    return result
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(after, encoding="utf-8")
            except OSError as exc:
                result["reason"] = str(exc)[:200]
                return result
        else:
            # File was created by the change and deleted on revert. Recreate.
            if target.exists():
                result["reason"] = "file recreated after the revert; refusing to overwrite"
                return result
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(after, encoding="utf-8")
            except OSError as exc:
                result["reason"] = str(exc)[:200]
                return result
        self._fingerprints.pop(self._safe_rel_path(target), None); self._persist_fingerprints()
        return {"status": "restored", "path": file_path, "kind": kind, **({"id": change_id} if change_id else {})}

    @staticmethod
    def _apply_inverse_hunks(content: str, hunks: list[dict[str, Any]]) -> str | None:
        """Apply the inverse of structured hunks to ``content``.

        Each hunk records the before-region (context + del lines) and the
        after-region (context + add lines). Reverting replaces the after-region
        with the before-region. Hunks are processed bottom-up so earlier line
        positions stay valid. Returns ``None`` if any hunk cannot be located.
        """
        had_trailing = content.endswith("\n")
        lines = content.splitlines()

        for hunk in reversed(hunks or []):
            after_region = [line.get("text", "") for line in hunk.get("lines", []) if line.get("type") in ("context", "add")]
            before_region = [line.get("text", "") for line in hunk.get("lines", []) if line.get("type") in ("context", "del")]
            if not after_region:
                continue
            match = _find_sequence(lines, after_region)
            if match < 0:
                return None
            lines[match : match + len(after_region)] = before_region

        result = "\n".join(lines)
        if had_trailing:
            result += "\n"
        return result

    def audit_tool_action(
        self,
        operation: str,
        status: str,
        details: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> None:
        append_tool_audit(self.audit_path, operation, status, details, context)

    @staticmethod
    def redact_command(command: list[str]) -> list[str]:
        redacted: list[str] = []
        redact_next = False
        sensitive_words = ("key", "token", "secret", "password", "authorization")
        for part in command:
            lower = part.lower()
            if redact_next:
                redacted.append("[redacted]")
                redact_next = False
                continue
            if any(word in lower for word in sensitive_words):
                if "=" in part:
                    key, _ = part.split("=", 1)
                    redacted.append(f"{key}=[redacted]")
                else:
                    redacted.append(part)
                    redact_next = True
                continue
            redacted.append(part)
        return redacted

    def list_dir(self, rel_path: str = "") -> list[dict[str, Any]]:
        target = self.resolve_read_path(rel_path)
        if not target.is_dir():
            raise ValueError(f"Not a directory: {rel_path or '.'}")
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return entries
        for child in children:
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            if is_dir and child.name in DEFAULT_IGNORED_DIRS:
                continue
            try:
                is_file = child.is_file()
                size = child.stat().st_size if is_file else None
            except OSError:
                continue
            entries.append(
                 {
                     "name": child.name,
                     "path": self._safe_rel_path(child),
                    "type": "dir" if is_dir else "file",
                    "size": size,
                }
            )
        return entries

    def build_tree(self, rel_path: str = "", depth: int = 3) -> dict[str, Any]:
        target = self.resolve_read_path(rel_path)
        node: dict[str, Any] = {
             "name": target.name or self.root.name,
             "path": self._safe_rel_path(target),
            "type": "dir",
        }
        if depth <= 0:
            return node
        node["children"] = []
        try:
            children = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return node
        for child in children:
            try:
                if child.is_dir():
                     if child.name in DEFAULT_IGNORED_DIRS:
                         continue
                     node["children"].append(self.build_tree(self._safe_rel_path(child), depth=depth - 1))
                else:
                    node["children"].append(
                         {
                             "name": child.name,
                             "path": self._safe_rel_path(child),
                            "type": "file",
                            "size": child.stat().st_size,
                        }
                    )
            except OSError:
                # Broken/dangling symlink or unreadable entry: skip rather than 500.
                continue
        return node

    @staticmethod
    def is_text_file(path: Path) -> bool:
        try:
            mime, _ = mimetypes.guess_type(str(path))
        except Exception:
            mime = None
        if mime and mime.startswith("text/"):
            return True
        if mime in {"application/json", "application/xml", "application/yaml", "application/x-yaml"}:
            return True
        if path.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".toml", ".txt", ".sh", ".lock"}:
            return True
        return False

    def read_preview(self, file_path: str, max_chars: int = 100_000) -> dict[str, Any]:
        target = self.resolve_read_path(file_path)
        if not target.is_file():
            raise ValueError(f"Not a file: {file_path}")
        if not self.is_text_file(target):
            return {"content": None, "binary": True, "size": target.stat().st_size}
        content = target.read_text(encoding="utf-8", errors="replace")
        self._record_fingerprint(target)
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        return {"content": content, "binary": False, "size": target.stat().st_size, "truncated": truncated}

    def search_text(
        self,
        query: str,
        rel_path: str = "",
        max_results: int = DEFAULT_SEARCH_MAX_RESULTS,
        max_file_bytes: int = DEFAULT_SEARCH_MAX_FILE_BYTES,
    ) -> dict[str, Any]:
        needle = query.strip()
        if not needle:
            raise ValueError("search query is required")

        target = self.resolve_read_path(rel_path)
        if not target.exists():
            raise ValueError(f"Path not found: {rel_path or '.'}")

        limit = max(1, min(max_results, DEFAULT_SEARCH_MAX_RESULTS))
        results: list[dict[str, Any]] = []
        searched_files = 0
        skipped_files = 0
        needle_lower = needle.lower()

        for path in self.walk_files(target):
            if len(results) >= limit:
                break
            try:
                stat = path.stat()
            except OSError:
                skipped_files += 1
                continue
            if stat.st_size > max_file_bytes or not self.is_text_file(path):
                skipped_files += 1
                continue

            searched_files += 1
            try:
                for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                    if needle_lower not in line.lower():
                        continue
                    results.append(
                        {
                            "path": self._safe_rel_path(path),
                            "line": line_number,
                            "preview": line.strip()[:240],
                        }
                    )
                    if len(results) >= limit:
                        break
            except OSError:
                skipped_files += 1

        return {
            "query": needle,
            "path": self._safe_rel_path(target) if target != self.root else "",
            "results": results,
            "result_count": len(results),
            "searched_files": searched_files,
            "skipped_files": skipped_files,
            "truncated": len(results) >= limit,
        }

    def walk_files(self, root: Path):
        if root.is_file():
            yield root
            return
        if not root.is_dir():
            raise ValueError(f"Not a file or directory: {self._safe_rel_path(root)}")

        # Guard against directory-symlink cycles (e.g. `ln -s . loop`): track
        # the resolved real path of every directory we descend into and skip
        # any already-visited one, so a cycle cannot recurse forever.
        visited: set[str] = set()

        def _walk(dirpath: Path) -> Any:
            try:
                real = str(dirpath.resolve())
            except OSError:
                return
            if real in visited:
                return
            visited.add(real)
            try:
                children = sorted(dirpath.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                return
            for child in children:
                try:
                    if child.is_dir():
                        if child.name in DEFAULT_IGNORED_DIRS:
                            continue
                        yield from _walk(child)
                    elif child.is_file():
                        yield child
                except OSError:
                    continue

        yield from _walk(root)

    def rel_path(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def _find_sequence(haystack: list[str], needle: list[str]) -> int:
    """Return the index of the first occurrence of ``needle`` in ``haystack`` or -1."""
    if not needle:
        return -1
    n = len(haystack)
    m = len(needle)
    if m > n:
        return -1
    for i in range(n - m + 1):
        if haystack[i : i + m] == needle:
            return i
    return -1


def append_tool_audit(
    audit_path: Path | None,
    operation: str,
    status: str,
    details: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> None:
    """Append one JSONL audit event. Never raises.

    Shared by :meth:`Workspace.audit_tool_action` and the MCP middleware, so
    external MCP calls land in the same audit trail as builtin tool actions.
    """
    if not audit_path:
        return
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "status": status,
        "context": context or {},
        "details": details,
    }
    append_jsonl_retained(audit_path, event, ACTIVE_TOOL_AUDIT_RETENTION)


def list_tool_audit_events(audit_path: Path, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 100), 200))
    if not audit_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return list(reversed(events[-safe_limit:]))


def trim_jsonl_file(path: Path, max_lines: int) -> None:
    """Rolling retention for append-only JSONL logs: keep only the last
    ``max_lines`` lines. Reads+rewrites only when over the cap, so steady-state
    writes stay cheap."""
    trim_jsonl(path, max_lines)


# git_status output budget: bounded at the source so a large working tree can
# never inject megabytes of diff into the model context. Per-file diffs are
# truncated to GIT_MAX_PER_FILE_DIFF_CHARS (keeping path/added/removed stats),
# the file list is capped at GIT_MAX_FILES, and the whole diff body at
# GIT_MAX_DIFF_CHARS.
GIT_MAX_FILES = 50
GIT_MAX_DIFF_CHARS = 100_000
GIT_MAX_PER_FILE_DIFF_CHARS = 2_000
GIT_COMMAND_TIMEOUT_SECONDS = 5.0


def git_is_repo(workspace_root: Path) -> bool:
    probe = (workspace_root / ".git")
    return probe.exists() or probe.is_file()


def _git_run(workspace_root: Path, args: list[str], timeout: float = GIT_COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_git_diff_sections(diff_text: str) -> dict[str, str]:
    """Split ``git diff HEAD`` output into per-file unified diff sections.

    Returns a mapping of new-file path (relative to the repo root) to its
    diff body (the ``@@`` hunks and body lines).
    """
    sections: dict[str, str] = {}
    current_path: str | None = None
    current_lines: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_path:
                sections[current_path] = "".join(current_lines)
            current_path = None
            current_lines = []
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target.startswith("b/"):
                current_path = target[2:]
            continue
        if current_path is None:
            continue
        current_lines.append(line)
    if current_path:
        sections[current_path] = "".join(current_lines)
    return sections


def workspace_git_diff(workspace_root: Path) -> dict[str, Any]:
    """Return the current working-tree diff (``git diff HEAD``) plus untracked files.

    When the workspace is not a git repository (or git is unavailable) this
    returns ``git: False`` so callers can fall back to session-scoped changes.
    """
    result: dict[str, Any] = {
        "git": False,
        "workspace": str(workspace_root),
        "files": [],
        "untracked": [],
        "truncated_diff": False,
        "note": "not a git repository",
    }
    if not git_is_repo(workspace_root):
        return result

    stat_proc = _git_run(workspace_root, ["diff", "HEAD", "--numstat"])
    if stat_proc is None or stat_proc.returncode != 0:
        result["note"] = "git unavailable"
        return result

    files: list[dict[str, Any]] = []
    skipped = 0
    for line in stat_proc.stdout.splitlines():
        if not line.strip():
            continue
        if len(files) >= GIT_MAX_FILES:
            skipped += 1
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], "\t".join(parts[2:])
        if added == "-" or removed == "-":
            files.append({"path": path, "added": 0, "removed": 0, "binary": True, "diff": ""})
            continue
        files.append({"path": path, "added": int(added or 0), "removed": int(removed or 0), "binary": False, "diff": ""})

    truncated_diff = False
    diff_proc = _git_run(workspace_root, ["diff", "HEAD"])
    if diff_proc is not None and diff_proc.returncode == 0:
        diff_text = diff_proc.stdout
        if len(diff_text) > GIT_MAX_DIFF_CHARS:
            diff_text = diff_text[:GIT_MAX_DIFF_CHARS]
            truncated_diff = True
        sections = _parse_git_diff_sections(diff_text)
        for file_entry in files:
            if file_entry.get("binary"):
                continue
            body = sections.get(file_entry["path"], "")
            if body:
                if len(body) > GIT_MAX_PER_FILE_DIFF_CHARS:
                    body = body[:GIT_MAX_PER_FILE_DIFF_CHARS] + "\n…[diff truncated]"
                    truncated_diff = True
                file_entry["diff"] = body

    untracked: list[str] = []
    ut_proc = _git_run(workspace_root, ["ls-files", "--others", "--exclude-standard"])
    if ut_proc is not None and ut_proc.returncode == 0:
        untracked = [line for line in ut_proc.stdout.splitlines() if line.strip()]

    result.update(
        {
            "git": True,
            "files": files,
            "untracked": untracked,
            "truncated_diff": truncated_diff,
            "note": "" if skipped == 0 else f"showing first {GIT_MAX_FILES} files",
        }
    )
    return result


def workspace_git_branch(workspace_root: Path) -> dict[str, Any]:
    """Return the current git branch for the workspace.

    Returns ``is_repo=False`` when the path is not a git repository, and
    ``branch=None`` when it is a repository but in a detached HEAD state.
    """
    if not git_is_repo(workspace_root):
        return {"is_repo": False, "branch": None}
    proc = _git_run(workspace_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if proc is None or proc.returncode != 0:
        return {"is_repo": True, "branch": None}
    branch = proc.stdout.strip()
    if not branch or branch == "HEAD":
        # Detached HEAD — surface the short commit hash for context.
        sha_proc = _git_run(workspace_root, ["rev-parse", "--short", "HEAD"])
        branch = sha_proc.stdout.strip() if sha_proc and sha_proc.returncode == 0 else None
    return {"is_repo": True, "branch": branch or None}
