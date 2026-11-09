"""Test del framing: frame costruiti dal portale e frame registrati da inverter reali."""

from pathlib import Path

import pytest

from app import es
from app import protocol as p
from app.transport import WriteNotAllowed, ensure_write_allowed

SAMPLES = Path(__file__).parent / "samples"


def sample(name: str) -> bytes:
    return bytes.fromhex(SAMPLES.joinpath(name).read_text().strip())


# --- Frame identici a quelli della libreria goodwe (stessa sorgente C0) -----


def test_read_settings_frame_matches_goodwe_library():
    # tests/test_es.py della libreria goodwe: 'aa55c07f0109000248'
    assert p.read_settings().to_bytes().hex() == "aa55c07f0109000248"


def test_export_limit_frame_matches_goodwe_library():
    # goodwe: set_grid_export_limit(100) -> 'aa55c07f033502006402dc'
    plan = es.plan_export_limit({"export_limit_w": 0}, 100)
    assert plan.steps[0].request.to_bytes().hex() == "aa55c07f033502006402dc"


def test_device_info_frame():
    assert p.read_device_info().to_bytes().hex() == "aa55c07f0102000241"  # 0xAA+0x55+0xC0+0x7F+0x01+0x02 = 0x241


# --- Frame "alla SolarGo" (sorgente B0 per i registri) ---------------------


def test_ongrid_dod_uses_b0_source_unlike_home_assistant():
    settings = {"soc_protect_raw": 0, "min_soc_raw": 20}
    frame = es.plan_ongrid_dod(settings, 80).steps[0].request.to_bytes()
    # Home Assistant / goodwe invia: aa55 c07f 0239 05 0560 01 0014 02f8
    ha = bytes.fromhex("aa55c07f023905056001001402f8")
    assert frame[:2] == ha[:2]
    assert frame[2] == 0xB0 and ha[2] == 0xC0  # unica differenza: l'indirizzo sorgente
    assert frame[3:-2] == ha[3:-2]
    assert frame.hex() == "aa55b07f023905056001001402e8"


def test_solargo_register_prefixes():
    # prefissi copiati da DataCollectUtil.build105SettingPackage / build105ReadingPackage
    assert p.read_registers(0x0701, 16).to_bytes()[:-2] == bytes([0xAA, 0x55, 0xB0, 0x7F, 1, 0x1A, 3, 7, 1, 16])
    assert p.read_registers(0x05A1, 1).to_bytes()[:-2] == bytes([0xAA, 0x55, 0xB0, 0x7F, 1, 0x1A, 3, 5, 0xA1, 1])
    g = es.EcoGroup(0, "01:00", "05:00", -50, True, 0x7F)
    assert p.write_registers(0x0701, g.to_bytes()).to_bytes()[:10] == bytes([0xAA, 0x55, 0xB0, 0x7F, 2, 0x39, 11, 7, 1, 4])
    sw = es.plan_eco_switch([g], 0, False).steps[1].request.to_bytes()
    assert sw[:10] == bytes([0xAA, 0x55, 0xB0, 0x7F, 2, 0x39, 5, 7, 4, 1])
    assert sw[10:12] == bytes([0x00, 0x7F])


def test_checksum_is_plain_sum():
    frame = p.read_registers(0x0701, 16).to_bytes()
    assert int.from_bytes(frame[-2:], "big") == sum(frame[:-2])


def test_modbus_crc_known_vector():
    # CRC Modbus di "01 03 00 00 00 0A" = C5CD (byte basso per primo)
    assert p.crc16_modbus(bytes.fromhex("01030000000a")) == 0xCDC5
    req = p.ModbusReadRequest(0xB158, 17)
    assert req.to_bytes()[:6] == bytes.fromhex("f703b1580011")


# --- Parsing di frame registrati da inverter reali ------------------------


def test_parse_recorded_em_device_info():
    resp = p.parse_aa55(sample("GW5048-EM_device_info.hex"))
    assert (resp.ctrl, resp.func) == (0x01, 0x82)
    info = es.parse_device_info(resp.data)
    assert info.model == "GW5048-EM"
    assert "EMU" in info.serial
    assert info.family_ok
    assert info.arm_string.startswith("410-")
    assert info.arm_version == int(info.arm_string.split("-")[2])


def test_parse_recorded_es_settings():
    resp = p.parse_aa55(sample("GW5048D-ES_settings_data.hex"))
    s = es.parse_settings(resp.data)
    # stessi offset della libreria goodwe
    assert s["capacity_ah"] == int.from_bytes(resp.data[22:24], "big")
    assert es.ongrid_dod(s) == 100 - int.from_bytes(resp.data[32:34], "big")
    assert s["work_mode"] in es.WORK_MODES
    fields = es.settings_fields(s)
    assert {f.key for f in fields["battery"]} >= {"soc_protect", "ongrid_dod"}


