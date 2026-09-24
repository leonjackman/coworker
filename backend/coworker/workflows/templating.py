"""Templating and variable resolution (W04).

Supports ``{{inputs.name}}``, ``{{steps.<id>.<field>}}``, ``{{env.NAME}}`` and
``{{secret:name}}`` references inside any string parameter. A reference that
resolves to a single value may be used bare; otherwise the string is
interpolated.

Secrets are resolved through a caller-provided resolver so the workflow files
never contain plaintext credentials.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

_REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_BARE_RE = re.compile(r"^\{\{\s*([^{}]+?)\s*\}\}$")

SecretResolver = Callable[[str], str | None]


class TemplateError(Exception):
    """A template reference could not be resolved."""


def _lookup(path: str, context: dict[str, Any], secrets: SecretResolver | None) -> Any:
    path = path.strip()
    if path.startswith("secret:"):
        name = path[len("secret:") :].strip()
        if secrets is None:
            raise TemplateError(f"secrets are not available for '{{{{secret:{name}}}}}'")
        value = secrets(name)
        if value is None:
            raise TemplateError(f"secret not found: {name}")
        return value
    if path.startswith("env."):
        name = path[len("env.") :]
        if name not in os.environ:
            raise TemplateError(f"environment variable not set: {name}")
        return os.environ[name]

    parts = [p for p in path.split(".") if p != ""]
    if not parts:
        raise TemplateError("empty template reference")
    # Allow both `inputs.x` and bare `x` for inputs.
    if parts[0] in context and isinstance(context[parts[0]], dict):
        node: Any = context
    elif parts[0] == "steps" or parts[0] in context:
        node = context
    else:
        node = context.get("inputs", context)
    for part in parts:
        if isinstance(node, dict):
            if part not in node:
                raise TemplateError(f"undefined reference: {path}")
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as exc:
                raise TemplateError(f"bad list index in reference: {path}") from exc
        else:
            raise TemplateError(f"cannot descend into {type(node).__name__} at '{part}' in {path}")
    return node


def resolve_string(value: str, context: dict[str, Any], secrets: SecretResolver | None = None) -> Any:
    """Resolve a templated string. A pure single reference returns its raw value
    (preserving type); mixed text is interpolated to a string."""
    bare = _BARE_RE.match(value)
    if bare:
        return _lookup(bare.group(1), context, secrets)
    if "{{" not in value:
        return value

    def _sub(match: re.Match[str]) -> str:
        resolved = _lookup(match.group(1), context, secrets)
        if isinstance(resolved, (dict, list)):
            return json.dumps(resolved, ensure_ascii=False)
        return str(resolved)

    return _REF_RE.sub(_sub, value)


def resolve(value: Any, context: dict[str, Any], secrets: SecretResolver | None = None) -> Any:
    """Recursively resolve templates in any JSON-like structure."""
    if isinstance(value, str):
        return resolve_string(value, context, secrets)
    if isinstance(value, list):
        return [resolve(v, context, secrets) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, context, secrets) for k, v in value.items()}
    return value


def resolve_bool(value: Any, context: dict[str, Any], secrets: SecretResolver | None = None) -> bool:
    """Resolve a value and coerce it to bool (for ``when`` conditions)."""
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    resolved = resolve_string(str(value), context, secrets)
    if isinstance(resolved, bool):
        return resolved
    if isinstance(resolved, (int, float)):
        return resolved != 0
    if isinstance(resolved, (list, dict)):
        return len(resolved) > 0
    return str(resolved).strip().lower() not in {"", "0", "false", "no", "off", "none", "null"}
