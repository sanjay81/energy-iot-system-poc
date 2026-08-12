import threading
import json
from types import SimpleNamespace

from iot_device.buffer import LocalBuffer
from iot_device.mqtt_publisher import MQTTPublisher, TOPIC_MEASUREMENTS
from iot_device.calculator import EnergyMeasurement


class FakeMQTTClient:
    def __init__(self):
        self.messages = []
        self.next_mid = 10

    def publish(self, topic, payload, qos=0, retain=False):
        mid = self.next_mid
        self.next_mid += 1
        self.messages.append((topic, payload, qos))
        return SimpleNamespace(rc=0, mid=mid)


def build_publisher(tmp_path):
    publisher = MQTTPublisher.__new__(MQTTPublisher)
    publisher.client = FakeMQTTClient()
    publisher.buffer = LocalBuffer(buffer_file=str(tmp_path / "buffer.json"))
    publisher._pending_replay_mid = None
    publisher._lock = threading.Lock()
    publisher.device_id = "test-device"
    publisher._connected = True
    return publisher


def test_replay_waits_for_ack_before_removing_record(tmp_path):
    publisher = build_publisher(tmp_path)
    publisher.buffer.add({"timestamp": 1})
    publisher.buffer.add({"timestamp": 2})

    publisher._upload_buffer()

    assert publisher.buffer.size == 2
    assert publisher.client.messages[0][0] == TOPIC_MEASUREMENTS

    publisher._on_publish(None, None, 10)

    assert publisher.buffer.size == 1
    assert len(publisher.client.messages) == 2

    publisher._on_publish(None, None, 11)
    assert publisher.buffer.is_empty


def test_measurement_payload_contains_daily_energy_totals(tmp_path):
    publisher = build_publisher(tmp_path)
    measurement = EnergyMeasurement(
        pv_production=3000,
        ac_output=2500,
        battery_power=-500,
        grid_power=-500,
        house_consumption=2000,
        inverter_timestamp=100,
        powermeter_timestamp=100.01,
        accounting_status="integrated",
        daily_energy={"local_date": "2026-08-12", "pv_generation_wh": 42.0},
        daily_energy_flows={"pv_direct_to_house_wh": 20.0},
        battery_provenance={"pv_origin_wh": 10.0},
        daily_kpis={"self_consumption_percent": 71.4},
    )

    assert publisher.publish_measurement(measurement)
    payload = json.loads(publisher.client.messages[-1][1])

    assert payload["accounting_status"] == "integrated"
    assert payload["daily_energy"]["pv_generation_wh"] == 42.0
    assert payload["daily_kpis"]["self_consumption_percent"] == 71.4
    assert payload["battery_provenance"]["pv_origin_wh"] == 10.0
