"""Step assertions (W14).

Each step may declare ``pre`` and ``post`` assertion specs. An assertion is a
short, declarative string evaluated against the step result and the run context.
A failed postcondition means the step did NOT succeed — the executor then runs
recovery instead of trusting the action's own "ok".

Supported forms:

* ``ok`` / ``not_error`` — the result carries no error
* ``contains <text>`` / ``not_contains <text>`` — substring in the serialized result
* ``equals <ref> <value>`` / ``not_equals <ref> <value>`` — context/result lookup
* ``matches <ref> <regex>``
* ``<ref>`` — truthiness of a context/result lookup
"""

from __future__ import annotations

import json
import re
from typing import Any

from .templating import TemplateError, resolve_string

AssertionResult = tuple[bool, str]


def _serialize(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


def _has_error(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("error") or result.get("error_code"))
    return False


def _lookup_ref(ref: str, result: Any, context: dict[str, Any]) -> Any:
    ref = ref.strip()
    if ref in ("", "result", "$"):
        return result
    if ref.startswith("result."):
        node: Any = result
        parts = ref[len("result.") :].split(".")
    elif ref.startswith("context."):
        node = context
        parts = ref[len("context.") :].split(".")
    else:
        # Resolve as a template reference (inputs.x / steps.id.field).
        return resolve_string("{{" + ref + "}}", context)
    for part in parts:
        if isinstance(node, dict):
            if part not in node:
                raise KeyError(ref)
            node = node[part]
        elif isinstance(node, list):
            node = node[int(part)]
        else:
            raise KeyError(ref)
    return node


def evaluate(spec: str, result: Any, context: dict[str, Any]) -> AssertionResult:
    """Evaluate one assertion spec; return ``(passed, message)``."""
    token = (spec or "").strip()
    if not token:
        return True, ""

    low = token.lower()
    if low in ("ok", "not_error", "no_error"):
        if _has_error(result):
            return False, f"assertion '{spec}' failed: result reports an error"
        return True, ""

    for prefix, invert in (("not_contains ", True), ("contains ", False)):
        if low.startswith(prefix):
            needle = token[len(prefix) :].strip()
            if "{{" in needle:
                try:
                    needle = str(resolve_string(needle, context))
                except TemplateError as exc:
                    return False, f"assertion '{spec}' failed: {exc}"
            haystack = _serialize(result)
            found = needle in haystack
            if found == invert:
                return False, f"assertion '{spec}' failed: {'found' if invert else 'missing'} '{needle}'"
            return True, ""

    for prefix, invert in (("not_equals ", True), ("equals ", False)):
        if low.startswith(prefix):
            rest = token[len(prefix) :].strip()
            parts = rest.split(None, 1)
            if len(parts) != 2:
                return False, f"assertion '{spec}' failed: expected 'equals <ref> <value>'"
            ref, expected_raw = parts
            try:
                actual = _lookup_ref(ref, result, context)
            except (KeyError, IndexError, TemplateError):
                return False, f"assertion '{spec}' failed: undefined ref '{ref}'"
            expected = resolve_string(expected_raw, context) if "{{" in expected_raw else expected_raw
            equal = str(actual) == str(expected)
            if equal == invert:
                return False, f"assertion '{spec}' failed: {ref}={actual!r} (expected {expected!r})"
            return True, ""

    if low.startswith("exit_code "):
        expected = token[len("exit_code ") :].strip()
        actual = None
        if isinstance(result, dict):
            actual = result.get("return_code", result.get("exit_code"))
        if str(actual) != str(expected):
            return False, f"assertion '{spec}' failed: exit code {actual!r} != {expected!r}"
        return True, ""

    if low.startswith("file_exists ") or low.startswith("not_file_exists "):
        invert = low.startswith("not_file_exists ")
        prefix = "not_file_exists " if invert else "file_exists "
        raw_path = token[len(prefix) :].strip()
        try:
            path = str(resolve_string(raw_path, context)) if "{{" in raw_path else raw_path
        except TemplateError as exc:
            return False, f"assertion '{spec}' failed: {exc}"
        import os
        from pathlib import Path

        exists = Path(os.path.expanduser(path)).exists()
        if exists == invert:
            return False, f"assertion '{spec}' failed: file {'exists' if invert else 'missing'}: {path}"
        return True, ""

    if low.startswith("file_contains "):
        rest = token[len("file_contains ") :].strip()
        parts = rest.split(None, 1)
        if len(parts) != 2:
            return False, f"assertion '{spec}' failed: expected 'file_contains <path> <text>'"
        raw_path, raw_text = parts
        try:
            path = str(resolve_string(raw_path, context)) if "{{" in raw_path else raw_path
            needle = str(resolve_string(raw_text, context)) if "{{" in raw_text else raw_text
        except TemplateError as exc:
            return False, f"assertion '{spec}' failed: {exc}"
        import os
        from pathlib import Path

        try:
            content = Path(os.path.expanduser(path)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False, f"assertion '{spec}' failed: cannot read {path}"
        if needle not in content:
            return False, f"assertion '{spec}' failed: '{needle}' not in {path}"
        return True, ""

    if low.startswith("exists ") or low.startswith("not_exists "):
        invert = low.startswith("not_exists ")
        ref = token[len("not_exists ") if invert else len("exists ") :].strip()
        try:
            value = _lookup_ref(ref, result, context)
        except (KeyError, IndexError, TemplateError):
            value = None
        present = value is not None and value != "" and value != [] and value != {}
        if present == invert:
            return False, f"assertion '{spec}' failed: ref {ref} is {'set' if invert else 'unset'}"
        return True, ""

    if low.startswith("matches ") or low.startswith("regex "):
        prefix = "matches " if low.startswith("matches ") else "regex "
        rest = token[len(prefix) :].strip()
        parts = rest.split(None, 1)
        if len(parts) != 2:
            return False, f"assertion '{spec}' failed: expected 'matches <ref> <regex>'"
        ref, pattern = parts
        try:
            actual = _lookup_ref(ref, result, context)
        except (KeyError, IndexError, TemplateError):
            return False, f"assertion '{spec}' failed: undefined ref '{ref}'"
        pattern = resolve_string(pattern, context) if "{{" in pattern else pattern
        if re.search(pattern, str(actual)) is None:
            return False, f"assertion '{spec}' failed: {ref}={actual!r} !~ /{pattern}/"
        return True, ""

    # Bare reference: truthiness.
    try:
        value = _lookup_ref(token, result, context)
    except (KeyError, IndexError, TemplateError):
        return False, f"assertion '{spec}' failed: undefined ref '{token}'"
    if isinstance(value, str):
        truthy = value.strip().lower() not in {"", "0", "false", "no", "off", "none", "null"}
    else:
        truthy = bool(value)
    if not truthy:
        return False, f"assertion '{spec}' failed: value is falsy"
    return True, ""


def evaluate_all(specs: list[str], result: Any, context: dict[str, Any]) -> AssertionResult:
    """Evaluate a list of assertions; return the first failure."""
    for spec in specs or []:
        passed, message = evaluate(spec, result, context)
        if not passed:
            return False, message
    return True, ""
