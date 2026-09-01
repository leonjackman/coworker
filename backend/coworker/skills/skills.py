"""Skill entry model, frontmatter parsing, and validation.

A skill is a directory containing a ``SKILL.md`` file. The YAML frontmatter
declares metadata (only ``name`` and ``description`` are required); the rest of
the file is the instruction body loaded on demand by the agent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# Agent Skills standard limits.
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_FILE_BYTES = 256 * 1024  # 256 KiB

# name: lowercase alphanumeric with single hyphen separators.
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Known frontmatter keys (unknown keys are ignored).
_KNOWN_KEYS = frozenset(
    {
        "name",
        "description",
        "version",
        "license",
        "author",
        "platforms",
        "metadata",
        "compatibility",
        "disable-model-invocation",
        "user-invocable",
        "allowed-tools",
        "disallowed-tools",
        "model",
        "effort",
        "context",
        "paths",
        "invocation",
        "when_to_use",
        "prerequisites",
        "required_environment_variables",
        "setup",
        "provenance",
        "status",
        "sources",
        "created_at",
        "bundle",
    }
)


@dataclass(frozen=True)
class SkillCommand:
    """A sub-command exposed by a skill package.

    Rendered in the chat ``/`` menu as ``/<name>`` with the owning package
    shown as a secondary label. Instructions live in a file relative to the
    package directory (default ``commands/<name>.md``).
    """

    name: str
    description: str
    file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "file": self.file}


@dataclass(frozen=True)
class SkillEntry:
    """A discovered skill (catalog view; body loaded on demand)."""

    name: str
    description: str
    file_path: Path
    base_dir: Path
    source: str  # "user" | "project" | "coworker-user" | "coworker-project"
    version: str = ""
    disable_model_invocation: bool = False
    enabled: bool = True
    commands: list[SkillCommand] = field(default_factory=list)
    # ── self-calibration metadata ─────────────────────────────────────
    provenance: str = "user"  # "user" | "market" | "agent"
    status: str = "active"  # "active" | "draft" (draft = waiting for approval)
    sources: list[str] = field(default_factory=list)  # evidence chain
    created_at: str = ""
    bundle: str = ""  # product grouping (e.g. "agent-learned", "custom", "market")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "file_path": str(self.file_path),
            "base_dir": str(self.base_dir),
            "source": self.source,
            "version": self.version,
            "disable_model_invocation": self.disable_model_invocation,
            "enabled": self.enabled,
            "commands": [c.to_dict() for c in self.commands],
            "provenance": self.provenance,
            "status": self.status,
            "sources": list(self.sources),
            "created_at": self.created_at,
            "bundle": self.bundle,
        }


@dataclass(frozen=True)
class SkillDiagnostic:
    """A validation problem found while scanning (invalid / collision)."""

    type: str  # "invalid" | "collision"
    name: str
    path: Path | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "path": str(self.path) if self.path is not None else None,
            "message": self.message,
        }


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Returns ``(frontmatter, body)``; ``({}, content)`` when there is no valid
    frontmatter block. Malformed YAML is treated as "no frontmatter" so a
    single broken skill never breaks the whole scan.
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    raw = content[3:end].strip()
    body = content[end + 4 :].lstrip("\n")
    if not raw:
        return {}, body
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is a project dependency
        return {}, body
    try:
        data = yaml.safe_load(raw)
    except Exception:
        return {}, body
    return (data if isinstance(data, dict) else {}), body


def _frontmatter_str(frontmatter: dict[str, Any], key: str) -> str:
    value = frontmatter.get(key)
    return value if isinstance(value, str) else ""


def set_frontmatter_value(content: str, key: str, value: str) -> str:
    """Set a scalar frontmatter key in a SKILL.md string, preserving formatting.

    Replaces an existing ``key:`` line in place; injects one before the closing
    ``---`` when absent; prepends a fresh frontmatter block when the file has
    none. Values that YAML would coerce (ISO timestamps, numbers, booleans) are
    quoted so they stay strings. Returns the (possibly unchanged) content.
    """
    import re as _re

    if _re.search(r"[:#\[\]{},&*!|>'\"]", value) or _re.fullmatch(r"-?\d+(\.\d+)?", value) or value.lower() in {
        "true",
        "false",
        "null",
        "yes",
        "no",
        "on",
        "off",
    }:
        value = f'"{value.replace(chr(34), chr(92) + chr(34))}"'
    if not content.startswith("---"):
        body = content.lstrip("\n")
        return f"---\n{key}: {value}\n---\n\n{body}"
    lines = content.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if close is None:
        return content
    replaced = False
    out: list[str] = []
    for i, line in enumerate(lines[:close]):
        if i == 0:
            out.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith(f"{key}:") or stripped.startswith(f"{key} :"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}{key}: {value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}: {value}")
    out.extend(lines[close:])
    return "\n".join(out)


def set_frontmatter_list(content: str, key: str, items: list[str]) -> str:
    """Set a list-valued frontmatter key (e.g. ``sources``) in a SKILL.md string.

    Replaces an existing ``key:`` block (including its indented list items), or
    injects one before the closing ``---``. Returns the (possibly unchanged)
    content.
    """
    import json as _json

    def _scalar(item: str) -> str:
        if item.startswith(("'", '"')):
            return item
        return _json.dumps(item, ensure_ascii=False)

    block_lines = [f"{key}:"] + [f"  - {_scalar(i)}" for i in items]
    if not content.startswith("---"):
        return f"---\n" + "\n".join(block_lines) + "\n---\n\n" + content.lstrip("\n")
    lines = content.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if close is None:
        return content
    key_idx = next(
        (i for i in range(1, close) if lines[i].strip() == key or lines[i].strip().startswith(key + ":")),
        None,
    )
    if key_idx is None:
        lines[close:close] = block_lines
        return "\n".join(lines)
    end = key_idx + 1
    while end < close and (lines[end].startswith(" ") or lines[end].startswith("\t")):
        end += 1
    return "\n".join(lines[:key_idx] + block_lines + lines[end:])


def _parse_commands(frontmatter: dict[str, Any]) -> list[SkillCommand]:
    """Parse the ``commands`` frontmatter list into validated SkillCommand objects.

    Unknown keys are ignored; entries without a valid ``name`` (lowercase
    alphanumeric + hyphens) or with a duplicate name are skipped so a single
    broken command never breaks the whole skill.
    """
    raw = frontmatter.get("commands")
    if not isinstance(raw, list):
        return []
    commands: list[SkillCommand] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        cname = _frontmatter_str(item, "name").strip()
        cdesc = _frontmatter_str(item, "description").strip()
        if not cname or not _NAME_RE.match(cname):
            continue
        if cname in seen:
            continue
        seen.add(cname)
        cfile = _frontmatter_str(item, "file").strip()
        if not cfile:
            cfile = f"commands/{cname}.md"
        # Only allow relative paths inside the skill package: a command file must
        # never be absolute or escape the skill directory (e.g. ../../.ssh/...).
        if cfile.startswith("/") or "\\" in cfile or any(seg == ".." for seg in cfile.split("/")):
            continue
        commands.append(SkillCommand(name=cname, description=cdesc, file=cfile))
    return commands


def validate_name(name: str) -> list[str]:
    """Return validation errors for a skill name (empty when valid)."""
    errors: list[str] = []
    if not name:
        errors.append("name is required")
        return errors
    if len(name) > MAX_NAME_LENGTH:
        errors.append(f"name exceeds {MAX_NAME_LENGTH} characters")
    if not _NAME_RE.match(name):
        errors.append("name must be lowercase alphanumeric with single hyphen separators")
    return errors


def validate_description(description: str) -> list[str]:
    """Return validation errors for a skill description (empty when valid)."""
    errors: list[str] = []
    if not description or not description.strip():
        errors.append("description is required (the agent uses it to decide when to load the skill)")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"description exceeds {MAX_DESCRIPTION_LENGTH} characters")
    return errors


def content_version(content: str) -> str:
    """Deterministic version marker for the SKILL.md body (prompt cache invalidation)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def load_skill_from_file(
    skill_file: Path,
    source: str,
    *,
    max_bytes: int = MAX_SKILL_FILE_BYTES,
) -> tuple[SkillEntry | None, list[SkillDiagnostic]]:
    """Parse and validate a single ``SKILL.md`` file into a skill entry."""
    diagnostics: list[SkillDiagnostic] = []
    try:
        stat = skill_file.stat()
        if stat.st_size > max_bytes:
            diagnostics.append(
                SkillDiagnostic(
                    "invalid",
                    skill_file.parent.name,
                    skill_file,
                    f"SKILL.md exceeds {max_bytes} bytes",
                )
            )
            return None, diagnostics
        content = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        diagnostics.append(
            SkillDiagnostic("invalid", skill_file.parent.name, skill_file, f"unreadable: {exc}")
        )
        return None, diagnostics

    frontmatter, _body = parse_frontmatter(content)
    fallback_name = skill_file.parent.name.strip()
    name = _frontmatter_str(frontmatter, "name").strip() or fallback_name
    description = _frontmatter_str(frontmatter, "description").strip()

    problems = validate_name(name) + validate_description(description)
    if problems:
        diagnostics.append(
            SkillDiagnostic("invalid", name, skill_file, "; ".join(problems))
        )
        return None, diagnostics

    disable_model_invocation = bool(frontmatter.get("disable-model-invocation", False))
    version_raw = _frontmatter_str(frontmatter, "version").strip()
    version = version_raw or content_version(content)
    commands = _parse_commands(frontmatter)

    provenance_raw = _frontmatter_str(frontmatter, "provenance").strip().lower()
    provenance = provenance_raw if provenance_raw in {"user", "market", "agent"} else "user"
    status_raw = _frontmatter_str(frontmatter, "status").strip().lower()
    status = status_raw if status_raw in {"active", "draft"} else "active"
    sources_raw = frontmatter.get("sources")
    sources = [s for s in sources_raw if isinstance(s, str)] if isinstance(sources_raw, list) else []
    created_at = _frontmatter_str(frontmatter, "created_at").strip()
    bundle = _frontmatter_str(frontmatter, "bundle").strip()

    return (
        SkillEntry(
            name=name,
            description=description,
            file_path=skill_file,
            base_dir=skill_file.parent,
            source=source,
            version=version,
            disable_model_invocation=disable_model_invocation,
            commands=commands,
            provenance=provenance,
            status=status,
            sources=sources,
            created_at=created_at,
            bundle=bundle,
        ),
        diagnostics,
    )


