"""Framing tests: frames built by the portal and frames recorded from real inverters."""

from pathlib import Path

import pytest

from app import es
from app import protocol as p
from app.transport import WriteNotAllowed, ensure_write_allowed

SAMPLES = Path(__file__).parent / "samples"


def sample(name: str) -> bytes:
    return bytes.fromhex(SAMPLES.joinpath(name).read_text().strip())


# --- Frames identical to the goodwe library ones (same source C0) ----------


def test_read_settings_frame_matches_goodwe_library():
    # goodwe library, tests/test_es.py: 'aa55c07f0109000248'
    assert p.read_settings().to_bytes().hex() == "aa55c07f0109000248"


def test_export_limit_frame_matches_goodwe_library():
    # goodwe: set_grid_export_limit(100) -> 'aa55c07f033502006402dc'
    plan = es.plan_export_limit({"export_limit_w": 0}, 100)
    assert plan.steps[0].request.to_bytes().hex() == "aa55c07f033502006402dc"


def test_device_info_frame():
    assert p.read_device_info().to_bytes().hex() == "aa55c07f0102000241"  # 0xAA+0x55+0xC0+0x7F+0x01+0x02 = 0x241


# --- SolarGo-style frames (source B0 for registers) ------------------------


def test_ongrid_dod_uses_b0_source_unlike_home_assistant():
    settings = {"soc_protect_raw": 0, "min_soc_raw": 20}
    frame = es.plan_ongrid_dod(settings, 80).steps[0].request.to_bytes()
    # Home Assistant / goodwe sends: aa55 c07f 0239 05 0560 01 0014 02f8
    ha = bytes.fromhex("aa55c07f023905056001001402f8")
    assert frame[:2] == ha[:2]
    assert frame[2] == 0xB0 and ha[2] == 0xC0  # the only difference: the source address
    assert frame[3:-2] == ha[3:-2]
    assert frame.hex() == "aa55b07f023905056001001402e8"


def test_solargo_register_prefixes():
    # prefixes taken from SolarGo's DataCollectUtil.build105SettingPackage / build105ReadingPackage
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
    # Modbus CRC of "01 03 00 00 00 0A" = C5CD (low byte first)
    assert p.crc16_modbus(bytes.fromhex("01030000000a")) == 0xCDC5
    req = p.ModbusReadRequest(0xB158, 17)
    assert req.to_bytes()[:6] == bytes.fromhex("f703b1580011")


# --- Parsing frames recorded from real inverters ---------------------------


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
    # same offsets as the goodwe library
    assert s["capacity_ah"] == int.from_bytes(resp.data[22:24], "big")
    assert es.ongrid_dod(s) == 100 - int.from_bytes(resp.data[32:34], "big")
    assert s["work_mode"] in es.WORK_MODES
    fields = es.settings_fields(s)
    assert {f.key for f in fields["battery"]} >= {"soc_protect", "ongrid_dod"}


def test_parse_recorded_eco_register_reply():
    # reply recorded in the goodwe library for a read of register 1793 (0x0701)
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


# --- Eco groups ----------------------------------------------------------------


def test_eco_group_roundtrip():
    g = es.EcoGroup(2, "13:30", "16:45", -75, True, 0b0111110)
    data = bytes(16) + g.to_bytes() + bytes(8)
    parsed = es.parse_eco_groups(data)[2]
    assert parsed == g
    assert parsed.mode == "charge"
    assert parsed.days_list == ["Mon", "Tue", "Wed", "Thu", "Fri"]


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


# --- Write sequences -----------------------------------------------------------


def test_work_mode_general_sequence_like_solargo():
    plan = es.plan_work_mode(3, 0)
    codes = [(s.request.ctrl, s.request.func) for s in plan.steps]
    assert codes == [(3, 0x2C), (3, 0x2D), (2, 0x39), (3, 0x59), (3, 0x36)]
    # the goodwe library (commented-out test) uses 'aa55c07f03590101029c' for mode 1
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
    assert "SoC protection" in plan.warning


