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
- Docker Compose startup and health checks.
- Unit and Modbus integration tests, including golden datasets.

## Energy model and register contract

Grid power is positive when importing and negative when exporting. Battery
power is positive when charging and negative when discharging.

Household consumption is not read directly:

```text
Inverter AC output = PV production - battery power
House consumption  = inverter AC output + grid power
```

| Device | Display address | Internal address | Measurement | Encoding | Scale |
|---|---:|---:|---|---|---:|
| Inverter | 40001 | 0 | PV production | unsigned 16-bit | ÷ 10 W |
| Inverter | 40002 | 1 | AC output | unsigned 16-bit | ÷ 10 W |
| Inverter | 40003 | 2 | Battery power | signed 16-bit | ÷ 10 W |
| Power meter | 30001–30002 | 0–1 | Grid power | signed 32-bit, big-endian | ÷ 10 W |

Grid power uses two registers because a signed 16-bit value at this scale
cannot represent export below `-3276.8 W`, while the simulated PV system can
produce 6 kW.

## Repository structure

```text
.
├── cloud/                  # MQTT terminal dashboard
├── iot_device/             # Gateway, Modbus clients, MQTT and disk buffer
├── simulators/             # Shared scenarios and both Modbus servers
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
| `POLL_INTERVAL` | `2.0` | Poll interval in seconds |
| `BUFFER_FILE` | `iot_buffer.json` | Persistent buffer path |

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
end-to-end golden Modbus datasets.

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