def format_skills_prompt(skills: list[SkillEntry]) -> str:
    """Render the Agent Skills catalog block injected into the system prompt.

    Format follows the agentskills.io integration template (byte-compatible with
    openclaw/pi). Only the catalog is always in context; bodies load on demand.
    """
    if not skills:
        return ""
    lines = [
        "\n\nThe following skills provide specialized instructions for specific tasks.",
        "Only each skill's name + description are shown below; its full SKILL.md body lives "
        "outside the workspace sandbox, so use the dedicated `load_skill` tool (not read_file) "
        "to load a skill's file when the task matches its description.",
        "If a skill's <version> differs from a previous turn, re-load its SKILL.md before using it.",
        "When a skill file references a relative path, resolve it against the skill directory "
        "(parent of SKILL.md) and use that absolute path in tool calls.",
        "",
        "<available_skills>",
    ]
    install_note = (
        "To CREATE or INSTALL a NEW skill from chat (for example when the user asks you to "
        "build/install a skill), you MUST use the dedicated `install_skill` tool with the skill "
        "name and its full SKILL.md content. Do NOT use write_file or run_command to write to the "
        "`~/.agents/skills/...` paths listed in <location> above — those paths live outside the "
        "workspace sandbox and the file tools will reject them. After install_skill succeeds the "
        "skill is immediately available as a `/skill <name>` command (or a direct `/<command>` "
        "sub-command when the skill declares `commands`) and in the Installed Skills list."
    )
    lines.append(install_note)
    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{_escape_xml(skill.name)}</name>")
        lines.append(f"    <description>{_escape_xml(skill.description)}</description>")
        lines.append(f"    <location>{_escape_xml(str(skill.file_path))}</location>")
        if skill.version:
            lines.append(f"    <version>{_escape_xml(skill.version)}</version>")
        if skill.commands:
            lines.append("    <commands>")
            for cmd in skill.commands:
                lines.append(
                    f'      <command name="{_escape_xml(cmd.name)}">'
                    f"{_escape_xml(cmd.description)}</command>"
                )
            lines.append("    </commands>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


# Token budget for the resident skills CATALOG (P4/V5). Bodies load on demand
# via load_skill; a huge catalog (many skills × long descriptions) is a fixed
# per-request cost on every model call. Mainstream coding agents keep the
# catalog small (opencode Skill.fmt injects name/description/location only).
SKILLS_CATALOG_MAX_TOKENS = 1500
# When the catalog is over budget, each description is clipped to this width
# before whole skills are dropped (dropping lowest-order ones last).
SKILL_DESCRIPTION_CLIP_CHARS = 160


def _clip_description(desc: str, limit: int) -> str:
    return " ".join(desc.split())[:limit]


def format_skills_prompt_bounded(skills: list["SkillEntry"]) -> str:
    """``format_skills_prompt`` + a hard token budget (mainstream, P4).

    Renders the catalog normally; if it exceeds ``SKILLS_CATALOG_MAX_TOKENS``
    it is re-rendered with clipped descriptions, then with whole skills dropped
    (from the end) until it fits. Never returns over-budget.
    """
    from coworker.context import estimate_text_tokens

    def _render(list_: list) -> str:
        return format_skills_prompt(list_)

    rendered = _render(skills)
    if estimate_text_tokens(rendered) <= SKILLS_CATALOG_MAX_TOKENS:
        return rendered

    clipped: list = []
    for s in skills:
        # SkillEntry is a frozen dataclass — copy.copy + assignment raises
        # "cannot assign to field 'description'". Use dataclasses.replace.
        c = replace(s, description=_clip_description(s.description or "", SKILL_DESCRIPTION_CLIP_CHARS))
        clipped.append(c)
    rendered = _render(clipped)
    if estimate_text_tokens(rendered) <= SKILLS_CATALOG_MAX_TOKENS:
        return rendered

    kept: list = []
    for s in clipped:
        kept.append(s)
        if estimate_text_tokens(_render(kept)) > SKILLS_CATALOG_MAX_TOKENS:
            kept.pop()
            break
    if kept:
        out = _render(kept)
        out += (
            "\n\n[skill catalog truncated to fit context — more skills exist; "
            "load any skill by name via the load_skill tool]"
        )
        return out
    return ""


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
