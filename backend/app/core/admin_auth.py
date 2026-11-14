"""
Admin authentication for operator / scheduler endpoints.

Callers send the ADMIN_SECRET_KEY in the ``X-Admin-Key`` header.

Two levels:
  * require_admin_key(...)        always enforced. Used where an
                                  unauthenticated call can change which model
                                  trades or bypass safety checks (promote,
                                  force-training, manual retrain).
  * scheduler_auth_dependency     governed by ADMIN_AUTH=log_only|enforce
                                  (default log_only), for endpoints Cloud
                                  Scheduler calls today WITHOUT the header.
                                  log_only lets the call through and logs
                                  "ADMIN_AUTH log_only: WOULD HAVE REJECTED",
                                  so you can add the header to the scheduler
                                  jobs first, then switch to enforce.

Comparison is constant-time. If ADMIN_SECRET_KEY is not configured,
always-enforced endpoints refuse (503) rather than open up.
"""
import hmac
import logging
import os
from typing import Optional

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)


def admin_auth_mode() -> str:
    raw = os.getenv("ADMIN_AUTH", "log_only").strip().lower()
    if raw not in ("log_only", "enforce"):
        logger.warning(f"Unknown ADMIN_AUTH={raw!r}; using log_only")
        return "log_only"
    return raw


def _key_ok(provided: Optional[str]) -> Optional[bool]:
    """True/False for a configured key; None if the server has no key."""
    expected = os.getenv("ADMIN_SECRET_KEY")
    if not expected:
        return None
    return bool(provided) and hmac.compare_digest(provided, expected)


def require_admin_key(provided: Optional[str], action: str) -> None:
    """Always-enforced check. Raises HTTPException on failure."""
    ok = _key_ok(provided)
    if ok is None:
        logger.error(f"Refusing {action}: ADMIN_SECRET_KEY is not configured")
        raise HTTPException(status_code=503, detail="ADMIN_SECRET_KEY is not configured on server")
    if not ok:
        logger.warning(f"Rejected unauthenticated {action}")
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key")


async def admin_key_dependency(
    request: Request,
    x_admin_key: Optional[str] = Header(None)
) -> None:
    """FastAPI dependency: always enforced."""
    require_admin_key(x_admin_key, f"{request.method} {request.url.path}")


async def scheduler_auth_dependency(
    request: Request,
    x_admin_key: Optional[str] = Header(None)
) -> None:
    """FastAPI dependency: enforced only when ADMIN_AUTH=enforce."""
    action = f"{request.method} {request.url.path}"
    if _key_ok(x_admin_key):
        return
    if admin_auth_mode() == "enforce":
        require_admin_key(x_admin_key, action)
        return
    logger.warning(
        f"ADMIN_AUTH log_only: WOULD HAVE REJECTED unauthenticated {action} "
        f"(add header X-Admin-Key to the caller, then set ADMIN_AUTH=enforce)"
    )
