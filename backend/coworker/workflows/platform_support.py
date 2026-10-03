"""Workflow platform support — one place that answers "which OS can run this?".

Most workflow steps are platform-neutral by construction: the same intent maps
to a per-OS implementation chosen at run time (key chords, ``sig`` ref prefixes,
desktop notifications, the native-script tool). Those need NO ``platform``
declaration — ``env.py`` / ``native.py`` make them match automatically.

A small minority is genuinely platform-only (literal AppleScript, ``open -a``,
``~/Desktop``, a PowerShell COM script). Those workflows declare ``platform``;
on a non-matching OS the run is refused (and scheduled runs are skipped).

This module is the single source of the tag vocabulary, the `platform` field
normalization, and the platform-only token/action detection used by both
``validation.py`` (advisory) and the live Studio capability catalog.
"""

from __future__ import annotations

from typing import Any, Iterable

from coworker.platform import platform_tag

#: Canonical OS tags, in a stable display order.
ALL_TAGS: tuple[str, ...] = ("darwin", "win32", "linux")

_TAG_NAMES = {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}

#: Accepted aliases → canonical tag.
_ALIASES: dict[str, str] = {
    "darwin": "darwin", "mac": "darwin", "macos": "darwin", "mac os": "darwin",
    "osx": "darwin", "macosx": "darwin",
    "win32": "win32", "win": "win32", "windows": "win32", "win64": "win32",
    "linux": "linux", "gnu/linux": "linux",
}

_ANY_TOKENS = {"any", "*", "all", ""}


def current_tag() -> str:
    """The OS the backend is running on (``darwin`` | ``win32`` | ``linux``)."""
    return platform_tag()


def normalize_tag(token: str) -> str | None:
    return _ALIASES.get(str(token or "").strip().lower())


def parse_platforms(value: Any) -> set[str]:
    """Normalize a workflow ``platform`` value into a set of canonical tags.

    Accepts ``None``/``""``/``"any"``/``"*"`` (→ all), a comma/space separated
    string, or a list of tags/aliases.
    """
    if value is None:
        return set(ALL_TAGS)
    items: list[str] = []
    if isinstance(value, str):
        items = [p for p in value.replace(",", " ").split() if p]
    elif isinstance(value, (list, tuple, set)):
        for entry in value:
            if entry is None:
                continue
            items.extend(str(entry).replace(",", " ").split())
    else:
        items = [str(value)]
    items = [i for i in (s.strip() for s in items) if i]
    if not items:
        return set(ALL_TAGS)
    out: set[str] = set()
    for item in items:
        low = item.lower()
        if low in _ANY_TOKENS:
            return set(ALL_TAGS)
        tag = normalize_tag(low)
        if tag:
            out.add(tag)
    return out or set(ALL_TAGS)


def explicit_tags(value: Any) -> set[str]:
    """Tags the author *explicitly* declared. Empty for ``any``/``*``/unset.

    Distinct from :func:`parse_platforms` (which expands ``any``/unset to all):
    validation needs to know whether a platform-specific step was actually
    declared, so "undeclared"/"any" must not silently satisfy it.
    """
    if value is None:
        return set()
    raw = value if isinstance(value, str) else " ".join(str(v) for v in value)
    raw = raw.replace(",", " ").strip().lower()
    if raw in _ANY_TOKENS:
        return set()
    return {t for t in (normalize_tag(x) for x in raw.split()) if t}


def canonical(value: Any) -> str:
    """Render a normalized ``platform`` string (``"any"`` when unconstrained)."""
    tags = parse_platforms(value)
    if tags >= set(ALL_TAGS):
        return "any"
    return ",".join(t for t in ALL_TAGS if t in tags)


def platform_label(tags: Iterable[str]) -> str:
    s = set(tags)
    if s >= set(ALL_TAGS):
        return "Any"
    return " / ".join(_TAG_NAMES.get(t, t) for t in ALL_TAGS if t in s)


def modifier_for(platform: str | None = None) -> str:
    """Primary modifier token for a generated key chord (``cmd``/``ctrl``)."""
    return "cmd" if platform_tag(platform) == "darwin" else "ctrl"


# ── Platform-only detection (single source for validation + capabilities) ────

#: Command substrings that only make sense on macOS.
MACOS_ONLY_TOKENS: tuple[str, ...] = (
    "unzip ", "open -a ", "osascript", "/applications/", "$home/desktop",
    "~/desktop", "pbcopy", "pbpaste", "sips ", "display notification",
)

#: Command substrings that only make sense on Windows.
WINDOWS_ONLY_TOKENS: tuple[str, ...] = (
    "powershell", "pwsh", "get-childitem", "get-content", "get-process",
    "set-location", "convertto-json", "select-string", "test-path",
    ".ps1", "reg add", "reg query", "robocopy ", "icacls ", "tasklist",
    "findstr ", "wmic ", "driverquery", "%userprofile%", "%appdata%",
)

#: Tools that exist on exactly one platform. Used to infer/validate `platform`
#: and to tag the capability catalog.
PLATFORM_ONLY_TOOLS: dict[str, set[str]] = {
    "run_applescript": {"darwin"},
    "run_powershell": {"win32"},
}


def _step_kind_action(step: Any) -> tuple[str, str]:
    return (
        str(getattr(step, "kind", "") or ""),
        str(getattr(step, "do", "") or ""),
    )


def _command_text(step: Any) -> str:
    params = getattr(step, "params", None) or {}
    raw = params.get("command") or params.get("run") or getattr(step, "do", "")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return " ".join(str(x) for x in raw)
    return str(raw or "")


def _walk_steps(steps: Any) -> Iterable[Any]:
    for step in steps or []:
        yield step
        for slot in ("then", "else_", "body"):
            sub = getattr(step, slot, None)
            if sub:
                yield from _walk_steps(sub)


def step_platform_tags(step: Any) -> set[str]:
    """The set of platforms a single step *requires* (empty = any)."""
    kind, do = _step_kind_action(step)
    tags: set[str] = set()

    if kind == "tool" and do in PLATFORM_ONLY_TOOLS:
        tags |= PLATFORM_ONLY_TOOLS[do]

    if kind == "command":
        low = _command_text(step).lower()
        if any(tok in low for tok in MACOS_ONLY_TOKENS):
            tags.add("darwin")
        if any(tok in low for tok in WINDOWS_ONLY_TOKENS):
            tags.add("win32")

    return tags


def infer_platforms(workflow: Any) -> set[str]:
    """Platform tags implied by the workflow's steps (empty = platform-neutral).

    When a workflow mixes macOS-only and Windows-only tokens the result is not a
    single tag; callers treat `len(tags) == 1` as confidently inferable.
    """
    tags: set[str] = set()
    for step in _walk_steps(getattr(workflow, "steps", None)):
        tags |= step_platform_tags(step)
    return tags


def workflow_supports(workflow: Any) -> tuple[bool, set[str], str]:
    """Return ``(ok, declared_tags, reason)`` for running on the CURRENT OS."""
    declared = parse_platforms(getattr(workflow, "platform", "") or "")
    if current_tag() in declared:
        return True, declared, ""
    reason = (
        f"this workflow targets {platform_label(declared)}; "
        f"the current OS is {platform_label([current_tag()])}"
    )
    return False, declared, reason
