"""Web portal for the settings of a GoodWe ES/EM inverter.

No periodic polling: the inverter is read only when the page (or a section) is opened
or refreshed, and written only when a save is confirmed.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import secrets
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field as PField

from . import es
from . import protocol as p
from .store import Store
from .transport import Exchange, InverterUnreachable, UdpClient, WriteNotAllowed, dry_run_exchange

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("portal")

WRITES_ENABLED = os.environ.get("WRITES_ENABLED", "false").lower() in ("1", "true", "yes")
PORTAL_USER = os.environ.get("PORTAL_USER", "admin")
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD", "")
CACHE_MAX_AGE = 600  # seconds: older cached data is re-read before preparing a write
PLAN_TTL = 300

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="GoodWe portal", docs_url=None, redoc_url=None)
store = Store()


# ---------------------------------------------------------------------------
# Simple authentication (HTTP Basic), active only when PORTAL_PASSWORD is set
# ---------------------------------------------------------------------------


@app.middleware("http")
async def basic_auth(request: Request, call_next):  # noqa: ANN001
    if PORTAL_PASSWORD and request.url.path != "/health":
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, PORTAL_USER) and secrets.compare_digest(pwd, PORTAL_PASSWORD)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="GoodWe portal"'})
    return await call_next(request)


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------


class State:
    def __init__(self) -> None:
        self.client: UdpClient | None = None
        self.raw: dict[str, bytes] = {}
        self.read_at: dict[str, float] = {}
        self.plans: dict[str, tuple[float, es.WritePlan]] = {}

    def get_client(self) -> UdpClient:
        cfg = store.get_config()
        host = cfg.get("host") or ""
        if not host:
            raise HTTPException(400, "Set the inverter IP address first.")
        port = int(cfg.get("port") or 8899)
        if not self.client or self.client.host != host or self.client.port != port:
            self.client = UdpClient(host, port)
            self.raw.clear()
            self.read_at.clear()
        return self.client


state = State()


def _exchanges(trace: list[Exchange]) -> list[dict]:
    return [asdict(x) for x in trace]


async def _read(keys: list[str], steps: list[es.ReadStep], trace: list[Exchange]) -> dict[str, str]:
    """Run the requested reads; return errors by key (other reads carry on)."""
    client = state.get_client()
    errors: dict[str, str] = {}
    for step in steps:
        if step.key not in keys:
            continue
        try:
            resp = await client.request(step.request, step.description, trace)
            data = resp.data if isinstance(resp, p.Aa55Response) else resp
            state.raw[step.key] = data
            state.read_at[step.key] = time.time()
        except (InverterUnreachable, p.ProtocolError) as e:
            errors[step.key] = str(e)
            if isinstance(e, InverterUnreachable):
                # if the module is silent there is no point in trying the other reads
                for other in steps:
                    if other.key in keys and other.key not in errors and other.key not in state.raw:
                        errors[other.key] = "not read: the inverter is not responding"
                break
    return errors


def _view() -> dict[str, Any]:
    """Decode the cached raw data into what the page shows."""
    out: dict[str, Any] = {"read_at": state.read_at}
    if "device_info" in state.raw:
        info = es.parse_device_info(state.raw["device_info"])
        out["device"] = [f.as_dict() for f in es.device_info_fields(info)]
        out["device_warning"] = (
            "" if info.family_ok else
            f"Serial number {info.serial!r} does not look like an ES/EM: the portal's commands are meant for that family."
        )
    if "settings" in state.raw:
        s = es.parse_settings(state.raw["settings"])
        out["settings"] = {k: [f.as_dict() for f in v] for k, v in es.settings_fields(s).items()}
        out["work_mode"] = s.get("work_mode")
        out["soc_protect_on"] = es.soc_protect_on(s)
    if "eco" in state.raw:
        out["eco"] = [g.as_dict() for g in es.parse_eco_groups(state.raw["eco"])]
    if "offgrid_dod" in state.raw and len(state.raw["offgrid_dod"]) == 2:
        raw = int.from_bytes(state.raw["offgrid_dod"], "big")
        out["offgrid_dod"] = {"value": 100 - raw, "raw": raw}
    if "protection" in state.raw:
        out["protection"] = [
            f.as_dict() for f in es.parse_register_block(es.PROTECTION_BLOCK[0], state.raw["protection"], es.PROTECTION_FIELDS)
        ]
    if "connect" in state.raw:
        out["connect"] = [
            f.as_dict() for f in es.parse_register_block(es.CONNECT_BLOCK[0], state.raw["connect"], es.CONNECT_FIELDS)
        ]
    return out


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class ConfigIn(BaseModel):
    host: str
    port: int = PField(8899, ge=1, le=65535)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
async def get_config() -> dict:
    cfg = store.get_config()
    return {**cfg, "writes_enabled": WRITES_ENABLED}


@app.post("/api/config")
async def set_config(cfg: ConfigIn) -> dict:
    host = cfg.host.strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not host or any(c.isspace() for c in host) or len(host) > 253:
            raise HTTPException(400, "Invalid address.")
    saved = store.set_config(host=host, port=cfg.port)
    state.client = None
    state.raw.clear()
    state.read_at.clear()
    return {**saved, "writes_enabled": WRITES_ENABLED}


@app.post("/api/read")
async def read_all() -> JSONResponse:
    """Full read (page opened / refreshed)."""
    trace: list[Exchange] = []
    steps = es.page_read_steps()
    errors = await _read([s.key for s in steps], steps, trace)
    return JSONResponse({"data": _view(), "errors": errors, "exchanges": _exchanges(trace)})


@app.post("/api/read/protection")
async def read_protection() -> JSONResponse:
    """Grid protection parameters: read only on request, never written."""
    trace: list[Exchange] = []
    steps = es.protection_read_steps()
    errors = await _read([s.key for s in steps], steps, trace)
    return JSONResponse({"data": _view(), "errors": errors, "exchanges": _exchanges(trace)})


@app.get("/api/state")
async def cached_view() -> dict:
    return {"data": _view()}


class PlanIn(BaseModel):
    setting: str
    value: Any = None
    group: dict | None = None


async def _ensure_fresh(keys: list[str]) -> None:
    stale = [k for k in keys if k not in state.raw or time.time() - state.read_at.get(k, 0) > CACHE_MAX_AGE]
    if stale:
        trace: list[Exchange] = []
        errors = await _read(stale, es.page_read_steps(), trace)
        if errors:
            raise HTTPException(503, "Could not read the current state before the change: " + "; ".join(errors.values()))


def _build_plan(req: PlanIn) -> es.WritePlan:
    s = es.parse_settings(state.raw["settings"]) if "settings" in state.raw else None
    groups = es.parse_eco_groups(state.raw["eco"]) if "eco" in state.raw else []
    st = req.setting
    try:
        if st == "work_mode":
            return es.plan_work_mode(s.get("work_mode") if s else None, int(req.value))
        if st == "soc_protect":
            return es.plan_soc_protect(s, bool(req.value))
        if st == "ongrid_dod":
            return es.plan_ongrid_dod(s, int(req.value))
        if st == "offgrid_dod":
            cur = None
            if "offgrid_dod" in state.raw and len(state.raw["offgrid_dod"]) == 2:
                cur = 100 - int.from_bytes(state.raw["offgrid_dod"], "big")
            return es.plan_offgrid_dod(cur, int(req.value))
        if st == "export_limit_w":
            return es.plan_export_limit(s, int(req.value))
        if st == "eco_group":
            g = req.group or {}
            power = abs(int(g["power_abs"])) * (-1 if g.get("mode") == "charge" else 1)
            group = es.EcoGroup(int(g["index"]), str(g["start"]), str(g["end"]), power, bool(g.get("enabled", True)), int(g["days"]))
            return es.plan_eco_group(groups, group)
        if st == "eco_switch":
            g = req.group or {}
            return es.plan_eco_switch(groups, int(g["index"]), bool(g["enabled"]))
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"Invalid value: {e}") from e
    raise HTTPException(400, f"Unknown or non-writable setting: {st}")


@app.post("/api/prepare")
async def prepare(req: PlanIn) -> dict:
    """Prepare a change: return the frames that would be sent, to be confirmed."""
    needed = ["settings", "eco"] if req.setting.startswith("eco") else ["settings"]
    if req.setting == "offgrid_dod":
        needed = ["offgrid_dod"]
    state.get_client()
    await _ensure_fresh(needed)
    plan = _build_plan(req)
    frames = []
    for step in plan.steps:
        try:
            frames.append(asdict(dry_run_exchange(step.request, step.description)))
        except WriteNotAllowed as e:
            raise HTTPException(403, f"Command blocked: {e}") from e
    now = time.time()
    state.plans = {k: v for k, v in state.plans.items() if now - v[0] < PLAN_TTL}
    plan_id = uuid.uuid4().hex
    state.plans[plan_id] = (now, plan)
    return {
        "id": plan_id,
        "setting": plan.setting,
        "label": plan.label,
        "before": plan.before,
        "after": plan.after,
        "warning": plan.warning,
        "frames": frames,
        "writes_enabled": WRITES_ENABLED,
    }


class ExecuteIn(BaseModel):
    id: str
    dry_run: bool = True


@app.post("/api/execute")
async def execute(req: ExecuteIn) -> dict:
    entry = state.plans.pop(req.id, None)
    if not entry or time.time() - entry[0] > PLAN_TTL:
        raise HTTPException(410, "Change expired: prepare it again.")
    plan = entry[1]
    dry = req.dry_run or not WRITES_ENABLED
    trace: list[Exchange] = []
    outcome = "ok"
    message = ""
    if dry:
        trace = [dry_run_exchange(s.request, s.description) for s in plan.steps]
        outcome = "dry run"
        message = (
            "Dry run: no command sent." if WRITES_ENABLED else
            "Writes are disabled (WRITES_ENABLED=false): no command sent."
        )
    else:
        client = state.get_client()
        for step in plan.steps:
            try:
                resp = await client.request(step.request, step.description, trace)
            except (InverterUnreachable, p.ProtocolError, WriteNotAllowed) as e:
                outcome = "error"
                message = str(e)
                break
            if step.must_ack and not resp.is_ack:
                outcome = "rejected"
                message = f"The inverter did not acknowledge '{step.description}' (reply {resp.data.hex(' ').upper()})."
                break
    reread: Any = None
    if not dry:
        await asyncio.sleep(0.5)
        for k in plan.verify:
            state.read_at.pop(k, None)
        verify_trace: list[Exchange] = []
        errors = await _read(plan.verify, es.page_read_steps(), verify_trace)
        trace.extend(verify_trace)
        view = _view()
        reread = _extract(plan.setting, view)
        if errors:
            message = (message + " " if message else "") + "Read-back failed: " + "; ".join(errors.values())
            if outcome == "ok":
                outcome = "not verified"
        elif outcome == "ok" and not _matches(plan, reread):
            outcome = "not applied"
            message = "The inverter acknowledged the command but the value read back differs from the requested one."
    frames = _exchanges(trace)
    store.log_change(plan.setting, plan.label, plan.before, plan.after, reread, outcome, dry, frames)
    return {"outcome": outcome, "message": message, "reread": reread, "exchanges": frames, "data": _view()}


def _extract(setting: str, view: dict) -> Any:
    if setting == "work_mode":
        return es.WORK_MODES.get(view.get("work_mode"), view.get("work_mode"))
    if setting == "soc_protect":
        return "on" if view.get("soc_protect_on") else "off"
    if setting in ("ongrid_dod", "export_limit_w"):
        for fields in view.get("settings", {}).values():
            for f in fields:
                if f["key"] == setting:
                    return f["value"]
    if setting == "offgrid_dod":
        return (view.get("offgrid_dod") or {}).get("value")
    if setting.startswith("eco_group_"):
        idx = int(setting.split("_")[2]) - 1
        groups = view.get("eco") or []
        g = next((x for x in groups if x["index"] == idx), None)
        if g and setting.endswith("_switch"):
            return "enabled" if g["enabled"] else "disabled"
        return g
    return None


def _matches(plan: es.WritePlan, reread: Any) -> bool:
    if isinstance(plan.after, dict) and isinstance(reread, dict):
        keys = ("start", "end", "power", "enabled", "days")
        return all(plan.after.get(k) == reread.get(k) for k in keys)
    return plan.after == reread


@app.get("/api/changes")
async def changes() -> dict:
    return {"changes": store.list_changes()}


@app.exception_handler(InverterUnreachable)
async def unreachable_handler(_: Request, exc: InverterUnreachable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})
