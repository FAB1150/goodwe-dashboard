"""End-to-end API tests against the fake inverter (UDP on localhost)."""

import importlib

import httpx
import pytest

from tests import fake_inverter


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _setup(tmp_path, monkeypatch, writes: bool, **fake_kw):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WRITES_ENABLED", "true" if writes else "false")
    monkeypatch.delenv("PORTAL_PASSWORD", raising=False)
    import app.store
    import app.main

    importlib.reload(app.store)
    main = importlib.reload(app.main)
    transport, fake, port = await fake_inverter.start(**fake_kw)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
    r = await client.post("/api/config", json={"host": "127.0.0.1", "port": port})
    assert r.status_code == 200
    return main, client, fake, transport


@pytest.mark.anyio
async def test_read_all(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=False)
    try:
        r = await client.post("/api/read")
        body = r.json()
        assert body["errors"] == {}
        data = body["data"]
        assert any(f["key"] == "model" and f["value"] == "GW5048-EM" for f in data["device"])
        assert len(data["eco"]) == 4
        assert data["eco"][0]["mode"] == "charge" and data["eco"][0]["enabled"]
        battery = {f["key"]: f for f in data["settings"]["battery"]}
        assert battery["offgrid_dod"]["value"] == 20
        grid = {f["key"]: f for f in data["settings"]["grid"]}
        assert grid["dred"]["value"] == "off" and grid["dred"]["control"] is None
        # every field has an explanation
        for group in data["settings"].values():
            assert all(f["help"] for f in group)
        # 5 reads (4 AA55 + 1 Modbus), no writes
        assert len(fake.received) == 5
        assert all(f[4] == 0x01 for f in fake.received[:4]) and fake.received[4][:2] == b"\xf7\x03"
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_dry_run_sends_nothing(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=False)
    try:
        await client.post("/api/read")
        n = len(fake.received)
        plan = (await client.post("/api/prepare", json={"setting": "export_limit_w", "value": 3000})).json()
        assert plan["frames"][0]["request_hex"].startswith("AA 55 C0 7F 03 35 02 0B B8")
        r = (await client.post("/api/execute", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "dry run"  # WRITES_ENABLED=false wins
        assert len(fake.received) == n
        log = (await client.get("/api/changes")).json()["changes"]
        assert log[0]["dry_run"] and log[0]["setting"] == "export_limit_w"
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_write_and_reread(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=True)
    try:
        await client.post("/api/read")
        # SoC protection on, then DoD 70 %
        for setting, value in (("soc_protect", True), ("ongrid_dod", 70)):
            plan = (await client.post("/api/prepare", json={"setting": setting, "value": value})).json()
            r = (await client.post("/api/execute", json={"id": plan["id"], "dry_run": False})).json()
            assert r["outcome"] == "ok", r
        assert fake.ongrid_dod == 70
        assert r["reread"] == 70
        # eco group 3
        group = {"index": 2, "start": "12:00", "end": "14:30", "mode": "discharge", "power_abs": 40, "days": 0x41, "enabled": True}
        plan = (await client.post("/api/prepare", json={"setting": "eco_group", "group": group})).json()
        r = (await client.post("/api/execute", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "ok", r
        assert r["reread"]["start"] == "12:00" and r["reread"]["power"] == 40 and r["reread"]["days"] == 0x41
        log = (await client.get("/api/changes")).json()["changes"]
        assert [c["outcome"] for c in log] == ["ok", "ok", "ok"]
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_rejected_write_is_reported(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=True)
    try:
        await client.post("/api/read")
        fake.reject_ack = True
        plan = (await client.post("/api/prepare", json={"setting": "export_limit_w", "value": 1234})).json()
        r = (await client.post("/api/execute", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "rejected"
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_silent_module_gives_clear_error(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=False, mute_for=100)
    try:
        main.state.get_client().timeout = 0.2
        r = (await client.post("/api/read")).json()
        assert "did not answer" in r["errors"]["device_info"]
        assert "not responding" in r["errors"]["settings"]
        assert len(fake.received) == 3  # three attempts, then it stops
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_retry_after_lost_packet(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=False, mute_for=1)
    try:
        main.state.get_client().timeout = 0.3
        r = (await client.post("/api/read")).json()
        assert r["errors"] == {}
        assert r["exchanges"][0]["attempts"] == 2
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_basic_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_PASSWORD", "secret")
    import app.store
    import app.main

    importlib.reload(app.store)
    main = importlib.reload(app.main)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
        assert (await c.get("/api/config")).status_code == 401
        assert (await c.get("/api/config", auth=("admin", "secret"))).status_code == 200
        assert (await c.get("/health")).status_code == 200
    monkeypatch.delenv("PORTAL_PASSWORD")
    importlib.reload(app.main)


def test_no_protection_write_builders():
    """The catalogue must not contain write builders for protection parameters."""
    from app import es

    names = [n for n in dir(es) if n.startswith("plan_")]
    assert not any(k in n for n in names for k in ("protect_param", "safety", "country", "voltage", "frequency", "island"))


@pytest.mark.anyio
async def test_eco_schedule_save_and_reread(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=True)
    try:
        await client.post("/api/read")
        groups = [
            {"index": 0, "start": "02:00", "end": "06:00", "mode": "charge", "power_abs": 50, "days": 127, "enabled": True},
            {"index": 2, "start": "12:00", "end": "13:00", "mode": "discharge", "power_abs": 20, "days": 1, "enabled": True},
        ]
        plan = (await client.post("/api/prepare", json={"setting": "eco_schedule", "groups": groups})).json()
        assert len(plan["frames"]) == 3
        r = (await client.post("/api/execute", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "ok", r
        assert [g["index"] for g in r["reread"]] == [0, 2]
        assert r["data"]["eco"][0]["start"] == "02:00" and r["data"]["eco"][2]["enabled"]
        bad = [{"index": 3, "start": "03:00", "end": "04:00", "mode": "discharge", "power_abs": 20, "days": 127, "enabled": True}]
        r = await client.post("/api/prepare", json={"setting": "eco_schedule", "groups": bad})
        assert r.status_code == 400 and "opposite" in r.json()["detail"]
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_advanced_switches_write_and_reread(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=True)
    try:
        await client.post("/api/read")
        cases = [
            ("shadow_scan", True, "on"),
            ("backup_supply", False, "off"),
            ("export_limit_enabled", False, "off"),
            ("battery_activated", True, "on"),
            ("detection_mode", 1, "UPS mode – half wave detection"),
        ]
        for setting, value, expected in cases:
            plan = (await client.post("/api/prepare", json={"setting": setting, "value": value})).json()
            r = (await client.post("/api/execute", json={"id": plan["id"], "dry_run": False})).json()
            assert r["outcome"] == "ok", (setting, r)
            assert r["reread"] == expected
        plan = (await client.post("/api/prepare", json={"setting": "overload_reset"})).json()
        r = (await client.post("/api/execute", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "ok"
        for blocked in ("power_factor", "dred", "safety_country"):
            r = await client.post("/api/prepare", json={"setting": blocked, "value": 1})
            assert r.status_code == 400
    finally:
        await client.aclose()
        transport.close()
