"""Test end-to-end dell'API contro l'inverter finto (UDP su localhost)."""

import asyncio
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
        r = await client.post("/api/leggi")
        body = r.json()
        assert body["errors"] == {}
        data = body["data"]
        assert any(f["key"] == "model" and f["value"] == "GW5048-EM" for f in data["device"])
        assert len(data["eco"]) == 4
        assert data["eco"][0]["mode"] == "carica" and data["eco"][0]["enabled"]
        assert data["offgrid_dod"]["value"] == 20
        # 4 letture, nessuna scrittura
        assert len(fake.received) == 4
        assert all(f[4] == 0x01 for f in fake.received)
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_dry_run_sends_nothing(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=False)
    try:
        await client.post("/api/leggi")
        n = len(fake.received)
        plan = (await client.post("/api/prepara", json={"setting": "export_limit_w", "value": 3000})).json()
        assert plan["frames"][0]["request_hex"].startswith("AA 55 C0 7F 03 35 02 0B B8")
        r = (await client.post("/api/esegui", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "prova a secco"  # WRITES_ENABLED=false prevale
        assert len(fake.received) == n
        log = (await client.get("/api/registro")).json()["changes"]
        assert log[0]["prova_a_secco"] and log[0]["impostazione"] == "export_limit_w"
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_write_and_reread(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=True)
    try:
        await client.post("/api/leggi")
        # protezione SoC attiva, poi DoD 70 %
        for setting, value in (("soc_protect", True), ("ongrid_dod", 70)):
            plan = (await client.post("/api/prepara", json={"setting": setting, "value": value})).json()
            r = (await client.post("/api/esegui", json={"id": plan["id"], "dry_run": False})).json()
            assert r["outcome"] == "ok", r
        assert fake.ongrid_dod == 70
        assert r["reread"] == 70
        # fascia eco 3
        group = {"index": 2, "start": "12:00", "end": "14:30", "mode": "scarica", "power_abs": 40, "days": 0x41, "enabled": True}
        plan = (await client.post("/api/prepara", json={"setting": "eco_group", "group": group})).json()
        r = (await client.post("/api/esegui", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "ok", r
        assert r["reread"]["start"] == "12:00" and r["reread"]["power"] == 40 and r["reread"]["days"] == 0x41
        log = (await client.get("/api/registro")).json()["changes"]
        assert [c["esito"] for c in log] == ["ok", "ok", "ok"]
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_rejected_write_is_reported(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=True)
    try:
        await client.post("/api/leggi")
        fake.reject_ack = True
        plan = (await client.post("/api/prepara", json={"setting": "export_limit_w", "value": 1234})).json()
        r = (await client.post("/api/esegui", json={"id": plan["id"], "dry_run": False})).json()
        assert r["outcome"] == "rifiutato"
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_silent_module_gives_clear_error(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=False, mute_for=100)
    try:
        main.state.get_client().timeout = 0.2
        r = (await client.post("/api/leggi")).json()
        assert "non ha risposto" in r["errors"]["device_info"]
        assert "non risponde" in r["errors"]["settings"]
        assert len(fake.received) == 3  # tre tentativi, poi si ferma
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_retry_after_lost_packet(tmp_path, monkeypatch):
    main, client, fake, transport = await _setup(tmp_path, monkeypatch, writes=False, mute_for=1)
    try:
        main.state.get_client().timeout = 0.3
        r = (await client.post("/api/leggi")).json()
        assert r["errors"] == {}
        assert r["exchanges"][0]["attempts"] == 2
    finally:
        await client.aclose()
        transport.close()


@pytest.mark.anyio
async def test_basic_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_PASSWORD", "segreta")
    import app.store
    import app.main

    importlib.reload(app.store)
    main = importlib.reload(app.main)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
        assert (await c.get("/api/config")).status_code == 401
        assert (await c.get("/api/config", auth=("admin", "segreta"))).status_code == 200
        assert (await c.get("/salute")).status_code == 200
    monkeypatch.delenv("PORTAL_PASSWORD")
    importlib.reload(app.main)


def test_no_protection_write_builders():
    """Il catalogo non deve contenere costruttori di scrittura per i parametri di protezione."""
    from app import es

    names = [n for n in dir(es) if n.startswith("plan_")]
    assert not any(k in n for n in names for k in ("protect_param", "safety", "country", "voltage", "frequency", "island"))
