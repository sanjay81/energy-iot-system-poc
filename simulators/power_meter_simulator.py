# simulators/power_meter_simulator.py

import threading
import time
import logging
from pymodbus.server import StartTcpServer
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusSlaveContext,
    ModbusServerContext
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCALING_FACTOR = 10

# Input register address
REG_GRID_POWER = 0  # 30001
REG_TIMESTAMP = 2   # 30003-30006, UTC epoch milliseconds


class PowerMeterSimulator:
    """
    Simulates a real Power Meter's Modbus TCP interface.

    Measures grid power via CT Clamps on 3-phase AC line.
    Positive = importing from grid.
    Negative = exporting to grid.

    Runs on a different port from the Inverter Simulator
    so both can run simultaneously on the same machine.

    Real Power Meter would expose this over Ethernet
    via the Switch — Interface 4 in our architecture.
    """

    def __init__(self, host: str = "localhost", port: int = 5021):
        self.host = host
        self.port = port
        self._running = False
        self._grid_power = 0.0  # current grid power in Watts

        # Input registers (function code 4)
        self.store = ModbusSlaveContext(
            ir=ModbusSequentialDataBlock(0, [0] * 10)
        )
        self.context = ModbusServerContext(
            slaves=self.store, single=True
        )

    def _watts_to_raw(self, watts: float) -> list[int]:
        """
        Convert Watts to a signed 32-bit value across two registers.

        A signed 16-bit value at x10 scaling bottoms out at -3276.8 W,
        which cannot represent normal export from a 6 kW PV installation.
        """
        raw = int(watts * SCALING_FACTOR) & 0xFFFFFFFF
        return [(raw >> 16) & 0xFFFF, raw & 0xFFFF]

    @staticmethod
    def _timestamp_to_raw(timestamp: float) -> list[int]:
        raw = int(timestamp * 1000)
        return [(raw >> shift) & 0xFFFF for shift in (48, 32, 16, 0)]

    def set_timestamp(self, timestamp: float) -> None:
        self.store.setValues(
            4, REG_TIMESTAMP, self._timestamp_to_raw(timestamp)
        )

    def set_grid_power(self, watts: float, timestamp: float | None = None):
        """
        Set grid power directly.
        Used by IoT Device integration and fault injection tests.

        Positive = importing (house needs more than inverter provides)
        Negative = exporting (inverter produces more than house needs)
        """
        self._grid_power = watts
        raw = self._watts_to_raw(watts)
        self.store.setValues(4, REG_GRID_POWER, raw)
        self.set_timestamp(timestamp if timestamp is not None else time.time())
        logger.info(
            f"[PowerMeter] Grid Power = {watts:.1f}W "
            f"({'importing' if watts > 0 else 'exporting' if watts < 0 else 'balanced'})"
        )

    def start(self):
        """Start the simulator in a background thread."""
        self._running = True

        self._server_thread = threading.Thread(
            target=StartTcpServer,
            kwargs={
                "context": self.context,
                "address": (self.host, self.port)
            },
            daemon=True,
            name="PowerMeterModbusServer"
        )
        self._server_thread.start()
        logger.info(
            f"[PowerMeter Simulator] Running on "
            f"{self.host}:{self.port}"
        )

    def stop(self):
        self._running = False
        logger.info("[PowerMeter Simulator] Stopped")

    def simulate_ct_clamp_fault(self):
        """
        Simulates a CT Clamp installed backwards.
        Flips the sign of grid power.
        This is a physical silent failure — software cannot detect it.
        """
        self._grid_power = -self._grid_power
        raw = self._watts_to_raw(self._grid_power)
        self.store.setValues(4, REG_GRID_POWER, raw)
        self.set_timestamp(time.time())
        logger.warning(
            "[PowerMeter] CT CLAMP FAULT — sign flipped "
            f"Grid Power now = {self._grid_power:.1f}W"
        )

    def simulate_offline(self):
        """Stop responding — tests IoT timeout handling."""
        self._running = False
        logger.warning("[PowerMeter] Simulating offline")


if __name__ == "__main__":
    import math

    sim = PowerMeterSimulator(host="localhost", port=5021)
    sim.start()

    print("Power Meter Simulator running. Press Ctrl+C to stop.")
    hour = 8.0

    try:
        while True:
            # Simple grid power simulation
            # Morning → importing, midday → exporting, evening → importing
            angle = math.pi * (hour - 6) / 14
            pv_approx = math.sin(angle) * 6000 if 6 < hour < 20 else 0
            house_approx = 2000
            grid = house_approx - pv_approx
            sim.set_grid_power(grid)
            hour = (hour + 0.1) % 24
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()
