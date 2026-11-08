"""Hosted-Postgres DATABASE_URL handling (Neon / Render style URLs)."""

import ssl

import pytest

from app.db_url import asyncpg_dsn_and_args, normalize_database_url, split_ssl


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@h:5432/db",
        "postgresql://u:p@h:5432/db",
        "postgresql+psycopg2://u:p@h:5432/db",
        "  postgresql+asyncpg://u:p@h:5432/db ",
    ],
)
def test_scheme_becomes_asyncpg(raw: str) -> None:
    assert normalize_database_url(raw) == "postgresql+asyncpg://u:p@h:5432/db"


def test_neon_url_is_split_into_clean_url_and_ssl_arg() -> None:
    url, args = split_ssl("postgresql+asyncpg://u:p@ep-x.neon.tech/neondb?sslmode=require&channel_binding=require")
    assert url.render_as_string(hide_password=False) == "postgresql+asyncpg://u:p@ep-x.neon.tech/neondb"
    assert args == {"ssl": "require"}


def test_no_sslmode_means_no_ssl_arg() -> None:
    url, args = split_ssl("postgresql+asyncpg://u:p@h:5432/db?application_name=crm")
    assert args == {}
    assert url.query == {"application_name": "crm"}  # unrelated parameters are kept


def test_disable_and_verify_modes() -> None:
    assert split_ssl("postgresql+asyncpg://u:p@h/db?sslmode=disable")[1] == {"ssl": False}
    full = split_ssl("postgresql+asyncpg://u:p@h/db?sslmode=verify-full")[1]["ssl"]
    assert isinstance(full, ssl.SSLContext) and full.check_hostname and full.verify_mode == ssl.CERT_REQUIRED
    ca = split_ssl("postgresql+asyncpg://u:p@h/db?sslmode=verify-ca")[1]["ssl"]
    assert isinstance(ca, ssl.SSLContext) and not ca.check_hostname and ca.verify_mode == ssl.CERT_REQUIRED


def test_invalid_sslmode_is_rejected() -> None:
    with pytest.raises(ValueError, match="sslmode must be one of"):
        split_ssl("postgresql+asyncpg://u:p@h/db?sslmode=yes-please")


def test_raw_asyncpg_dsn() -> None:
    dsn, args = asyncpg_dsn_and_args("postgresql+asyncpg://u:p%40ss@h:5432/db?sslmode=require")
    assert dsn == "postgresql://u:p%40ss@h:5432/db"
    assert args == {"ssl": "require"}
