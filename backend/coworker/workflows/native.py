"""Native workflow actions implemented server-side (no external tool needed).

These back the ``http`` / ``file`` / ``transform`` / ``notify`` step kinds. They
are deliberately dependency-light (httpx + stdlib) so a workflow can do real work
(headers/auth API calls, file moves, data transforms, desktop notifications)
without shelling out or writing a script.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def run_native(
    kind: str,
    action: str,
    payload: dict[str, Any],
    *,
    notifier: Any = None,
) -> Any:
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise RuntimeError(f"unsupported native step kind: {kind}")
    # `notify` gets an optional Electron-backed notifier (preferred; consistent
    # with the tray / DND). All other kinds are pure.
    if kind == "notify":
        return handler(action, payload or {}, notifier)
    return handler(action, payload or {})


# ── http ─────────────────────────────────────────────────────────────────


def _http(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action != "request":
        raise RuntimeError(f"http has no action '{action}'")
    import httpx

    url = str(payload.get("url") or "").strip()
    if not url:
        raise RuntimeError("http request requires 'url'")
    method = str(payload.get("method") or "GET").upper()
    headers = {str(k): str(v) for k, v in (payload.get("headers") or {}).items()}

    auth = str(payload.get("auth") or "").lower()
    if auth == "bearer" and payload.get("token"):
        headers.setdefault("Authorization", f"Bearer {payload['token']}")
    basic = None
    if auth == "basic" and (payload.get("username") or payload.get("password")):
        basic = (str(payload.get("username") or ""), str(payload.get("password") or ""))

    kwargs: dict[str, Any] = {
        "headers": headers or None,
        "params": payload.get("query") or None,
        "timeout": float(payload.get("timeout") or 30),
        "follow_redirects": True,
    }
    if basic:
        kwargs["auth"] = basic
    if payload.get("json") is not None:
        kwargs["json"] = payload["json"]
    elif payload.get("body") is not None:
        kwargs["content"] = str(payload["body"])

    with httpx.Client() as client:
        resp = client.request(method, url, **kwargs)
    try:
        data: Any = resp.json()
    except Exception:  # noqa: BLE001 - non-JSON body is fine
        data = None
    return {
        "status": resp.status_code,
        "ok": resp.is_success,
        "headers": dict(resp.headers),
        "text": resp.text[:20000],
        "json": data,
    }


# ── file ─────────────────────────────────────────────────────────────────


def _expand(path: Any) -> Path:
    return Path(os.path.expanduser(str(path or "")))


def _file(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    import shutil

    path = _expand(payload.get("path"))
    encoding = str(payload.get("encoding") or "utf-8")

    if action == "read":
        return {"path": str(path), "content": path.read_text(encoding=encoding, errors="replace")[:200000]}
    if action == "write":
        if payload.get("mkdirs"):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(payload.get("content") or ""), encoding=encoding)
        return {"path": str(path), "written": True}
    if action == "append":
        if payload.get("mkdirs"):
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding=encoding) as handle:
            handle.write(str(payload.get("content") or ""))
        return {"path": str(path), "appended": True}
    if action in ("copy", "move"):
        target = _expand(payload.get("to"))
        if payload.get("mkdirs"):
            target.parent.mkdir(parents=True, exist_ok=True)
        if action == "copy":
            if path.is_dir():
                shutil.copytree(str(path), str(target), dirs_exist_ok=True)
            else:
                shutil.copy2(str(path), str(target))
        else:
            shutil.move(str(path), str(target))
        return {"path": str(path), "to": str(target)}
    if action == "delete":
        if path.is_dir():
            shutil.rmtree(str(path))
        elif path.exists():
            path.unlink()
        return {"path": str(path), "deleted": True}
    if action == "mkdir":
        path.mkdir(parents=True, exist_ok=True)
        return {"path": str(path), "created": True}
    if action in ("list", "glob", "newest"):
        pattern = str(payload.get("pattern") or "*")
        # Non-recursive by default (like a shell glob). Recursion only when the
        # pattern asks for it ("**") or `recursive: true` is set — this stops a
        # broad `*.zip` from reaching into unrelated subfolders.
        recursive = bool(payload.get("recursive")) or "**" in pattern
        if action == "newest":
            candidates = path.rglob(pattern) if recursive else path.glob(pattern)
            files = [p for p in candidates if p.is_file()]
            if not files:
                return {"path": str(path), "found": False}
            latest = max(files, key=lambda p: p.stat().st_mtime)
            return {
                "path": str(latest),
                "found": True,
                "mtime": latest.stat().st_mtime,
                "count": len(files),
            }
        iterator = path.rglob(pattern) if recursive else path.glob(pattern)
        items = sorted(str(p) for p in iterator)
        return {"path": str(path), "items": items[:5000], "count": len(items)}
    if action == "exists":
        return {"path": str(path), "exists": path.exists()}
    if action == "stat":
        exists = path.exists()
        return {
            "path": str(path),
            "exists": exists,
            "is_dir": path.is_dir() if exists else False,
            "size": path.stat().st_size if exists and path.is_file() else 0,
        }
    if action == "unzip":
        # Cross-platform archive extraction via stdlib — no `unzip`/shell, so a
        # workflow stays atomic and does not need `platform: darwin`.
        import zipfile

        target = payload.get("to")
        if not target:
            raise RuntimeError("file unzip requires 'to' (destination directory)")
        dest = _expand(target)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(path)) as archive:
            names = archive.namelist()
            archive.extractall(str(dest))
        items = sorted(str(p) for p in dest.rglob("*"))
        return {"path": str(path), "to": str(dest), "names": names[:5000], "items": items[:5000], "count": len(names)}
    if action == "zip":
        import zipfile

        target = payload.get("to")
        if not target:
            raise RuntimeError("file zip requires 'to' (output .zip path)")
        archive_path = _expand(target)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        added = 0
        with zipfile.ZipFile(str(archive_path), "w", zipfile.ZIP_DEFLATED) as archive:
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        archive.write(str(child), arcname=str(child.relative_to(path)))
                        added += 1
            elif path.exists():
                archive.write(str(path), arcname=path.name)
                added += 1
            else:
                raise RuntimeError(f"file zip source not found: {path}")
        return {"path": str(path), "to": str(archive_path), "count": added}
    raise RuntimeError(f"file has no action '{action}'")


# ── transform ────────────────────────────────────────────────────────────


def _json_path(data: Any, path: str) -> Any:
    current = data
    for part in [p for p in str(path or "").split(".") if p != ""]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _transform(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    import re as _re

    if action == "json_parse":
        return {"result": json.loads(str(payload.get("text") or ""))}
    if action == "json_path":
        data = payload.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                pass
        return {"result": _json_path(data, str(payload.get("path") or ""))}
    if action == "regex":
        text = str(payload.get("text") or "")
        pattern = str(payload.get("pattern") or "")
        if str(payload.get("mode") or "extract") == "replace":
            return {"result": _re.sub(pattern, str(payload.get("replace") or ""), text)}
        match = _re.search(pattern, text)
        if not match:
            return {"result": None, "matched": False}
        group = payload.get("group")
        return {
            "result": match.group(int(group)) if group is not None else match.group(0),
            "groups": list(match.groups()),
            "matched": True,
        }
    if action == "template":
        text = str(payload.get("text") or "")
        for key, value in (payload.get("vars") or {}).items():
            text = text.replace("{{" + str(key) + "}}", str(value)).replace("{{ " + str(key) + " }}", str(value))
        return {"result": text}
    if action == "csv_parse":
        delimiter = str(payload.get("delimiter") or ",")
        rows = list(csv.reader(io.StringIO(str(payload.get("text") or "")), delimiter=delimiter))
        return {"rows": rows, "result": rows, "count": len(rows)}
    if action == "base64":
        text = str(payload.get("text") or "")
        if str(payload.get("op") or "encode") == "encode":
            return {"result": base64.b64encode(text.encode()).decode()}
        return {"result": base64.b64decode(text.encode()).decode(errors="replace")}
    if action == "date_format":
        value = str(payload.get("value") or "")
        from_fmt = str(payload.get("from_format") or "%Y-%m-%d")
        to_fmt = str(payload.get("to_format") or "%Y%m%d")
        moment = datetime.strptime(value, from_fmt) if value else datetime.now()
        return {"result": moment.strftime(to_fmt)}
    raise RuntimeError(f"transform has no action '{action}'")


# ── notify ───────────────────────────────────────────────────────────────


def _windows_toast(title: str, body: str) -> str:
    """Best-effort Windows toast via PowerShell WinRT (no third-party deps).

    Works in a headless/scheduled run (no Electron). Values are passed through
    the environment so quoting/newlines can never break the PowerShell script.
    """
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null; "
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$x = $t.GetElementsByTagName('text'); "
        "$x.Item(0).AppendChild($t.CreateTextNode($env:CW_TOAST_TITLE)) > $null; "
        "$x.Item(1).AppendChild($t.CreateTextNode($env:CW_TOAST_BODY)) > $null; "
        "$n = [Windows.UI.Notifications.ToastNotification]::new($t); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('CoWorker').Show($n);"
    )
    env = dict(os.environ, CW_TOAST_TITLE=str(title), CW_TOAST_BODY=str(body))
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
        timeout=8, capture_output=True, text=True, shell=False, env=env,
    )
    return "powershell-toast" if completed.returncode == 0 else "none"


def _desktop_notification(title: str, body: str) -> str:
    """OS-native notification; returns the backend used, or "none"."""
    try:
        if sys.platform == "darwin":
            script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
            subprocess.run(["osascript", "-e", script], timeout=5, check=False)
            return "osascript"
        if sys.platform.startswith("linux"):
            subprocess.run(["notify-send", title, body], timeout=5, check=False)
            return "notify-send"
        if sys.platform.startswith("win"):
            return _windows_toast(title, body)
    except Exception:  # noqa: BLE001 - a notification must never fail the run
        pass
    return "none"


def _notify(action: str, payload: dict[str, Any], notifier: Any = None) -> dict[str, Any]:
    if action == "notification":
        title = str(payload.get("title") or "CoWorker")
        body = str(payload.get("body") or payload.get("message") or "")
        via = "none"
        # Preferred: the desktop app's Electron Notification (all OSes, respects
        # the tray / focus-assist). Falls back to the OS-native CLI.
        if notifier is not None:
            try:
                res = notifier(title, body)
                if isinstance(res, dict) and res.get("ok"):
                    via = "electron"
            except Exception:  # noqa: BLE001 - fall through to native
                via = "none"
        if via == "none":
            via = _desktop_notification(title, body)
        return {"notified": via != "none", "title": title, "body": body, "via": via}
    if action == "webhook":
        return _http(
            "request",
            {
                "method": payload.get("method") or "POST",
                "url": payload.get("url"),
                "headers": payload.get("headers"),
                "json": payload.get("json"),
                "body": payload.get("body"),
                "expect_status": payload.get("expect_status"),
                "timeout": payload.get("timeout"),
            },
        )
    raise RuntimeError(f"notify has no action '{action}'")


_HANDLERS = {"http": _http, "file": _file, "transform": _transform, "notify": _notify}
