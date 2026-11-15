"""Protection of endpoints that change data (POST / PATCH / PUT / DELETE).

Reading is public. Writing needs the admin token from settings (APP_ADMIN_TOKEN),
sent in the "X-Admin-Token" header (in /docs: the "Authorize" button).
Without a configured token, writing is allowed only from this computer (localhost),
so a public server is read-only by default.
"""
import secrets
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.config import get_settings

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

admin_token_header = APIKeyHeader(
    name="X-Admin-Token",
    auto_error=False,
    description="Admin token (APP_ADMIN_TOKEN) - needed to add, change or delete data",
)


def require_admin(request: Request, token: Annotated[str | None, Security(admin_token_header)]) -> None:
    """Allow the request only for the admin (valid token) or, with no token configured, from localhost."""
    expected = get_settings().admin_token
    if expected:
        if not token or not secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Missing or invalid X-Admin-Token header")
        return
    client_host = request.client.host if request.client else None
    if client_host not in LOCAL_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="Changing data is disabled on this server (set APP_ADMIN_TOKEN to enable it)",
        )
