# goodwe-dashboard

A self-hosted web page to read and change the settings of a **GoodWe ES/EM** hybrid inverter
(developed for a GW5048-EM), as a replacement for the settings pages of the SolarGo app.

- Talks to the WiFi module over **UDP port 8899** using the same commands as SolarGo
  (details in [docs/PROTOCOL.md](docs/PROTOCOL.md)).
- **No polling**: it reads only when you open the page or press "Read from inverter", and
  writes only when you confirm a save.
- After every write it **reads the value back** and reports whether the inverter accepted it;
  every change goes into a **change log** (when, what, before, requested, read back, outcome).
- **Dry run** shows the exact frames that would be sent, without sending them.
- **Grid interface protection parameters** (e.g. Italian CEI 0-21) are read-only: the portal
  contains no commands to write them.

> Unofficial project, not affiliated with GoodWe. Changing inverter settings is at your own
> risk; start in read-only mode and compare the values with SolarGo.

## What can be changed

| Section | Settings |
|---|---|
| Work mode | General, Backup, Eco (off-grid is shown only) |
| Charge/discharge windows (eco groups) | Weekly timeline editor: drag to move/resize, click to set charge/discharge, power % and days, add and remove windows (up to 4); all changes are saved together |
| Battery | SoC protection, on-grid DoD, off-grid DoD, battery activation |
| Grid, export and backup | Export limit on/off and value (W), shadow scan, backup output, backup detection mode, clear overload history |

**DRED / Remote Shutdown / RCR** can be toggled too (with a warning before enabling it).

The **grid interface protection parameters** (CEI 0-21: voltage/frequency thresholds and trip
times, connection, curves, ride-through, PQ) are shown read-only by default, grouped into tabs.
Editing can be unlocked with a header switch that requires reading a warning and typing
`I UNDERSTAND`; it stays unlocked only for the current browser session. This is intended for a
qualified installer — changing these values can make the system non-compliant and is the
installer's responsibility. Trip times and curve time-constants stay read-only even when
unlocked (their write path is firmware-dependent); set those from SolarGo.

Every setting has a **?** button with an explanation (hover with the mouse, tap on a phone),
including all the protection parameters.

## Install with Portainer (e.g. Raspberry Pi 4)

1. Portainer → **Stacks** → **Add stack** → **Repository**.
2. Repository URL: `https://github.com/FAB1150/goodwe-dashboard`, reference:
   `refs/heads/main`, compose path: `docker-compose.yml`.
3. **Deploy the stack**. The first time the image is built on the device (a couple of minutes).
4. Open `http://<host-ip>:8080`, enter the inverter IP and press **Save IP**. The IP is stored
   in the `goodwe-dashboard-data` volume.

The host must reach the inverter over UDP port 8899 (if the inverter is in another VLAN, the
firewall must allow UDP 8899 from the host and the replies). Docker's bridge network usually
works; if replies never arrive, try `network_mode: host` in the compose file (and remove the
`ports` section).

### Environment variables

Set them in Portainer under **Environment variables** of the stack (or in a `.env` file next to
`docker-compose.yml`); the defaults below apply when they are not set.

| Variable | Default | Meaning |
|---|---|---|
| `WRITES_ENABLED` | `false` | With `false` the portal sends **no** writes at all: everything is a dry run. |
| `INVERTER_HOST` | empty | Initial IP (can be changed from the page). |
| `PORTAL_USER` / `PORTAL_PASSWORD` | `admin` / empty | If a password is set, the page asks for a login (HTTP Basic). Useful behind a reverse proxy. |

## Suggested testing order

1. **Read-only** (`WRITES_ENABLED=false`): open the page and compare the values with SolarGo
   and Home Assistant. The *Diagnostics* section shows the raw frames exchanged.
2. **Writes**: set `WRITES_ENABLED=true`, redeploy, and try one setting at a time, starting
   with the least sensitive ones (export limit, then eco groups, then SoC protection and DoD).
   Keep **Dry run** on to inspect the frames first, then turn it off to send. Every send asks
   for confirmation.

## Development

```bash
pip install -r requirements-dev.txt
pytest                                  # framing and API tests (with a fake inverter)
python -m tests.fake_inverter 18899     # fake inverter on 127.0.0.1:18899
DATA_DIR=./data uvicorn app.main:app --port 8080
```

The tests use responses recorded from real ES/EM inverters, taken from the tests of the
[`goodwe`](https://github.com/marcelblijleven/goodwe) library (MIT licence).
