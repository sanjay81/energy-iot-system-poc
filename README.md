# Energy IoT System POC

[![tests](https://github.com/sanjay81/energy-iot-system-poc/actions/workflows/tests.yml/badge.svg)](https://github.com/sanjay81/energy-iot-system-poc/actions/workflows/tests.yml)

## Portfolio overview

This project is a **software-only IoT/edge validation POC** designed to demonstrate system integration, protocol handling, resilience, deterministic testing, and measurable energy-control behavior.

**What it showcases**

- Python-based edge/gateway logic.
- Modbus TCP device simulation and register-contract validation.
- MQTT messaging with QoS, status, fault, and acknowledgement flows.
- Persistent buffering and ordered replay during broker outages.
- Docker Compose orchestration and health checks.
- Unit, integration, golden-dataset, and end-to-end tests.
- Energy accounting and KPI validation.
- Safe command-driven battery control.
- Deterministic self-consumption control.
- Baseline-versus-smart comparison with reproducible metrics.

The POC intentionally uses simulated devices and local infrastructure so the entire system can be published and reproduced without proprietary hardware or customer code.

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
- Safe command-driven battery interface with SOC and power-limit enforcement.
- Opt-in deterministic self-consumption controller using the Step 4 contract.
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
| Inverter | 40008 | 7 | Battery SOC | unsigned 16-bit | ÷ 10 % |
| Inverter | 40009 | 8 | Usable battery capacity | unsigned 16-bit | Wh |
| Inverter | 40010–40014 | 9–13 | Charge/discharge and SOC limits, availability | unsigned 16-bit | ÷ 10 |
| Inverter | 40015–40016 | 14–15 | Requested battery power | signed 32-bit | ÷ 10 W |
| Inverter | 40017–40020 | 16–19 | Command and acknowledgement sequences, status, reason | unsigned 16-bit | enum |
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

## Step 4 safe battery control

Step 4 makes the battery explicitly command-driven. The coordinator no longer
calculates charge or discharge from PV surplus or household deficit. Without a
command, the battery remains idle; deciding *when* operation is beneficial is
reserved for a later EMS-control phase.

The sign convention is unchanged: positive power discharges and negative power
charges. Commands use schema version 1 and contain a unique command ID, target
device, requested watts, UTC issue time, and optional expiry. The gateway
rejects malformed, wrong-device, unsupported-version, and stale commands before
writing Modbus. The battery remains the final safety authority and rejects
commands exceeding charge/discharge power or configured SOC limits.

```json
{
  "schema_version": 1,
  "command_id": "demo-charge-1",
  "device_id": "energy_iot_001",
  "requested_power_w": -1200,
  "issued_at": 1786550400,
  "expires_at": 1786550430
}
```

Publish commands to `energy-iot/<device-id>/battery/commands`. Acknowledgements
arrive on `energy-iot/<device-id>/battery/acknowledgements` with `accepted` or
`rejected`, actual power, and a structured rejection reason. Measurement
messages include `battery_state`: requested and actual power, SOC, usable
capacity, power/SOC limits, availability, and operating mode. Efficiency is an
explicit nullable field reserved for the later loss model.

Command IDs are idempotent and the latest 100 acknowledgements persist across
gateway restarts. Battery SOC, requested/actual power, availability, and the
latest device command also persist atomically. Separate request and
acknowledgement sequences prevent a previous result from being mistaken for a
new device response.

Run the Docker MQTT → gateway → Modbus → telemetry safety check:

```bash
docker compose up --build --wait
docker compose --profile battery-check run --rm battery-check
```

## Step 5 automatic self-consumption

Step 5 is a separate decision layer. It decides the desired battery power and
submits an ordinary versioned command to Step 4. It cannot write Modbus, change
SOC, enforce device limits, or assume that requested power was executed. Step 4
continues to accept or reject every request and reports actual power.

The initial deterministic policy is intentionally narrow:

```text
PV surplus       -> request charge:    -min(PV - house, controller limit)
Household deficit -> request discharge: +min(house - PV, controller limit)
Within deadband   -> request idle:       0 W
Stale/invalid/lost measurement -> fail-safe idle request
```

Automatic operation is opt-in. `EMS_MODE=manual` is the default and emits no
automatic commands. This preserves direct Step 4 control and the Step 3
no-control benchmark. Replayed/out-of-order measurements and unchanged desired
power do not create duplicate commands. The last measurement, desired power,
and decision persist so a restart does not repeat the previous action.

Controller decisions are included in MQTT measurements as `controller_state`,
including desired power, reason, command ID, Step 4 acknowledgement, actual
power, and rejection reason. Tariffs, forecasts, EV scheduling, battery
efficiency optimization, and economic dispatch remain outside Step 5.

Run automatic mode and its Docker check:

```bash
EMS_MODE=automatic docker compose up --build --wait
docker compose --profile self-consumption-check run --rm --no-deps self-consumption-check
```

## Step 6 baseline versus smart comparison

Step 6 runs the unchanged Step 3 profile twice: once with an idle battery and
once with the frozen Step 5 policy executing through Step 4. The profile has a
SHA-256 fingerprint and the report embeds every controller and battery setting.
The configuration is versioned as `baseline_vs_smart_v1`; it is not tuned while
generating the comparison.

Current deterministic results:

| Metric | Baseline | Smart | Change |
|---|---:|---:|---:|
| Grid import | 9.660677 kWh | 0.224874 kWh | -97.672275% raw |
| Grid export | 38.412427 kWh | 31.214607 kWh | -18.738259% |
| Self-consumption | 28.166810% | 42.012698% | +13.845888 points |
| Self-sufficiency | 60.923911% | 85.497569% | +24.573658 points |
| Peak grid demand | 2.2355 kW | 0.4500 kW | -79.870275% |
| Ending SOC | 50.0% | 27.5% | -22.5 points |

The raw import reduction includes 2.25 kWh of net battery depletion because the
smart case ends below its starting SOC. With that stored-energy change added
back, smart grid import is 2.474874 kWh and the SOC-adjusted reduction is
74.381982%. Both figures are reported to prevent the end-state difference from
being hidden. Battery efficiency remains unset, matching the existing ideal
Step 4 battery model.

The smart run emitted 157 commands: 139 accepted and 18 rejected by Step 4.
Accounting uses actual battery power and finishes with a 0.000132% energy
balance error.

Run and exactly verify the comparison:

```bash
docker compose up -d mqtt
docker compose --profile comparison run --rm comparison
```

The generated result is `results/comparison_report.json`; the committed golden
report is `simulations/golden/comparison_report.json`.

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
| `BATTERY_CONTROL_STATE_FILE` | `battery_control.json` | Command IDs and acknowledgements |
| `BATTERY_STATE_FILE` | unset | Simulator SOC and device command state |
| `EMS_MODE` | `manual` | `manual` or opt-in `automatic` control mode |
| `CONTROLLER_STATE_FILE` | `self_consumption_controller.json` | Last Step 5 decision state |
| `CONTROLLER_MAX_POWER_W` | `3000` | Step 5 desired-power cap; not a safety limit |
| `CONTROLLER_STALE_AFTER_SECONDS` | `15` | Maximum usable measurement age |
| `CONTROLLER_DEADBAND_W` | `50` | Balance range that requests idle |

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
