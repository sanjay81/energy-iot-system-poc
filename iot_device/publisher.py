"""Publisher boundary used by the IoT gateway.

The gateway depends on this small contract instead of depending directly on
MQTT. Tests can therefore use an in-memory publisher without a broker.
"""

from typing import Protocol

from iot_device.calculator import EnergyMeasurement


class MeasurementPublisher(Protocol):
    """Transport-independent publishing contract."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def publish_measurement(self, measurement: EnergyMeasurement) -> bool: ...

    def publish_fault(self, fault_code: str) -> None: ...

    def set_battery_command_handler(self, handler) -> None: ...


class NullPublisher:
    """No-op publisher for local runs that do not need cloud transport."""

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def publish_measurement(self, measurement: EnergyMeasurement) -> bool:
        return True

    def publish_fault(self, fault_code: str) -> None:
        pass

    def set_battery_command_handler(self, handler) -> None:
        pass
