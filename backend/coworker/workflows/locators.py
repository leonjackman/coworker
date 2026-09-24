"""Semantic locators with a fallback ladder (W11/W12).

A GUI step never stores a raw pixel coordinate as its primary target. It stores
a semantic descriptor (role + accessible name / identifier) with ordered
fallbacks. At run time the first descriptor that resolves wins. When a step was
recorded with a different primary descriptor, a successful fallback is reported
as *drift* so the healer can rewrite the canonical locator (W15).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ResolveFn = Callable[[dict[str, Any]], Any]

# Descriptors considered unstable (heuristic/coordinate based) — using one is
# allowed but flagged so the UI can warn and the healer can prefer a semantic
# replacement.
UNSTABLE_KEYS = frozenset({"coords", "x", "y", "pixel"})


@dataclass
class LocatorResolution:
    target: Any
    descriptor: dict[str, Any]
    index: int  # 0 = primary; >0 = a fallback (drift)

    @property
    def drifted(self) -> bool:
        return self.index > 0

    @property
    def unstable(self) -> bool:
        return any(k in self.descriptor for k in UNSTABLE_KEYS)


class LocatorUnresolved(Exception):
    """No descriptor in the ladder resolved."""


def resolve(locator: dict[str, Any] | None, resolve_fn: ResolveFn) -> LocatorResolution:
    """Walk the locator's candidate descriptors and return the first match.

    ``locator`` is the raw dict form: ``{"role": ..., "name": ..., "fallback": [...]}``.
    Raises :class:`LocatorUnresolved` when every descriptor fails.
    """
    from .model import Locator

    parsed = Locator.from_dict(locator)
    if parsed is None or not parsed.candidates():
        raise LocatorUnresolved("step has no locator")
    last_error: Exception | None = None
    for index, descriptor in enumerate(parsed.candidates()):
        try:
            target = resolve_fn(descriptor)
        except Exception as exc:  # noqa: BLE001 - a bad descriptor falls through
            last_error = exc
            continue
        if target is not None:
            return LocatorResolution(target=target, descriptor=descriptor, index=index)
    raise LocatorUnresolved(f"no descriptor resolved (tried {len(parsed.candidates())})") from last_error


def promote(locator: dict[str, Any] | None, descriptor: dict[str, Any]) -> dict[str, Any]:
    """Return a locator dict whose primary descriptor is ``descriptor`` and
    whose ladder keeps the previous primary as a fallback (W15 heal rewrite)."""
    from .model import Locator

    parsed = Locator.from_dict(locator) or Locator()
    rest = [c for c in parsed.candidates() if c != descriptor]
    return Locator(primary=dict(descriptor), fallback=[dict(c) for c in rest]).to_dict()


def summarize(locator: dict[str, Any] | None) -> str:
    """Human-readable one-line summary for UI/logs."""
    from .model import Locator

    parsed = Locator.from_dict(locator)
    if parsed is None:
        return "(no locator)"
    parts: list[str] = []
    for descriptor in parsed.candidates():
        if "role" in descriptor and "name" in descriptor:
            parts.append(f"{descriptor['role']}[{descriptor['name']}]")
        elif "identifier" in descriptor:
            parts.append(f"#{descriptor['identifier']}")
        elif "text" in descriptor:
            parts.append(f"text={descriptor['text']}")
        elif "coords" in descriptor:
            parts.append(f"coords={descriptor['coords']}")
        else:
            parts.append(str(descriptor))
    return " -> ".join(parts)
