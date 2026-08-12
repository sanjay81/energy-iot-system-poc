from iot_device.iot_device import IoTDevice
from iot_device.publisher import NullPublisher
from iot_device.energy_accounting import EnergyAccumulator


class FakeInverterClient:
    timestamp = 100.0

    def connect(self):
        return True

    def disconnect(self):
        pass

    def read_measurements(self):
        return {
            "pv_production": 3000,
            "ac_output": 2500,
            "battery_power": 500,
            "timestamp": self.timestamp,
        }


class FakePowerMeterClient:
    timestamp = 100.01

    def connect(self):
        return True

    def disconnect(self):
        pass

    def read_grid_power(self):
        return {"grid_power": -500, "timestamp": self.timestamp}


class FailingInverterClient(FakeInverterClient):
    def read_measurements(self):
        return None


class RecordingPublisher:
    def __init__(self):
        self.connected = False
        self.measurements = []
        self.faults = []

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def publish_measurement(self, measurement):
        self.measurements.append(measurement)
        return True

    def publish_fault(self, fault_code):
        self.faults.append(fault_code)


def build_device(publisher=None, energy_accumulator=None):
    device = IoTDevice(
        publisher=publisher,
        energy_accumulator=energy_accumulator,
        device_id="test-device",
    )
    device.inverter_client = FakeInverterClient()
    device.powermeter_client = FakePowerMeterClient()
    return device


def test_mqtt_is_disabled_by_default():
    device = build_device()

    assert isinstance(device.publisher, NullPublisher)
    assert device.connect()
    assert device.poll_once() is not None


def test_injected_publisher_receives_measurement_without_broker():
    publisher = RecordingPublisher()
    device = build_device(publisher)

    assert device.connect()
    measurement = device.poll_once()

    assert publisher.connected
    assert publisher.measurements == [measurement]
    assert measurement.house_consumption == 2000

    device.disconnect()
    assert not publisher.connected


def test_read_failure_is_published_before_poll_returns():
    publisher = RecordingPublisher()
    device = build_device(publisher)
    device.inverter_client = FailingInverterClient()
    device.connect()

    assert device.poll_once() is None
    assert "inverter_read_failed" in publisher.faults


def test_gateway_attaches_persisted_daily_energy_totals(tmp_path):
    accounting = EnergyAccumulator(
        state_file=str(tmp_path / "energy.json")
    )
    device = build_device(energy_accumulator=accounting)
    device.connect()

    first = device.poll_once()
    device.inverter_client.timestamp += 5
    device.powermeter_client.timestamp += 5
    second = device.poll_once()

    assert first.accounting_status == "baseline"
    assert second.accounting_status == "integrated"
    assert second.daily_energy["integrated_intervals"] == 1
    assert second.daily_energy["pv_generation_wh"] > 0
