from iot_device.iot_device import IoTDevice
from iot_device.publisher import NullPublisher


class FakeInverterClient:
    def connect(self):
        return True

    def disconnect(self):
        pass

    def read_measurements(self):
        return {
            "pv_production": 3000,
            "ac_output": 2500,
            "battery_power": 500,
            "timestamp": 100.0,
        }


class FakePowerMeterClient:
    def connect(self):
        return True

    def disconnect(self):
        pass

    def read_grid_power(self):
        return {"grid_power": -500, "timestamp": 100.01}


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


def build_device(publisher=None):
    device = IoTDevice(publisher=publisher)
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