# --- Grid protection parameters: never writable -----------------------------


@pytest.mark.parametrize(
    "req",
    [
        p.write_registers(0x0579, (2530).to_bytes(2, "big")),  # EMJ voltage thresholds
        p.write_registers(0x0001, (60).to_bytes(2, "big")),  # reconnection time
        p.write_registers(0x7150, (1).to_bytes(2, "big")),  # active anti-islanding
        p.write_registers(0x0700, bytes(4)),  # beyond the allowed register
        p.Aa55Request(p.SRC_REGISTER, 0x02, 0x3A, bytes([3, 0])),  # safety country (0x023A)
        p.classic_command(0x01, bytes([0])),  # safety country (0x0301)
        p.classic_command(0x3C, bytes([0])),  # AU safety
        p.Aa55Request(p.SRC_REGISTER, 0x03, 0x59, bytes([0])),  # right command, wrong source
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


# --- Timeline editor: several eco groups in one plan -------------------------


def _groups():
    return [
        es.EcoGroup(0, "01:00", "05:00", -50, True, 0x7F),
        es.EcoGroup(1, "17:00", "21:00", 100, False, 0x3E),
        es.EcoGroup(2, "00:00", "00:00", 0, False, 0),
        es.EcoGroup(3, "00:00", "00:00", 0, False, 0),
    ]


def test_eco_schedule_writes_only_changed_groups():
    cur = _groups()
    moved = es.EcoGroup(0, "02:00", "06:00", -50, True, 0x7F)
    added = es.EcoGroup(2, "12:00", "14:00", 30, True, 0x41)
    plan = es.plan_eco_schedule(cur, [moved, added])
    assert plan.steps[0].request.to_bytes().hex() == "aa55c07f03590103029e"  # eco mode once
    regs = [int.from_bytes(s.request.payload[0:2], "big") for s in plan.steps[1:]]
    assert regs == [0x0701, 0x0709]
    assert plan.steps[2].request.payload[3:] == added.to_bytes()


def test_eco_schedule_remove_writes_disabled_group():
    cur = _groups()
    removed = es.EcoGroup(0, "01:00", "05:00", -50, False, 0x7F)
    plan = es.plan_eco_schedule(cur, [removed])
    assert plan.steps[1].request.payload[3:] == bytes([1, 0, 5, 0, 0xFF, 0xCE, 0x00, 0x7F])


def test_eco_schedule_rejects_conflicts_and_noop():
    cur = _groups()
    with pytest.raises(ValueError, match="opposite"):
        es.plan_eco_schedule(cur, [es.EcoGroup(2, "04:00", "06:00", 50, True, 0x01)])
    with pytest.raises(ValueError, match="no changes"):
        es.plan_eco_schedule(cur, [cur[0]])
    with pytest.raises(ValueError):
        es.plan_eco_schedule(cur, [es.EcoGroup(2, "22:00", "06:00", 50, True, 0x01)])
    plan = es.plan_eco_schedule(cur, [es.EcoGroup(2, "04:00", "06:00", -50, True, 0x01)])  # same mode+power: ok
    for step in plan.steps:
        ensure_write_allowed(step.request)


# --- Advanced settings (SolarGo AdvancedSettingNewActivity) -------------------


def _settings():
    return es.parse_settings(p.parse_aa55(sample("GW5048D-ES_settings_data.hex")).data)


def test_advanced_switch_frames_like_solargo():
    s = _settings()
    def payload(plan):
        req = plan.steps[0].request
        assert req.src == p.SRC_CLASSIC and req.ctrl == 0x03
        ensure_write_allowed(req)
        return req.func, req.payload
    assert payload(es.plan_export_limit_enabled(s, True)) == (0x53, bytes([1]))
    assert payload(es.plan_export_limit_enabled(s, False)) == (0x53, bytes([0]))
    assert payload(es.plan_shadow_scan(s, True)) == (0x28, bytes([0x80, 0x80]))
    assert payload(es.plan_shadow_scan(s, False)) == (0x28, bytes([0x80, 0x00]))
    assert payload(es.plan_backup_supply(s, True)) == (0x27, bytes([0x00, 0x30]))
    assert payload(es.plan_backup_supply(s, False)) == (0x27, bytes([0x00, 0x10]))
    assert payload(es.plan_battery_activated(s, True)) == (0x37, bytes([0x00, 0x0F]))
    assert payload(es.plan_detection_mode(s, 2)) == (0x5B, bytes([2]))
    assert payload(es.plan_overload_reset()) == (0x5E, bytes([0x0F]))


def test_power_factor_display_like_solargo():
    s = _settings()
    s["power_factor_raw"] = 90
    assert es.power_factor(s) == 0.9
    s["power_factor_raw"] = 10  # SolarGo: 0..20 means negative
    assert es.power_factor(s) == -0.9


def test_power_factor_read_only_and_not_set():
    fields = es.settings_fields(_settings(), offgrid_dod_raw=20, dred=1)
    by_key = {f.key: f for group in fields.values() for f in group}
    for key in ("power_factor", "charge_v", "capacity_ah"):
        assert by_key[key].control is None
    assert by_key["dred"].value == "on" and by_key["dred"].control["type"] == "switch"
    assert all(f.help for f in by_key.values())
    with pytest.raises(WriteNotAllowed):
        ensure_write_allowed(p.classic_command(0x1F, bytes([90])))  # power factor command stays blocked
    s = _settings()
    s["power_factor_raw"] = 0xFFFF
    pf = {f.key: f for group in es.settings_fields(s).values() for f in group}["power_factor"]
    assert pf.value == "not set"


def test_dred_plan():
    on = es.plan_dred(0, True)
    off = es.plan_dred(1, False)
    assert isinstance(on.steps[0].request, p.ModbusWriteRequest)
    assert on.steps[0].request.to_bytes().hex().startswith("f706b7bc0001")
    assert off.steps[0].request.to_bytes().hex().startswith("f706b7bc0000")
    assert on.warning and not off.warning
    ensure_write_allowed(on.steps[0].request)
    ensure_write_allowed(off.steps[0].request)


def test_safety_decode_matches_app():
    from app import safety
    # raw block for 0xB158.. : OV1=2645 (264.5V), OV1 time=9 (0.18s), UV1=345 (34.5V)
    data = b"".join(v.to_bytes(2, "big") for v in [2645, 9, 345, 9] + [0] * 13)
    groups = safety.fields({"safety_protect": data})["groups"]
    volt = next(g for g in groups if g["key"] == "voltage")["fields"]
    by = {f["key"]: f for f in volt}
    assert by["safety_B158"]["value"] == 264.5 and by["safety_B158"]["unit"] == "V"
    assert by["safety_B159"]["value"] == 0.18 and by["safety_B159"]["edit"] is None  # trip time read-only
    assert by["safety_B15A"]["value"] == 34.5
    assert all(f["help"] and f["protected"] for f in volt)


def test_safety_write_frame_and_range():
    from app import safety
    plan = safety.plan_safety(0xB158, 253)
    assert plan.steps[0].request.to_bytes().hex().startswith("f706b15809e2")  # 2530 = 0x09E2
    with pytest.raises(ValueError):
        safety.plan_safety(0xB158, 400)  # out of range [60,300]
    with pytest.raises(ValueError):
        safety.plan_safety(0xB159, 1)  # trip time: read-only
    # signed per-mille round-trips (e.g. P(U) V4 active, default -800)
    plan = safety.plan_safety(0xB1A9, -800)
    assert plan.steps[0].request.value == (-800 & 0xFFFF)
