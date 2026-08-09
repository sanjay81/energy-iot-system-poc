# Energy IoT System POC

This POC simulates an inverter and power meter, reads both over Modbus TCP,
calculates household consumption, validates the result, and publishes it to an
MQTT broker. A persistent gateway buffer protects measurements while MQTT is
unavailable.

## Architecture

```text
Inverter simulator ---- Modbus TCP ---+
                                      +--> IoT gateway --> MQTT --> Dashboard
Power-meter simulator - Modbus TCP ---+         |
                                                +--> disk buffer during outage
```

Grid power is positive while importing and negative while exporting. Battery
power is positive while charging and negative while discharging.

## Run everything with Docker

Requirements: Docker Desktop or Docker Engine with Compose v2.

```bash
docker compose up --build
```

The default stack starts Mosquitto, both Modbus simulators, and the IoT gateway.
To include the interactive terminal dashboard:

```bash
docker compose --profile dashboard up --build
```

Inspect measurements without the dashboard:

```bash
docker compose exec mqtt mosquitto_sub \
  -h localhost -t 'energy-iot/#' -v
```

Stop the stack:

```bash
docker compose down
```

Persistent MQTT and gateway-buffer volumes are retained. Remove them only when
you intentionally want a clean environment:

```bash
docker compose down --volumes
```

## Demonstrate outage recovery

With the stack running:

```bash
docker compose stop mqtt
docker compose exec gateway python -c \
  "from iot_device.buffer import LocalBuffer; print(LocalBuffer(buffer_file='/data/iot_buffer.json').size)"
docker compose start mqtt
docker compose logs -f gateway
```

The buffer size should increase during the outage and return to zero after the
broker reconnects and acknowledges the replayed QoS 1 messages.

## Run locally without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Start a broker, then use separate terminals for:

```bash
python -m simulators.coordinator
python -m iot_device.iot_device
python -m cloud.dashboard
```

Environment variables accepted by the gateway include `INVERTER_HOST`,
`INVERTER_PORT`, `POWERMETER_HOST`, `POWERMETER_PORT`, `MQTT_HOST`,
`MQTT_PORT`, `DEVICE_ID`, `POLL_INTERVAL`, and `BUFFER_FILE`.

## Test scope

The automated suite covers calculation rules, register encoding and decoding,
physical validation, golden Modbus datasets, timing gaps, publisher injection,
fault forwarding, persistent buffer behavior, overflow, and acknowledged MQTT
buffer replay.

The historical Phase 1 notes remain in `PHASE1_README.md`.
