# iot_device/modbus_client.py

import logging
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

logger = logging.getLogger(__name__)

SCALING_FACTOR = 10

BATTERY_STATUS = {0: "idle", 1: "accepted", 2: "rejected"}
BATTERY_REJECTION_REASON = {
    0: None,
    1: "device_unavailable",
    2: "charge_power_limit_exceeded",
    3: "discharge_power_limit_exceeded",
    4: "maximum_soc_reached",
    5: "minimum_soc_reached",
}


def raw_to_watts_unsigned(raw: int) -> float:
    """Decode unsigned register — PV Production, AC Output."""
    return raw / SCALING_FACTOR


def raw_to_watts_signed(raw: int) -> float:
    """Decode signed register — Battery Power, Grid Power."""
    if raw > 32767:
        raw = raw - 65536
    return raw / SCALING_FACTOR


def registers_to_watts_signed_32(high: int, low: int) -> float:
    """Decode a big-endian signed 32-bit measurement from two registers."""
    raw = (high << 16) | low
    if raw > 0x7FFFFFFF:
        raw -= 0x100000000
    return raw / SCALING_FACTOR


def registers_to_utc_timestamp(registers: list[int]) -> float:
    """Decode four big-endian registers containing UTC epoch milliseconds."""
    raw = 0
    for register in registers:
        raw = (raw << 16) | register
    return raw / 1000.0


class InverterModbusClient:
    """
    Reads from Inverter Simulator via Modbus TCP.
    In production this would be Modbus RTU over serial.
    For testing we use TCP — same protocol, different transport.
    """

    def __init__(self, host: str = "localhost", port: int = 5020):
        self.host = host
        self.port = port
        self.client = ModbusTcpClient(host=host, port=port)

    def connect(self) -> bool:
        return self.client.connect()

    def disconnect(self):
        self.client.close()

    def read_measurements(self) -> dict | None:
        """
        Read all three inverter registers in one request.
        Returns dict with pv_production, ac_output, battery_power.
        Returns None if read fails.
        """
        try:
            result = self.client.read_holding_registers(
                address=0,
                count=7,
                slave=1
            )

            if result.isError():
                logger.error(
                    f"[Inverter] Modbus error: {result}"
                )
                return None

            regs = result.registers
            return {
                "pv_production": raw_to_watts_unsigned(regs[0]),
                "ac_output":     raw_to_watts_unsigned(regs[1]),
                "battery_power": raw_to_watts_signed(regs[2]),
                "timestamp": registers_to_utc_timestamp(regs[3:7])
            }

        except ModbusException as e:
            logger.error(f"[Inverter] Connection error: {e}")
            return None

    def read_battery_telemetry(self) -> dict | None:
        """Read the battery capability, state, and latest command result."""
        try:
            result = self.client.read_holding_registers(
                address=2, count=18, slave=1
            )
            if result.isError():
                return None
            registers = result.registers
            return {
                "actual_power_w": raw_to_watts_signed(registers[0]),
                "timestamp": registers_to_utc_timestamp(registers[1:5]),
                "soc_percent": registers[5] / 10,
                "usable_capacity_kwh": registers[6] / 1000,
                "max_charge_power_w": registers[7] / 10,
                "max_discharge_power_w": registers[8] / 10,
                "min_soc_percent": registers[9] / 10,
                "max_soc_percent": registers[10] / 10,
                "available": bool(registers[11]),
                "requested_power_w": registers_to_watts_signed_32(
                    registers[12], registers[13]
                ),
                "command_sequence": registers[14],
                "ack_sequence": registers[15],
                "command_status": BATTERY_STATUS.get(registers[16], "unknown"),
                "rejection_reason": BATTERY_REJECTION_REASON.get(
                    registers[17], "unknown_rejection"
                ),
            }
        except ModbusException as error:
            logger.error("[Battery] Telemetry read failed: %s", error)
            return None

    def write_battery_request(
        self, requested_power_w: float, command_sequence: int
    ) -> bool:
        """Write a signed requested power and monotonically changing sequence."""
        raw_power = int(requested_power_w * SCALING_FACTOR) & 0xFFFFFFFF
        try:
            result = self.client.write_registers(
                address=14,
                values=[
                    (raw_power >> 16) & 0xFFFF,
                    raw_power & 0xFFFF,
                    command_sequence & 0xFFFF,
                ],
                slave=1,
            )
            return not result.isError()
        except ModbusException as error:
            logger.error("[Battery] Command write failed: %s", error)
            return False


class PowerMeterModbusClient:
    """
    Reads from Power Meter Simulator via Modbus TCP.
    Uses input registers (function code 4) not holding registers.
    """

    def __init__(self, host: str = "localhost", port: int = 5021):
        self.host = host
        self.port = port
        self.client = ModbusTcpClient(host=host, port=port)

    def connect(self) -> bool:
        return self.client.connect()

    def disconnect(self):
        self.client.close()

    def read_grid_power(self) -> dict | None:
        """
        Read Grid Power register.
        Returns dict with grid_power and timestamp.
        Returns None if read fails.
        """
        try:
            result = self.client.read_input_registers(
                address=0,
                count=6,
                slave=1
            )

            if result.isError():
                logger.error(
                    f"[PowerMeter] Modbus error: {result}"
                )
                return None

            return {
                "grid_power": registers_to_watts_signed_32(
                    result.registers[0], result.registers[1]
                ),
                "timestamp": registers_to_utc_timestamp(
                    result.registers[2:6]
                )
            }

        except ModbusException as e:
            logger.error(f"[PowerMeter] Connection error: {e}")
            return None
