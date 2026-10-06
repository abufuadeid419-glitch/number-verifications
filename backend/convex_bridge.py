"""FastAPI -> Convex write-through bridge.

FastAPI + MongoDB remain the source of truth. After each relevant write we mirror
the document into the external Convex deployment so the app can read the ported
domains (routes, payment vouchers, purchases, notifications) and the live agent
map straight from Convex in real time.

Every call is fire-and-forget and fully swallowed: if Convex is unreachable the
main app keeps working exactly as before. Mirroring goes through the
secret-guarded `bridge:*` Convex mutations (see frontend/convex/bridge.ts).
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

CONVEX_URL = (os.environ.get("CONVEX_URL") or "").strip().rstrip("/")
BRIDGE_SECRET = os.environ.get("CONVEX_BRIDGE_SECRET") or "smartsystem-migrate-2026"
_MUTATION_URL = f"{CONVEX_URL}/api/mutation" if CONVEX_URL else ""


async def _run(path: str, args: dict):
    if not _MUTATION_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(_MUTATION_URL, json={"path": path, "args": args, "format": "json"})
            if r.status_code != 200 or (r.json() or {}).get("status") != "success":
                logger.warning(f"convex bridge {path} failed: {r.status_code} {r.text[:200]}")
    except Exception as e:  # never break the request on a mirror failure
        logger.warning(f"convex bridge {path} error: {e}")


def _spawn(path: str, args: dict):
    """Schedule a mirror without blocking the API response."""
    try:
        asyncio.get_running_loop().create_task(_run(path, args))
    except RuntimeError:
        pass


def _clean(doc: dict) -> dict:
    return {k: v for k, v in dict(doc).items() if k not in ("_id", "_creationTime")}


def cx_upsert(table: str, key_field: str, doc: dict):
    """Upsert a document into Convex matched by a single business key field."""
    _spawn("bridge:upsert", {"secret": BRIDGE_SECRET, "table": table, "keyField": key_field, "doc": _clean(doc)})


def cx_insert(table: str, doc: dict):
    """Append-only insert (e.g. agent_locations GPS pings)."""
    _spawn("bridge:insertRow", {"secret": BRIDGE_SECRET, "table": table, "doc": _clean(doc)})


def cx_delete(table: str, key_field: str, value):
    _spawn("bridge:removeByKey", {"secret": BRIDGE_SECRET, "table": table, "keyField": key_field, "value": value})
