"""
apps/api/pgserver_runner.py

Development helper: start an embedded pgserver on a fixed port (11667) so
the API and pytest can connect to a local Postgres without Docker.

Uses the bundled pgserver PostgreSQL binaries directly via pg_ctl, so
the port is fully under our control (pgserver's high-level
``ensure_postgres_running`` auto-allocates a free port, which is not
what we want for a fixed-port dev DB).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Force C locale so initdb doesn't pick Chinese (Simplified)_China.936
# which lacks a text search config and causes initdb to fail on dev
# machines with a non-English Windows install.
os.environ.setdefault("LANG", "C")
os.environ.setdefault("LC_ALL", "C")

import asyncpg  # noqa: E402
import pgserver  # noqa: E402

PGDATA = Path(os.environ.get("BIZ_BP_PGDATA") or Path.cwd() / ".pgdata")
PORT = int(os.environ.get("BIZ_BP_PGPORT") or "11667")
USER = os.environ.get("BIZ_BP_PGUSER") or "finbp"
PASSWORD = os.environ.get("BIZ_BP_PGPASSWORD") or "finbp"
DBNAME = os.environ.get("BIZ_BP_PGDATABASE") or "finbp"


def _kill_stale_postgres() -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/F", "/IM", "postgres.exe"],
        check=False,
        capture_output=True,
    )
    time.sleep(0.5)


def _is_ready(host: str, port: int) -> bool:
    async def _ping() -> bool:
        try:
            conn = await asyncpg.connect(
                host=host, port=port, user="postgres",
                password="", database="postgres",
            )
            await conn.close()
            return True
        except Exception:
            return False
    try:
        return asyncio.run(_ping())
    except Exception:
        return False


def _init_pgdata() -> None:
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    # Force C locale for the cluster (text search config + collation).
    # Pin the superuser to ``postgres`` so we can log in as it later.
    pgserver.initdb(
        args=[
            "--encoding=UTF8",
            "--locale=C",
            "-U", "postgres",
        ],
        pgdata=PGDATA,
    )
    (PGDATA / "postgresql.conf").write_text(
        "\n".join([
            "port = 11667",
            "listen_addresses = '127.0.0.1'",
            "max_connections = 100",
            "shared_buffers = 128MB",
        ]) + "\n",
        encoding="utf-8",
    )
    (PGDATA / "pg_hba.conf").write_text(
        "host all all 127.0.0.1/32 trust\n"
        "host all all ::1/128 trust\n"
        "local all all trust\n",
        encoding="utf-8",
    )


def _start_postgres() -> None:
    pg_ctl = pgserver.pg_ctl
    pg_ctl(
        args=[
            "-w",
            "-o", "-h 127.0.0.1",
            "-l", str(PGDATA / "postgresql.log"),
            "start",
        ],
        pgdata=PGDATA,
        timeout=15,
    )


def _stop_postgres() -> None:
    pg_ctl = pgserver.pg_ctl
    try:
        pg_ctl(args=["-m", "fast", "stop"], pgdata=PGDATA, timeout=10)
    except Exception:
        pass


def _bootstrap_role_and_db() -> None:
    async def _do() -> None:
        conn = await asyncpg.connect(
            host="127.0.0.1", port=PORT,
            user="postgres", password="", database="postgres",
        )
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM pg_roles WHERE rolname=$1", USER
            )
            if row is None:
                await conn.execute(
                    f"CREATE USER {USER} WITH PASSWORD '{PASSWORD}' SUPERUSER"
                )
            else:
                await conn.execute(
                    f"ALTER USER {USER} WITH PASSWORD '{PASSWORD}' SUPERUSER"
                )
            row = await conn.fetchrow(
                "SELECT 1 FROM pg_database WHERE datname=$1", DBNAME
            )
            if row is None:
                await conn.execute(f"CREATE DATABASE {DBNAME} OWNER {USER}")
        finally:
            await conn.close()
    asyncio.run(_do())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bg", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--stop", action="store_true")
    args = p.parse_args()

    if args.status:
        ok = _is_ready("127.0.0.1", PORT)
        print(f"pgdata={PGDATA} port={PORT} ready={ok}")
        return 0 if ok else 1

    if args.stop:
        _stop_postgres()
        print("stopped")
        return 0

    if args.reset:
        _stop_postgres()
        _kill_stale_postgres()
        if PGDATA.exists():
            shutil.rmtree(PGDATA, ignore_errors=True)

    if not (PGDATA / "PG_VERSION").exists():
        print(f"initializing pgdata at {PGDATA} ...")
        _init_pgdata()

    if not _is_ready("127.0.0.1", PORT):
        print(f"starting postgres on port {PORT} ...")
        _start_postgres()
    for _ in range(30):
        if _is_ready("127.0.0.1", PORT):
            break
        time.sleep(0.5)
    else:
        print("postgres did not become ready in 15s")
        return 1

    _bootstrap_role_and_db()
    print(f"pgserver ready at 127.0.0.1:{PORT}/db={DBNAME} user={USER}")
    if args.bg:
        while True:
            time.sleep(60)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("stopping pgserver...")
        _stop_postgres()
    return 0


if __name__ == "__main__":
    sys.exit(main())
