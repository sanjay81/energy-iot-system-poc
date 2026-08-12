# Energy IoT System POC

[![tests](https://github.com/sanjay81/energy-iot-system-poc/actions/workflows/tests.yml/badge.svg)](https://github.com/sanjay81/energy-iot-system-poc/actions/workflows/tests.yml)

A software-only proof of concept for an energy-monitoring gateway. It simulates
an inverter and power meter, reads both over Modbus TCP, derives household
consumption, validates the result, and publishes measurements and faults over
MQTT. A persistent local buffer protects measurements during broker outages.

## Current status

The local data path is implemented and tested:

```text
Inverter simulator ---- Modbus TCP ---+
                                      +--> IoT gateway --> MQTT --> Dashboard
Power-meter simulator - Modbus TCP ---+         |
                                                +--> persistent outage buffer
```

Verified capabilities:

- Coordinated inverter and power-meter simulation from one energy state.
- Signed, unsigned, scaled, and two-register Modbus decoding.
- Physical validation before data reaches MQTT.
- QoS 1 MQTT measurements, status, Last Will, and fault topics.
- Atomic disk buffering while MQTT is unavailable.
- Chronological replay, removing records only after broker acknowledgement.
- Event-driven Wh accounting with persistent Berlin-local daily totals.
- Auditable daily KPIs with persistent PV/grid/unknown battery provenance.
- Reproducible accelerated 24-hour no-control baseline scenario and report.
- Docker Compose startup and health checks.
- Unit and Modbus integration tests, including golden datasets.

## Energy model and register contract

Grid power is positive when importing and negative when exporting. Battery
power is positive when discharging and negative when charging.

Household consumption is not read directly:

```text
Inverter AC output = PV production + battery power
House consumption  = inverter AC output + grid power
```

| Device | Display address | Internal address | Measurement | Encoding | Scale |
|---|---:|---:|---|---|---:|
| Inverter | 40001 | 0 | PV production | unsigned 16-bit | ÷ 10 W |
| Inverter | 40002 | 1 | AC output | unsigned 16-bit | ÷ 10 W |
| Inverter | 40003 | 2 | Battery power | signed 16-bit | ÷ 10 W |
| Inverter | 40004–40007 | 3–6 | Device timestamp | unsigned 64-bit | UTC epoch ms |
| Power meter | 30001–30002 | 0–1 | Grid power | signed 32-bit, big-endian | ÷ 10 W |
| Power meter | 30003–30006 | 2–5 | Device timestamp | unsigned 64-bit | UTC epoch ms |

Grid power uses two registers because a signed 16-bit value at this scale
cannot represent export below `-3276.8 W`, while the simulated PV system can
produce 6 kW.

## Energy accounting

The gateway accepts event-driven readings and uses consecutive device
timestamps to integrate power with the trapezoidal rule. The Docker simulation
polls every five seconds.

Current daily totals include:

- PV generation and household consumption
- Grid import and export
- Battery charge and discharge

Timestamps originate at the simulated devices and are carried through Modbus
as Unix epoch milliseconds representing UTC. Daily totals roll over at local
midnight in `Europe/Berlin`, including daylight-saving transitions. An interval
crossing midnight is split at the boundary.

Intervals longer than 60 seconds are classified as stale/missing and are not
integrated. A reading is identified by device/source and timestamp. Exact
duplicates are ignored, while older out-of-order readings do not modify live
totals. The latest accepted reading and current daily totals are persisted
atomically so accounting continues after restart.

## Daily energy KPIs

Battery energy is tracked in three persistent virtual provenance buckets:
`pv_origin_wh`, `grid_origin_wh`, and `unknown_origin_wh`. Charging is allocated
PV-first: PV serves the house, then charges the battery, and only the remainder
is exported. Any charging not covered by PV surplus is grid-origin. Discharge
is withdrawn proportionally from all three buckets because stored energy is
physically mixed.

Provenance survives restart and Berlin-local midnight. PV-origin energy stored
on one day therefore contributes to self-sufficiency when it supplies the
house on a later day. Unknown- and grid-origin discharge never count as local
renewable supply. Step 2 assumes 100% battery accounting efficiency; explicit
loss modelling belongs with the later battery/SOC model.

The daily formulas are:

```text
PV used locally = PV direct to house + PV sent to battery
Self-consumption = PV used locally / PV generation × 100

Locally supplied consumption =
    PV direct to house + PV-origin battery discharge to house
Self-sufficiency = locally supplied consumption / house consumption × 100

Peak grid demand = maximum positive grid import power
Net grid energy = grid import - grid export
Battery throughput = battery charge + battery discharge

Energy entering = PV + grid import + battery discharge
Energy leaving = house consumption + grid export + battery charge
Energy balance error = energy entering - energy leaving
```

Self-consumption is `null` when there is no PV generation; self-sufficiency is
`null` when there is no household consumption. Each has an explicit
`not_applicable` status. Percentage KPIs are bounded to 0–100%. Energy balance
error is also exposed as an absolute percentage of the larger boundary flow.

Step 2 introduces a versioned accounting-state schema. On the one-time upgrade
from Step 1, current daily totals are reset because historical totals cannot be
reliably reconstructed into attributed flows. Subsequent restarts preserve the
complete totals, flows, peak, and provenance state.

## Step 3 baseline scenario

Step 3 freezes a deterministic clear-sky PV curve and household demand profile
for 15 June 2026 in `Europe/Berlin`. It sends 289 five-minute samples (both
midnight endpoints) through the real Modbus → accounting → KPI → MQTT path.
Battery power is fixed at zero and smart control, tariffs, EV demand, and
optimization are intentionally excluded.

The baseline runner uses a 300-second accounting gap only for this accelerated
scenario; the live gateway retains the production default of 60 seconds. The
result records 288 integrated intervals and is compared exactly with the
version-controlled golden report.

Run and verify it with Docker:

```bash
docker compose up -d mqtt
docker compose --profile baseline run --rm baseline
```

The generated report is written to `results/baseline_report.json`. The frozen
benchmark is `simulations/golden/baseline_report.json` and currently records
53.474483 kWh PV generation, 24.722733 kWh household consumption, 9.660677 kWh
grid import, and 38.412427 kWh grid export.

## Repository structure

```text
.
├── cloud/                  # MQTT terminal dashboard
├── iot_device/             # Gateway, Modbus clients, MQTT and disk buffer
├── simulators/             # Shared scenarios and both Modbus servers
├── simulations/            # Step 3 profile, runner, report and golden result
├── tests/
│   ├── unit/               # Calculation, gateway, buffer and MQTT tests
│   └── integration/        # Simulator-to-gateway golden datasets
├── .github/workflows/      # Python and Docker CI
├── Dockerfile
├── docker-compose.yml
├── mosquitto.conf
└── requirements.txt
```

## Run with Docker

Requirements: Docker Desktop or Docker Engine with Compose v2.

Start Mosquitto, both Modbus simulators, and the gateway:

```bash
docker compose up --build
```

Include the interactive dashboard:

```bash
docker compose --profile dashboard up --build
```

Inspect MQTT traffic directly:

```bash
docker compose exec mqtt mosquitto_sub \
  -h localhost -t 'energy-iot/#' -v
```

Stop containers while retaining persistent volumes:

```bash
docker compose down
```

Use `docker compose down --volumes` only when intentionally deleting broker and
gateway-buffer data.

## Demonstrate outage recovery

With the stack running:

```bash
docker compose stop mqtt
docker compose exec gateway python -c \
  "from iot_device.buffer import LocalBuffer; print(LocalBuffer(buffer_file='/data/iot_buffer.json').size)"
docker compose start mqtt
docker compose logs -f gateway
```

The buffer grows while the broker is stopped. After reconnection, the gateway
replays records in order and the size returns to zero as QoS acknowledgements
arrive.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Start an MQTT broker, then run these commands in separate terminals:

```bash
python -m simulators.coordinator
python -m iot_device.iot_device
python -m cloud.dashboard
```

Gateway configuration:

| Variable | Default | Purpose |
|---|---|---|
| `INVERTER_HOST` | `localhost` | Inverter Modbus host |
| `INVERTER_PORT` | `5020` | Inverter Modbus port |
| `POWERMETER_HOST` | `localhost` | Power-meter Modbus host |
| `POWERMETER_PORT` | `5021` | Power-meter Modbus port |
| `MQTT_HOST` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `DEVICE_ID` | `energy_iot_001` | MQTT device identifier |
| `POLL_INTERVAL` | `5.0` | Poll interval in seconds |
| `BUFFER_FILE` | `iot_buffer.json` | Persistent buffer path |
| `ENERGY_STATE_FILE` | `energy_accounting.json` | Accounting state path |
| `ENERGY_TIMEZONE` | `Europe/Berlin` | Daily-total boundary timezone |

## Tests

```bash
pytest -q
pytest tests/unit -q
pytest tests/integration -q
docker compose config --quiet
```

The tests cover calculation rules, physical constraints, register encoding,
sign and scaling conventions, timing gaps, fault forwarding, publisher
injection, persistent buffer behavior, overflow, acknowledged MQTT replay, and
end-to-end golden Modbus datasets. KPI tests cover PV/grid/unknown provenance,
cross-day persistence, zero denominators, proportional discharge, peak demand,
net grid energy, battery throughput, balance error, and Step 1 state migration.
The Docker CI job also runs the full accelerated baseline and requires an exact
match with the committed report.

## Important failures found during the POC

### Negative inverter output

Charging the battery faster than available PV originally produced a negative
AC value. The energy model now clamps inverter output to zero and only charges
when PV surplus exists.

### Unsigned PV decoded as signed

Applying two's-complement decoding to every register made PV values above
3276.7 W appear negative. PV and AC registers now use unsigned decoding, while
battery power uses signed decoding.

### Grid export overflow

A single signed 16-bit grid register could not represent normal 4 kW export at
×10 scaling. Grid power now uses a signed 32-bit value across two registers.

### Competing simulator states

The inverter and coordinator previously updated the same datastore from
separate scenario loops. The coordinator is now the only updater when running
the combined system, keeping golden datasets deterministic.

## Design lessons

- Treat the register map as a versioned contract: address, width, signedness,
  byte order, scale, and unit all matter.
- Enforce physical constraints at the gateway boundary so plausible protocol
  values cannot silently become invalid cloud data.
- Inject known values and assert the final result; golden datasets expose
  cross-interface failures that isolated unit tests cannot.
- Remove buffered data only after delivery acknowledgement, not merely after a
  client library accepts it locally.

## POC boundaries and next phase

This repository intentionally uses anonymous, unencrypted local MQTT and
Modbus TCP simulators. It is not production-ready. The next phase should add:

1. Real Modbus RTU/RS-485 hardware validation.
2. MQTT authentication, TLS, and per-device topic authorization.
3. Measurement IDs, sequence numbers, schema versions, and duplicate handling.
4. A cloud consumer and time-series persistence.
5. Structured metrics, alerts, and a long-running resilience test.
