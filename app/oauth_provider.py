import html
import secrets
import time
from typing import Any

from pydantic import AnyHttpUrl
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config import settings


OZON_READ_SCOPE = "ozon:read"


class OzonOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Single-owner OAuth provider for the private Ozon MCP endpoint."""

    def __init__(self, login_url: str, issuer_url: str) -> None:
        self.login_url = login_url
        self.issuer_url = issuer_url.rstrip("/")
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self.pending: dict[str, dict[str, Any]] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        state = params.state or secrets.token_urlsafe(24)
        self.pending[state] = {
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "client_id": client.client_id,
            "resource": params.resource,
            "scopes": params.scopes or [OZON_READ_SCOPE],
        }
        return construct_redirect_uri(self.login_url, state=state)

    async def get_login_page(self, state: str) -> HTMLResponse:
        if state not in self.pending:
            raise HTTPException(400, "Invalid or expired OAuth state")
        action = html.escape(f"{self.issuer_url}/login/callback", quote=True)
        safe_state = html.escape(state, quote=True)
        body = f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ozon Analytics</title></head>
<body style="font-family:Arial,sans-serif;max-width:420px;margin:48px auto;padding:0 20px">
<h1 style="font-size:24px">Ozon Analytics</h1>
<p>Войдите с логином и паролем панели магазина.</p>
<form action="{action}" method="post">
<input type="hidden" name="state" value="{safe_state}">
<label>Логин<br><input name="username" autocomplete="username" required
style="box-sizing:border-box;width:100%;padding:10px;margin:6px 0 16px"></label><br>
<label>Пароль<br><input type="password" name="password" autocomplete="current-password" required
style="box-sizing:border-box;width:100%;padding:10px;margin:6px 0 20px"></label><br>
<button type="submit" style="padding:10px 18px">Подключить ChatGPT</button>
</form></body></html>"""
        return HTMLResponse(
            body,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
                "X-Frame-Options": "DENY",
            },
        )

    async def handle_login_callback(self, request: Request) -> Response:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        state = form.get("state")
        if not all(isinstance(value, str) for value in (username, password, state)):
            raise HTTPException(400, "Missing login fields")
        redirect_url = await self.complete_login(username, password, state)
        return RedirectResponse(redirect_url, status_code=302)

    async def complete_login(self, username: str, password: str, state: str) -> str:
        pending = self.pending.get(state)
        if pending is None:
            raise HTTPException(400, "Invalid or expired OAuth state")
        if not settings.dashboard_username or not settings.dashboard_password:
            raise HTTPException(503, "Dashboard credentials are not configured")
        valid = secrets.compare_digest(username, settings.dashboard_username)
        valid = secrets.compare_digest(password, settings.dashboard_password) and valid
        if not valid:
            raise HTTPException(401, "Invalid credentials")

        code_value = f"ozon_code_{secrets.token_urlsafe(32)}"
        code = AuthorizationCode(
            code=code_value,
            client_id=pending["client_id"],
            redirect_uri=AnyHttpUrl(pending["redirect_uri"]),
            redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
            expires_at=time.time() + 300,
            scopes=pending["scopes"],
            code_challenge=pending["code_challenge"],
            resource=pending["resource"],
            subject=username,
        )
        self.auth_codes[code_value] = code
        del self.pending[state]
        return construct_redirect_uri(
            pending["redirect_uri"],
            code=code_value,
            state=state,
            iss=self.issuer_url,
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self.auth_codes.get(authorization_code)
        if code is None or code.client_id != client.client_id or code.expires_at < time.time():
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        stored = await self.load_authorization_code(client, authorization_code.code)
        if stored is None:
            raise TokenError(error="invalid_grant", error_description="Invalid authorization code")
        del self.auth_codes[authorization_code.code]
        return self._issue_tokens(stored.client_id, stored.scopes, stored.resource, stored.subject)

    def _issue_tokens(
        self,
        client_id: str,
        scopes: list[str],
        resource: str | None,
        subject: str | None,
    ) -> OAuthToken:
        now = int(time.time())
        access_value = f"ozon_access_{secrets.token_urlsafe(32)}"
        refresh_value = f"ozon_refresh_{secrets.token_urlsafe(48)}"
        self.access_tokens[access_value] = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + 3600,
            resource=resource,
            subject=subject,
            claims={"iss": self.issuer_url},
        )
        self.refresh_tokens[refresh_value] = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + 30 * 24 * 3600,
            resource=resource,
            subject=subject,
        )
        return OAuthToken(
            access_token=access_value,
            expires_in=3600,
            scope=" ".join(scopes),
            refresh_token=refresh_value,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = self.access_tokens.get(token)
        if access is None or (access.expires_at and access.expires_at < time.time()):
            self.access_tokens.pop(token, None)
            return None
        return access

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        token = self.refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        if token.expires_at and token.expires_at < time.time():
            self.refresh_tokens.pop(refresh_token, None)
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        stored = await self.load_refresh_token(client, refresh_token.token)
        if stored is None:
            raise TokenError(error="invalid_grant", error_description="Invalid refresh token")
        requested = scopes or stored.scopes
        if not set(requested).issubset(stored.scopes):
            raise TokenError(error="invalid_scope", error_description="Scope is not allowed")
        del self.refresh_tokens[stored.token]
        return self._issue_tokens(
            stored.client_id,
            requested,
            stored.resource,
            stored.subject,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access_tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)