def test_parse_recorded_eco_register_reply():
    # risposta registrata nella libreria goodwe per la lettura del registro 1793 (0x0701)
    resp = p.parse_aa55(bytes.fromhex("aa557fc0019a08000000000000007f0360"))
    assert resp.data == bytes.fromhex("000000000000007f")


def test_bad_checksum_is_rejected():
    raw = bytearray(sample("GW5048-EM_device_info.hex"))
    raw[-1] ^= 0xFF
    with pytest.raises(p.ProtocolError):
        p.parse_aa55(bytes(raw))


def test_reply_type_mismatch_is_rejected():
    with pytest.raises(p.ProtocolError):
        p.check_aa55_reply(p.read_settings(), sample("GW5048-EM_device_info.hex"))


# --- Fasce eco ---------------------------------------------------------------


def test_eco_group_roundtrip():
    g = es.EcoGroup(2, "13:30", "16:45", -75, True, 0b0111110)
    data = bytes(16) + g.to_bytes() + bytes(8)
    parsed = es.parse_eco_groups(data)[2]
    assert parsed == g
    assert parsed.mode == "carica"
    assert parsed.days_list == ["Lun", "Mar", "Mer", "Gio", "Ven"]


def test_eco_group_validation():
    with pytest.raises(ValueError):
        es.validate_eco_group(es.EcoGroup(0, "22:00", "06:00", -50, True, 0x7F))
    with pytest.raises(ValueError):
        es.validate_eco_group(es.EcoGroup(0, "01:00", "02:00", -150, True, 0x7F))


def test_eco_conflict_detection():
    a = es.EcoGroup(0, "01:00", "05:00", -50, True, 0x7F)
    b = es.EcoGroup(1, "04:00", "06:00", 50, True, 0x02)
    assert es.eco_conflicts([a, b], b)
    c = es.EcoGroup(1, "05:00", "06:00", 50, True, 0x02)
    assert not es.eco_conflicts([a, c], c)


# --- Sequenze di scrittura --------------------------------------------------


def test_work_mode_general_sequence_like_solargo():
    plan = es.plan_work_mode(3, 0)
    codes = [(s.request.ctrl, s.request.func) for s in plan.steps]
    assert codes == [(3, 0x2C), (3, 0x2D), (2, 0x39), (3, 0x59), (3, 0x36)]
    # la libreria goodwe (test commentato) per la modalità 1 usa 'aa55c07f03590101029c'
    assert plan.steps[3].request.to_bytes().hex() == "aa55c07f03590100029b"


def test_work_mode_offgrid_not_allowed():
    with pytest.raises(ValueError):
        es.plan_work_mode(0, 1)


def test_soc_protect_payload():
    on = es.plan_soc_protect({"soc_protect_raw": 1}, True).steps[0].request
    off = es.plan_soc_protect({"soc_protect_raw": 0}, False).steps[0].request
    assert (on.func, on.payload) == (0x3B, bytes([0, 0, 0]))
    assert (off.func, off.payload) == (0x3B, bytes([0, 1, 0]))


def test_dod_warning_when_soc_protection_off():
    plan = es.plan_ongrid_dod({"soc_protect_raw": 1, "min_soc_raw": 20}, 70)
    assert "protezione SoC" in plan.warning


# --- Parametri di protezione rete: mai scrivibili -------------------------


@pytest.mark.parametrize(
    "req",
    [
        p.write_registers(0x0579, (2530).to_bytes(2, "big")),  # soglie tensione EMJ
        p.write_registers(0x0001, (60).to_bytes(2, "big")),  # tempo di riconnessione
        p.write_registers(0x7150, (1).to_bytes(2, "big")),  # anti-islanding attivo
        p.write_registers(0x0700, bytes(4)),  # oltre il registro ammesso
        p.Aa55Request(p.SRC_REGISTER, 0x02, 0x3A, bytes([3, 0])),  # paese di sicurezza (0x023A)
        p.classic_command(0x01, bytes([0])),  # paese di sicurezza (0x0301)
        p.classic_command(0x3C, bytes([0])),  # sicurezza AU
        p.Aa55Request(p.SRC_REGISTER, 0x03, 0x59, bytes([0])),  # comando giusto, sorgente sbagliata
    ],
)
def test_protection_writes_are_blocked(req):
    with pytest.raises(WriteNotAllowed):
        ensure_write_allowed(req)


def test_every_plan_passes_allowlist():
    s = es.parse_settings(p.parse_aa55(sample("GW5048D-ES_settings_data.hex")).data)
    s["soc_protect_raw"] = 0
    g = es.EcoGroup(0, "01:00", "05:00", -50, True, 0x7F)
    plans = [
        es.plan_work_mode(0, 0), es.plan_work_mode(0, 2), es.plan_work_mode(0, 3),
        es.plan_eco_group([g], g), es.plan_eco_switch([g], 0, False),
        es.plan_soc_protect(s, True), es.plan_ongrid_dod(s, 80), es.plan_offgrid_dod(20, 80),
        es.plan_export_limit(s, 3000),
    ]
    for plan in plans:
        for step in plan.steps:
            ensure_write_allowed(step.request)
