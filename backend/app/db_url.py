"""DATABASE_URL handling for hosted Postgres (Neon, Render, etc.).

Hosted providers hand out libpq-style URLs such as
    postgres://user:pass@ep-x.neon.tech/db?sslmode=require&channel_binding=require
SQLAlchemy's asyncpg dialect needs the `postgresql+asyncpg://` scheme. It passes
query parameters to asyncpg.connect() as keyword arguments, and asyncpg has no
`sslmode` or `channel_binding` keyword (it would also send an unknown key to the
server as a setting). So the URL is split into a clean URL plus an `ssl` connect
argument:

    sslmode=disable                    -> no TLS
    sslmode=allow | prefer             -> TLS if the server offers it
    sslmode=require                    -> TLS, certificate not verified (libpq semantics)
    sslmode=verify-ca | verify-full    -> TLS, certificate and hostname verified against system CAs

channel_binding is dropped: asyncpg negotiates SCRAM itself.
"""

from __future__ import annotations

import ssl as ssl_module
from typing import Any

from sqlalchemy.engine import URL, make_url

ASYNC_DRIVER = "postgresql+asyncpg"
_LIBPQ_ONLY = ("sslmode", "ssl", "channel_binding")
_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


def normalize_database_url(raw: str) -> str:
    """Give any postgres URL the async driver scheme; the query string (sslmode, ...) is kept."""
    url = raw.strip()
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return ASYNC_DRIVER + "://" + url[len(prefix):]
    return url


def _ssl_arg(mode: str) -> Any:
    mode = mode.strip().lower()
    if mode not in _SSLMODES:
        raise ValueError(f"DATABASE_URL sslmode must be one of {', '.join(_SSLMODES)} (got {mode!r})")
    if mode == "disable":
        return False
    if mode in ("verify-ca", "verify-full"):
        ctx = ssl_module.create_default_context()
        ctx.check_hostname = mode == "verify-full"
        return ctx
    return mode  # asyncpg accepts "allow" / "prefer" / "require" directly


def split_ssl(url: str | URL) -> tuple[URL, dict[str, Any]]:
    """Return (URL without libpq-only parameters, asyncpg connect kwargs)."""
    parsed = make_url(url) if isinstance(url, str) else url
    query = dict(parsed.query)
    mode = query.get("sslmode") or query.get("ssl")
    if isinstance(mode, tuple):  # repeated parameter: last one wins, like libpq
        mode = mode[-1]
    clean = parsed.set(query={k: v for k, v in query.items() if k not in _LIBPQ_ONLY})
    return clean, ({"ssl": _ssl_arg(mode)} if mode else {})


def asyncpg_dsn_and_args(url: str) -> tuple[str, dict[str, Any]]:
    """For raw asyncpg.connect(): a plain postgresql:// DSN plus connect kwargs."""
    clean, args = split_ssl(url)
    return clean.set(drivername="postgresql").render_as_string(hide_password=False), args
