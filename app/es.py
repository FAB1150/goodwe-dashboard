"""Catalogo delle impostazioni della famiglia GoodWe ES/EM (piattaforma 105), ricavato da SolarGo.

Ogni lettura e ogni scrittura qui riproduce i frame che SolarGo invia per un inverter
EMU con firmware ARM 7..13 (quello di Fabio: GW5048-EM, 0909A / 410-04000-11).
Vedi docs/PROTOCOLLO.md per i dettagli e i riferimenti al codice di SolarGo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import protocol as p

WORK_MODES = {
    0: "Generale (autoconsumo)",
    1: "Fuori rete (off-grid)",
    2: "Backup",
    3: "Eco (fasce orarie)",
}
# Modalità che il portale permette di impostare (off-grid escluso di proposito)
WRITABLE_WORK_MODES = (0, 2, 3)

SAFETY_COUNTRIES = {0: "IT-CEI 0-21", 67: "IT-CEI 0-16"}

WEEKDAYS = ["Dom", "Lun", "Mar", "Mer", "Gio", "Ven", "Sab"]  # bit 0 = domenica

ECO_BASE = 0x0701
ECO_GROUPS = 4
ECO_REGS_PER_GROUP = 4
REG_ONGRID_DOD = 0x0560
REG_OFFGRID_DOD = 0x05A1
REG_CLEAR_BATTERY_MODE = 0x0700


# ---------------------------------------------------------------------------
# Utilità di decodifica
# ---------------------------------------------------------------------------


def _ascii(b: bytes) -> str:
    return b.replace(b"\x00", b" ").decode("ascii", errors="replace").strip()


def _u16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off : off + 2], "big")


def _s16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off : off + 2], "big", signed=True)


@dataclass
class Field:
    """Un valore mostrato nella pagina."""

    key: str
    label: str
    value: Any
    unit: str = ""
    raw: Any = None
    note: str = ""
    editable: bool = False
    protected: bool = False  # parametro di protezione rete: solo lettura, sempre

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# 0x0102 informazioni dispositivo
# ---------------------------------------------------------------------------


@dataclass
class DeviceInfo:
    firmware: str
    model: str
    manufacturer: str
    serial: str
    software: str
    safety_country_code: int
    arm_string: str
    dsp_version: int | None
    arm_version: int | None

    @property
    def safety_country(self) -> str:
        return SAFETY_COUNTRIES.get(self.safety_country_code, f"codice {self.safety_country_code}")

    @property
    def family_ok(self) -> bool:
        return any(t in self.serial for t in ("EMU", "ESU", "ESA", "BPS"))


def parse_device_info(data: bytes) -> DeviceInfo:
    if len(data) < 64:
        raise p.ProtocolError(f"risposta 0x0182 troppo corta ({len(data)} byte)")
    firmware = _ascii(data[0:5])
    arm_string = _ascii(data[64:76]) if len(data) >= 76 else ""
    dsp = arm = None
    try:
        dsp = int(firmware[0:2])
    except ValueError:
        pass
    # Come SolarGo: prima il 5° carattere del firmware (base 36: 'A' = 10),
    # poi, se presente, il terzo campo della stringa "410-04000-11".
    try:
        arm = int(firmware[4], 36)
    except (ValueError, IndexError):
        pass
    parts = arm_string.split("-")
    if len(parts) >= 3:
        try:
            arm = int(parts[2])
        except ValueError:
            pass
    return DeviceInfo(
        firmware=firmware,
        model=_ascii(data[5:15]),
        manufacturer=_ascii(data[15:31]),
        serial=_ascii(data[31:47]),
        software=_ascii(data[51:63]),
        safety_country_code=data[63],
        arm_string=arm_string,
        dsp_version=dsp,
        arm_version=arm,
    )


def device_info_fields(info: DeviceInfo) -> list[Field]:
    return [
        Field("model", "Modello", info.model),
        Field("serial", "Numero di serie", info.serial),
        Field("firmware", "Firmware", info.firmware),
        Field("software", "Versione software", info.software),
        Field("arm_string", "Versione ARM (estesa)", info.arm_string or "—"),
        Field("dsp_version", "Versione DSP (deriva dal firmware)", info.dsp_version),
        Field("arm_version", "Versione ARM usata da SolarGo", info.arm_version),
        Field(
            "safety_country",
            "Paese di sicurezza",
            info.safety_country,
            raw=info.safety_country_code,
            protected=True,
            note="Parametro di interfaccia rete: solo lettura. Byte 63 della risposta 0x0182, come in SolarGo; "
            "su alcuni firmware questo byte non contiene il paese (es. 32 = spazio).",
        ),
    ]


# ---------------------------------------------------------------------------
# 0x0109 blocco impostazioni
# ---------------------------------------------------------------------------


def _hhmm(h: int, m: int) -> str:
    # SolarGo: un byte 0x30 ('0' ASCII) nell'ora significa "non impostato"
    if h == 0x30:
        h, m = 0, 0
    return f"{h:02d}:{m:02d}"


def parse_settings(data: bytes) -> dict[str, Any]:
    if len(data) < 64:
        raise p.ProtocolError(f"risposta 0x0189 troppo corta ({len(data)} byte)")
    s: dict[str, Any] = {}
    s["charge_window"] = f"{_hhmm(data[0], data[1])}-{_hhmm(data[2], data[3])}"
    s["charge_power_limit"] = _u16(data, 4)
    s["discharge_window"] = f"{_hhmm(data[6], data[7])}-{_hhmm(data[8], data[9])}"
    s["discharge_power_limit"] = _u16(data, 10)
    s["backup_supply"] = data[13]
    s["offgrid_charge"] = data[15]
    s["shadow_scan"] = data[17]
    s["export_limit_enabled_raw"] = _u16(data, 18)
    s["capacity_ah"] = _u16(data, 22)
    s["charge_v_raw"] = _u16(data, 24)
    s["charge_i_raw"] = _u16(data, 26)
    s["discharge_i_raw"] = _u16(data, 28)
    s["discharge_v_raw"] = _u16(data, 30)
    s["min_soc_raw"] = _u16(data, 32)  # = 100 - DoD in rete
    s["battery_activated"] = _u16(data, 34)
    s["power_factor_raw"] = _u16(data, 42)
    s["float_v_raw"] = _u16(data, 44)
    s["float_i_raw"] = _u16(data, 46)
    s["float_time"] = _u16(data, 48)
    s["battery_model"] = _u16(data, 50)
    s["export_limit_w"] = _s16(data, 52)
    s["soc_protect_raw"] = _u16(data, 56)
    s["avg_v_raw"] = _u16(data, 60)
    s["avg_time"] = _u16(data, 62)
    s["work_mode"] = _u16(data, 66) if len(data) >= 68 else None
    s["grid_quality_check"] = _u16(data, 68) if len(data) >= 70 else None
    s["_len"] = len(data)
    return s


def soc_protect_on(settings: dict) -> bool:
    # SolarGo: valore 1 = protezione SoC disattivata, altrimenti attiva
    return settings["soc_protect_raw"] != 1


def ongrid_dod(settings: dict) -> int:
    return 100 - settings["min_soc_raw"]


def settings_fields(s: dict) -> dict[str, list[Field]]:
    wm = s.get("work_mode")
    pf_raw = s["power_factor_raw"]
    pf = pf_raw - 100 if 0 <= pf_raw <= 20 else pf_raw  # trasformazione di SolarGo
    discharge_a = min(s["discharge_i_raw"] / 0.128 / 10, 120.0)
    charge_a = s["charge_i_raw"] / 0.266 / 10
    return {
        "mode": [
            Field(
                "work_mode",
                "Modalità di lavoro",
                WORK_MODES.get(wm, f"sconosciuta ({wm})") if wm is not None else "—",
                raw=wm,
                editable=True,
            ),
            Field("charge_window", "Finestra di carica (comando legacy 0x032C)", s["charge_window"],
                  note=f"limite {s['charge_power_limit']} %"),
            Field("discharge_window", "Finestra di scarica (comando legacy 0x032D)", s["discharge_window"],
                  note=f"limite {s['discharge_power_limit']} %"),
        ],
        "battery": [
            Field("soc_protect", "Protezione SoC", "attiva" if soc_protect_on(s) else "disattivata",
                  raw=s["soc_protect_raw"], editable=True,
                  note="In SolarGo la DoD in rete si può modificare solo con la protezione SoC attiva."),
            Field("ongrid_dod", "Profondità di scarica (DoD) in rete", ongrid_dod(s), "%",
                  raw=s["min_soc_raw"], editable=True,
                  note=f"SoC minimo in rete = {s['min_soc_raw']} %"),
            Field("capacity_ah", "Capacità batteria", s["capacity_ah"], "Ah"),
            Field("charge_v", "Tensione di carica", s["charge_v_raw"] / 10, "V", raw=s["charge_v_raw"]),
            Field("charge_i", "Corrente di carica (conversione SolarGo)", round(charge_a, 1), "A",
                  raw=s["charge_i_raw"]),
            Field("discharge_v", "Tensione di scarica", s["discharge_v_raw"] / 10, "V", raw=s["discharge_v_raw"]),
            Field("discharge_i", "Corrente di scarica (conversione SolarGo)", round(discharge_a, 1), "A",
                  raw=s["discharge_i_raw"]),
            Field("battery_model", "Modello batteria (255 = personalizzata)", s["battery_model"]),
            Field("battery_activated", "Attivazione batteria", s["battery_activated"]),
            Field("offgrid_charge", "Carica da rete in off-grid", s["offgrid_charge"]),
        ],
        "grid": [
            Field("export_limit_w", "Limite di immissione in rete", s["export_limit_w"], "W", editable=True),
            Field("export_limit_enabled", "Limitazione immissione (stato letto)",
                  "attiva" if s["export_limit_enabled_raw"] == 1 else "disattivata",
                  raw=s["export_limit_enabled_raw"],
                  note="L'interruttore non è ancora modificabile dal portale (polarità da verificare)."),
            Field("backup_supply", "Uscita backup (EPS)", "attiva" if s["backup_supply"] else "disattivata",
                  raw=s["backup_supply"]),
            Field("power_factor", "Fattore di potenza", pf, raw=pf_raw),
            Field("shadow_scan", "Scansione ombre (MPPT)", "attiva" if s["shadow_scan"] else "disattivata",
                  raw=s["shadow_scan"]),
            Field("grid_quality_check", "Controllo qualità rete", s.get("grid_quality_check"),
                  raw=s.get("grid_quality_check")),
        ],
    }


# ---------------------------------------------------------------------------
# Fasce eco (registri 0x0701-0x0710, 4 gruppi da 8 byte)
# ---------------------------------------------------------------------------


@dataclass
class EcoGroup:
    index: int  # 0..3
    start: str  # "HH:MM"
    end: str
    power: int  # % con segno: negativo = carica, positivo = scarica
    enabled: bool
    days: int  # bitmask, bit0 = domenica

    @property
    def mode(self) -> str:
        return "carica" if self.power < 0 else "scarica"

    @property
    def days_list(self) -> list[str]:
        return [WEEKDAYS[i] for i in range(7) if self.days & (1 << i)]

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "power": self.power,
            "mode": self.mode,
            "power_abs": abs(self.power),
            "enabled": self.enabled,
            "days": self.days,
            "days_list": self.days_list,
        }

    def to_bytes(self, enabled: bool | None = None) -> bytes:
        sh, sm = (int(x) for x in self.start.split(":"))
        eh, em = (int(x) for x in self.end.split(":"))
        en = self.enabled if enabled is None else enabled
        return bytes([sh, sm, eh, em]) + self.power.to_bytes(2, "big", signed=True) + bytes(
            [0xFF if en else 0x00, self.days & 0x7F]
        )


def parse_eco_groups(data: bytes) -> list[EcoGroup]:
    if len(data) != 32:
        raise p.ProtocolError(f"fasce eco: attesi 32 byte, ricevuti {len(data)}")
    groups = []
    for i in range(ECO_GROUPS):
        b = data[i * 8 : i * 8 + 8]
        groups.append(
            EcoGroup(
                index=i,
                start=f"{b[0]:02d}:{b[1]:02d}",
                end=f"{b[2]:02d}:{b[3]:02d}",
                power=int.from_bytes(b[4:6], "big", signed=True),
                enabled=b[6] == 0xFF,
                days=b[7],
            )
        )
    return groups


def _parse_hhmm(t: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in t.split(":"))
    except ValueError as e:
        raise ValueError(f"orario non valido: {t!r}") from e
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"orario non valido: {t!r}")
    return h, m


def validate_eco_group(g: EcoGroup) -> None:
    if not 0 <= g.index < ECO_GROUPS:
        raise ValueError("indice gruppo non valido")
    s = _parse_hhmm(g.start)
    e = _parse_hhmm(g.end)
    if s >= e:
        # Stesso controllo di SolarGo (BatteryModeActivity.compareTime)
        raise ValueError("l'ora di inizio deve precedere l'ora di fine (le fasce non attraversano la mezzanotte)")
    if not -100 <= g.power <= 100:
        raise ValueError("potenza fuori intervallo [-100, 100] %")
    if not 0 <= g.days <= 0x7F:
        raise ValueError("giorni non validi")


def eco_conflicts(groups: list[EcoGroup], candidate: EcoGroup) -> list[str]:
    """Controlli di coerenza che fa SolarGo prima di salvare/attivare una fascia."""
    problems = []
    cs, ce = _parse_hhmm(candidate.start), _parse_hhmm(candidate.end)
    for g in groups:
        if g.index == candidate.index or not g.enabled:
            continue
        gs, ge = _parse_hhmm(g.start), _parse_hhmm(g.end)
        overlap = cs < ge and gs < ce
        if not overlap or not (g.days & candidate.days):
            continue
        same_kind = (g.power < 0) == (candidate.power < 0)
        if not same_kind:
            problems.append(f"si sovrappone alla fascia {g.index + 1} con modalità opposta ({g.mode})")
        elif g.power != candidate.power:
            problems.append(f"si sovrappone alla fascia {g.index + 1} con una potenza diversa ({g.power} %)")
    return problems


# ---------------------------------------------------------------------------
# Parametri di protezione rete (solo lettura, Modbus 0xB158.. e 0xB169..)
# ---------------------------------------------------------------------------

# (registro, etichetta, unità, divisore)
PROTECTION_BLOCK = (0xB158, 17)
PROTECTION_FIELDS = [
    (0xB158, "Sovratensione 1° livello", "V", 10),
    (0xB159, "Sovratensione 1° livello – tempo", "periodi", 1),
    (0xB15A, "Sottotensione 1° livello", "V", 10),
    (0xB15B, "Sottotensione 1° livello – tempo", "periodi", 1),
    (0xB15C, "Sovratensione 2° livello", "V", 10),
    (0xB15D, "Sovratensione 2° livello – tempo", "periodi", 1),
    (0xB15E, "Sottotensione 2° livello", "V", 10),
    (0xB15F, "Sottotensione 2° livello – tempo", "periodi", 1),
    (0xB160, "Sovratensione media 10 minuti", "V", 10),
    (0xB161, "Sovrafrequenza 1° livello", "Hz", 100),
    (0xB162, "Sovrafrequenza 1° livello – tempo", "periodi", 1),
    (0xB163, "Sottofrequenza 1° livello", "Hz", 100),
    (0xB164, "Sottofrequenza 1° livello – tempo", "periodi", 1),
    (0xB165, "Sovrafrequenza 2° livello", "Hz", 100),
    (0xB166, "Sovrafrequenza 2° livello – tempo", "periodi", 1),
    (0xB167, "Sottofrequenza 2° livello", "Hz", 100),
    (0xB168, "Sottofrequenza 2° livello – tempo", "periodi", 1),
]
CONNECT_BLOCK = (0xB169, 16)
CONNECT_FIELDS = [
    (0xB169, "Tensione di connessione – max", "V", 10),
    (0xB16A, "Tensione di connessione – min", "V", 10),
    (0xB16B, "Frequenza di connessione – max", "Hz", 100),
    (0xB16C, "Frequenza di connessione – min", "Hz", 100),
    (0xB16D, "Attesa prima del parallelo", "s", 1),
    (0xB16E, "Riconnessione dopo guasto: tensione max", "V", 10),
    (0xB16F, "Riconnessione dopo guasto: tensione min", "V", 10),
    (0xB170, "Riconnessione dopo guasto: frequenza max", "Hz", 100),
    (0xB171, "Riconnessione dopo guasto: frequenza min", "Hz", 100),
    (0xB172, "Riconnessione dopo guasto: attesa", "s", 1),
    (0xB173, "Riconnessione dopo guasto di frequenza: attesa", "s", 1),
    (0xB174, "Rampa di potenza alla connessione", "", 1),
    (0xB175, "Rampa di potenza dopo guasto", "", 1),
    (0xB178, "Rampa di connessione abilitata", "", 1),
]


def parse_register_block(start: int, data: bytes, spec) -> list[Field]:  # noqa: ANN001
    out = []
    for reg, label, unit, div in spec:
        off = (reg - start) * 2
        if off + 2 > len(data):
            continue
        raw = _u16(data, off)
        value = raw / div if div != 1 else raw
        out.append(
            Field(f"reg_{reg:04X}", label, value, unit, raw=raw, protected=True,
                  note=f"registro 0x{reg:04X} — solo lettura (CEI 0-21)")
        )
    return out


# ---------------------------------------------------------------------------
# Letture: la sequenza che il portale esegue all'apertura / aggiornamento
# ---------------------------------------------------------------------------


@dataclass
class ReadStep:
    key: str
    description: str
    request: p.Request


def page_read_steps() -> list[ReadStep]:
    return [
        ReadStep("device_info", "Informazioni dispositivo (0x0102)", p.read_device_info()),
        ReadStep("settings", "Impostazioni (0x0109)", p.read_settings()),
        ReadStep("eco", "Fasce eco (registri 0x0701-0x0710)", p.read_registers(ECO_BASE, ECO_GROUPS * ECO_REGS_PER_GROUP)),
        ReadStep("offgrid_dod", "DoD fuori rete (registro 0x05A1)", p.read_registers(REG_OFFGRID_DOD, 1)),
    ]


def protection_read_steps() -> list[ReadStep]:
    return [
        ReadStep("protection", "Parametri di protezione (Modbus 0xB158, 17 registri)",
                 p.ModbusReadRequest(*PROTECTION_BLOCK)),
        ReadStep("connect", "Parametri di connessione (Modbus 0xB169, 16 registri)",
                 p.ModbusReadRequest(*CONNECT_BLOCK)),
    ]


# ---------------------------------------------------------------------------
# Scritture: piani di comandi (riproducono le sequenze di SolarGo)
# ---------------------------------------------------------------------------


@dataclass
class WriteStep:
    description: str
    request: p.Request
    must_ack: bool = True  # SolarGo considera riuscito solo l'ACK (0x06)


@dataclass
class WritePlan:
    setting: str
    label: str
    before: Any
    after: Any
    steps: list[WriteStep]
    verify: list[str] = field(default_factory=list)  # chiavi di lettura da ripetere dopo la scrittura
    warning: str = ""


def _work_mode_cmd(mode: int) -> WriteStep:
    return WriteStep(f"modalità di lavoro = {mode} ({WORK_MODES[mode]})", p.classic_command(0x59, bytes([mode])))


def _offgrid_flag(v: int) -> WriteStep:
    return WriteStep(f"flag lavoro fuori rete = {v}", p.classic_command(0x36, bytes([v])))


def _legacy_window(func: int, start: str, end: str, limit: int, what: str) -> WriteStep:
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    return WriteStep(
        f"finestra di {what} {start}-{end}, limite {limit} %",
        p.classic_command(func, bytes([sh, sm, eh, em, limit])),
    )


def _clear_battery_mode() -> WriteStep:
    return WriteStep(
        "azzeramento parametri modalità batteria (registro 0x0700 = 1)",
        p.write_registers(REG_CLEAR_BATTERY_MODE, (1).to_bytes(2, "big")),
    )


def plan_work_mode(current: int | None, mode: int) -> WritePlan:
    """Sequenze di ChooseWorkModeActivity per ES/EM con ARM >= 7 e senza "nuova modalità eco"."""
    if mode not in WRITABLE_WORK_MODES:
        raise ValueError("modalità non consentita dal portale")
    if mode == 0:
        steps = [
            _legacy_window(0x2C, "00:00", "00:00", 0, "carica"),
            _legacy_window(0x2D, "00:00", "00:00", 0, "scarica"),
            _clear_battery_mode(),
            _work_mode_cmd(0),
            _offgrid_flag(0),
        ]
        warning = ""
    elif mode == 2:
        steps = [
            _clear_battery_mode(),
            _offgrid_flag(0),
            _legacy_window(0x2C, "00:00", "23:59", 10, "carica"),
            _work_mode_cmd(2),
        ]
        warning = "Come in SolarGo, la modalità backup azzera i parametri delle modalità batteria."
    else:  # eco
        steps = [_work_mode_cmd(3)]
        warning = "In modalità eco l'inverter segue le fasce orarie attive."
    return WritePlan(
        setting="work_mode",
        label="Modalità di lavoro",
        before=WORK_MODES.get(current, current) if current is not None else None,
        after=WORK_MODES[mode],
        steps=steps,
        verify=["settings"],
        warning=warning,
    )


def plan_eco_group(groups: list[EcoGroup], new: EcoGroup) -> WritePlan:
    """BatteryModeActivity: modalità eco (0x0359=3) + scrittura dei 4 registri del gruppo."""
    validate_eco_group(new)
    old = next((g for g in groups if g.index == new.index), None)
    reg = ECO_BASE + new.index * ECO_REGS_PER_GROUP
    return WritePlan(
        setting=f"eco_group_{new.index + 1}",
        label=f"Fascia eco {new.index + 1}",
        before=old.as_dict() if old else None,
        after=new.as_dict(),
        steps=[
            _work_mode_cmd(3),
            WriteStep(f"fascia {new.index + 1}: registri 0x{reg:04X}-0x{reg + 3:04X}", p.write_registers(reg, new.to_bytes())),
        ],
        verify=["eco", "settings"],
        warning="; ".join(eco_conflicts(groups, new)),
    )


def plan_eco_switch(groups: list[EcoGroup], index: int, enabled: bool) -> WritePlan:
    """BatteryModeListActivity: modalità eco + registro "switch" del gruppo (byte attivo + giorni)."""
    g = next(g for g in groups if g.index == index)
    reg = ECO_BASE + index * ECO_REGS_PER_GROUP + 3
    candidate = EcoGroup(g.index, g.start, g.end, g.power, enabled, g.days)
    return WritePlan(
        setting=f"eco_group_{index + 1}_switch",
        label=f"Fascia eco {index + 1}: {'attiva' if enabled else 'disattiva'}",
        before="attiva" if g.enabled else "disattiva",
        after="attiva" if enabled else "disattiva",
        steps=[
            _work_mode_cmd(3),
            WriteStep(
                f"fascia {index + 1}: registro 0x{reg:04X} = {'FF' if enabled else '00'} + giorni",
                p.write_registers(reg, bytes([0xFF if enabled else 0x00, g.days & 0x7F])),
            ),
        ],
        verify=["eco", "settings"],
        warning="; ".join(eco_conflicts(groups, candidate)) if enabled else "",
    )


def plan_soc_protect(settings: dict, enabled: bool) -> WritePlan:
    """Comando 0x033B: 00 <0 = attiva, 1 = disattiva> 00 (sorgente C0)."""
    return WritePlan(
        setting="soc_protect",
        label="Protezione SoC",
        before="attiva" if soc_protect_on(settings) else "disattivata",
        after="attiva" if enabled else "disattivata",
        steps=[WriteStep("protezione SoC", p.classic_command(0x3B, bytes([0, 0 if enabled else 1, 0])))],
        verify=["settings"],
    )


def plan_ongrid_dod(settings: dict, dod: int) -> WritePlan:
    """BatteryFunction105Activity: registro 0x0560 = 100 - DoD, sorgente B0."""
    if not 0 <= dod <= 95:
        raise ValueError("DoD fuori intervallo [0, 95] %")
    warning = ""
    if not soc_protect_on(settings):
        warning = (
            "La protezione SoC è disattivata: SolarGo in questo caso non permette di modificare la DoD "
            "(e la reinvia invariata). Attiva prima la protezione SoC."
        )
    return WritePlan(
        setting="ongrid_dod",
        label="DoD in rete",
        before=ongrid_dod(settings),
        after=dod,
        steps=[WriteStep(f"registro 0x0560 = {100 - dod} (100 − DoD)", p.write_registers(REG_ONGRID_DOD, (100 - dod).to_bytes(2, "big")))],
        verify=["settings"],
        warning=warning,
    )


def plan_offgrid_dod(current: int | None, dod: int) -> WritePlan:
    """Registro 0x05A1 = 100 - DoD, sorgente B0 (BatteryFunction105Activity)."""
    if not 0 <= dod <= 95:
        raise ValueError("DoD fuori intervallo [0, 95] %")
    return WritePlan(
        setting="offgrid_dod",
        label="DoD fuori rete",
        before=current,
        after=dod,
        steps=[WriteStep(f"registro 0x05A1 = {100 - dod} (100 − DoD)", p.write_registers(REG_OFFGRID_DOD, (100 - dod).to_bytes(2, "big")))],
        verify=["offgrid_dod"],
    )


def plan_export_limit(settings: dict, watts: int) -> WritePlan:
    """Comando 0x0335 con il limite in W (2 byte), sorgente C0."""
    if not 0 <= watts <= 10000:
        raise ValueError("limite fuori intervallo [0, 10000] W")
    return WritePlan(
        setting="export_limit_w",
        label="Limite di immissione",
        before=settings["export_limit_w"],
        after=watts,
        steps=[WriteStep(f"limite di immissione = {watts} W", p.classic_command(0x35, watts.to_bytes(2, "big")))],
        verify=["settings"],
    )
