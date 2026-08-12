"""OAuth 2.1 + PKCE support for remote MCP servers (RFC 8252 / RFC 8414).

The MCP SDK ships a complete ``OAuthClientProvider`` (an ``httpx.Auth``) that
handles metadata discovery, dynamic client registration, PKCE and token
refresh. This module provides the two pieces the SDK asks the application for:

* :class:`FileTokenStorage` -- persists access tokens and the registered
  client identity to disk so reconnects do not re-run the browser flow.
* :class:`LoopbackCallbackServer` -- the RFC 8252 native-app loopback redirect
  target. The authorization page redirects here with ``?code=`` and the
  provider exchanges it for a token.

The provider is wired into the MCP session via the ``auth`` key of the
``langchain-mcp-adapters`` connection dict, which is forwarded to the
underlying httpx client for SSE and Streamable HTTP transports.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import AnyUrl

from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

logger = logging.getLogger(__name__)

#: How long the authorization flow waits for the user to finish in the browser.
OAUTH_AUTH_TIMEOUT_SECONDS = 300.0


class FileTokenStorage:
    """Persist OAuth tokens + client identity for one server as JSON.

    The MCP SDK's ``OAuthClientProvider`` consumes this through the
    ``TokenStorage`` protocol (``get_tokens`` / ``set_tokens`` /
    ``get_client_info`` / ``set_client_info``).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - corrupt token file resets to empty
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # access/refresh tokens must not be world-readable
        except OSError:
            pass
        tmp.replace(self.path)

    # ── TokenStorage protocol ───────────────────────────────────────────

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthToken.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)


class LoopbackCallbackServer:
    """RFC 8252 native-app loopback redirect target.

    Listens on ``127.0.0.1:0`` and answers ``GET /oauth/callback?code=...``.
    The received code/state is surfaced to the waiting OAuth flow via an
    ``asyncio.Future``. Runs on the same event loop as the session manager, so
    ``start`` / ``wait_for_code`` must be called on that loop.
    """

    def __init__(self) -> None:
        self._server: asyncio.base_events.Server | None = None
        self._future: asyncio.Future[tuple[str, str | None]] | None = None
        self.redirect_uri = ""

    async def start(self) -> str:
        """Bind the loopback listener and return the redirect URI."""
        self._future = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        socket = self._server.sockets[0]
        port = socket.getsockname()[1]
        self.redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"
        logger.info("OAuth loopback listener ready on %s", self.redirect_uri)
        return self.redirect_uri

    async def wait_for_code(self, timeout: float = OAUTH_AUTH_TIMEOUT_SECONDS) -> tuple[str, str | None]:
        """Wait for the browser redirect and return ``(code, state)``."""
        if self._future is None:
            raise RuntimeError("LoopbackCallbackServer.start() must be called first")
        return await asyncio.wait_for(self._future, timeout=timeout)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._server = None
        if self._future is not None:
            if self._future.done():
                # Drain a not-yet-retrieved exception to avoid the "Future
                # exception was never retrieved" warning when the flow never
                # reached the browser callback.
                try:
                    self._future.exception()
                except Exception:  # noqa: BLE001
                    pass
            elif not self._future.cancelled():
                self._future.cancel()
        self._future = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_line = ""
        try:
            request_line = (await asyncio.wait_for(reader.readline(), timeout=10)).decode(
                "utf-8", errors="replace"
            ).strip()
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=10)
                if header_line in (b"\r\n", b"\n", b""):
                    break
            target = request_line.split(" ")[1] if " " in request_line else "/"
            query = urlsplit(target).query
            params = parse_qs(query)
            code = params.get("code", [""])[0]
            state = params.get("state", [None])[0]
            if code and self._future is not None and not self._future.done():
                self._future.set_result((code, state))
            body = (
                "<!doctype html><html><head><meta charset='utf-8'></head><body>"
                "<h2>授权成功</h2><p>你可以关闭此页面并返回 CoWorker。</p></body></html>"
            )
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body.encode('utf-8'))}\r\n"
                "Connection: close\r\n\r\n"
                + body
            )
            writer.write(response.encode("utf-8"))
        except Exception:  # noqa: BLE001 - a malformed probe must not crash the server
            logger.debug("OAuth loopback handler failed: %r", request_line)
        finally:
            try:
                await writer.drain()
            except Exception:  # noqa: BLE001
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


def build_oauth_provider(
    server_url: str,
    storage: FileTokenStorage,
    loopback: LoopbackCallbackServer,
    open_browser: Any | None = None,
    timeout: float = OAUTH_AUTH_TIMEOUT_SECONDS,
    enable_browser_flow: bool = True,
) -> OAuthClientProvider:
    """Build an ``OAuthClientProvider`` for one remote MCP server.

    Args:
        server_url: Base URL of the MCP server.
        storage: Token/client persistence for this server.
        loopback: Started loopback listener (its redirect URI is advertised to
            the authorization server). Required when ``enable_browser_flow``.
        open_browser: Callable that receives the authorization URL; defaults to
            opening the OS default browser via ``webbrowser.open``.
        timeout: How long to wait for the browser callback.
        enable_browser_flow: When ``False`` the provider can attach a persisted
            token or refresh it silently, but will never open the browser. This
            lets background connect/prewarm reuse stored tokens without
            surprising the user; a server that needs a fresh login surfaces as
            ``needs_auth`` instead.
    """
    if enable_browser_flow:
        if not loopback.redirect_uri:
            raise RuntimeError("loopback.start() must be called before build_oauth_provider()")
        redirect_uri = loopback.redirect_uri

        opener = open_browser or webbrowser.open

        async def _redirect_handler(auth_url: str) -> None:
            try:
                logger.info("Opening authorization URL in browser")
                await asyncio.to_thread(opener, auth_url)
            except Exception:  # noqa: BLE001 - browser launch must not break the flow
                logger.warning("Failed to open authorization URL: %s", auth_url)

        async def _callback_handler() -> tuple[str, str | None]:
            return await loopback.wait_for_code(timeout=timeout)

    else:

        async def _redirect_handler(auth_url: str) -> None:  # pragma: no cover - never invoked
            raise RuntimeError("OAuth browser flow is disabled in this context")

        async def _callback_handler() -> tuple[str, str | None]:  # pragma: no cover - never invoked
            raise RuntimeError("OAuth browser flow is disabled in this context")

        redirect_uri = "http://127.0.0.1:0/oauth/callback"

    client_metadata = OAuthClientMetadata(
        redirect_uris=[AnyUrl(redirect_uri)],
        token_endpoint_auth_method="none",
        client_name="Coworker",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
        timeout=timeout,
    )
