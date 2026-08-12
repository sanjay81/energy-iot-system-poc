# simulators/inverter_simulator.py

import threading
import time
import logging
from pymodbus.server import StartTcpServer
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusSlaveContext,
    ModbusServerContext
)
from simulators.scenarios import get_energy_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── SCALING FACTOR ───────────────────────────────────────────────
# All values stored as raw integers multiplied by 10
# IoT must divide by 10 to get Watts
# This is where scaling factor bugs live
SCALING_FACTOR = 10

# ─── REGISTER ADDRESSES ───────────────────────────────────────────
# Modbus holding registers start at 40001 in display
# In pymodbus they are zero-indexed internally
# 40001 → index 0
# 40002 → index 1
# 40003 → index 2
REG_PV_PRODUCTION   = 0   # 40001
REG_AC_OUTPUT       = 1   # 40002
REG_BATTERY_POWER   = 2   # 40003
REG_TIMESTAMP       = 3   # 40004-40007, UTC epoch milliseconds
REG_BATTERY_SOC = 7
REG_BATTERY_CAPACITY_WH = 8
REG_MAX_CHARGE_POWER = 9
REG_MAX_DISCHARGE_POWER = 10
REG_MIN_SOC = 11
REG_MAX_SOC = 12
REG_BATTERY_AVAILABLE = 13
REG_REQUESTED_POWER = 14  # 40015-40016, signed 32-bit at x10 scale
REG_COMMAND_SEQUENCE = 16
REG_ACK_SEQUENCE = 17
REG_COMMAND_STATUS = 18
REG_REJECTION_REASON = 19

# Total registers we expose
# pymodbus' slave context translates protocol address 0 to data-block address
# 1, so one extra storage slot is required for protocol register 19.
NUM_REGISTERS = 21


class InverterSimulator:
    """
    Simulates a real inverter's Modbus RTU interface.

    Runs as a Modbus TCP server (RTU over TCP for testing).
    Updates register values every second based on energy scenario.

    Real inverter would expose same register map over serial RS-485.
    For testing we use TCP — same Modbus protocol, different transport.
    """

    def __init__(self, host: str = "localhost", port: int = 5020):
        self.host = host
        self.port = port
        self.hour = 8.0          # Start at 8am
        self.battery_soc = 20.0  # Start at 20% charge
        self._running = False
        self._server_thread = None
        self._update_thread = None

        # Initialise datastore with zeros
        # This is the "register map" the IoT device reads from
        self.store = ModbusSlaveContext(
            hr=ModbusSequentialDataBlock(0, [0] * NUM_REGISTERS)
        )
        self.context = ModbusServerContext(
            slaves=self.store, single=True
        )

    def _watts_to_raw(self, watts: float) -> int:
        """
        Convert Watts to raw register value.
        Applies scaling factor.

        IMPORTANT: Modbus registers are unsigned 16-bit integers (0-65535).
        Negative values (battery charging) must be handled carefully.
        We use two's complement for negative values.
        """
        raw = int(watts * SCALING_FACTOR)

        # Handle negative values — two's complement
        # -1500W → raw=-15000 → stored as 65536-15000=50536
        if raw < 0:
            raw = raw & 0xFFFF  # two's complement 16-bit

        return raw

    @staticmethod
    def _timestamp_to_raw(timestamp: float) -> list[int]:
        raw = int(timestamp * 1000)
        return [(raw >> shift) & 0xFFFF for shift in (48, 32, 16, 0)]

    def set_timestamp(self, timestamp: float) -> None:
        self.store.setValues(
            3, REG_TIMESTAMP, self._timestamp_to_raw(timestamp)
        )

    def _update_registers(self):
        """
        Updates register values based on current energy scenario.
        Called every second in a background thread.
        """
        while self._running:
            # Get current energy state based on simulated hour
            state = get_energy_state(
                hour=self.hour,
                battery_soc=self.battery_soc
            )

            # Convert to raw register values
            pv_raw      = self._watts_to_raw(state.pv_production)
            ac_raw      = self._watts_to_raw(state.inverter_ac_output)
            battery_raw = self._watts_to_raw(state.battery_power)

            # Write to datastore
            # Arguments: (function_code, address, values)
            # function_code 3 = holding registers
            self.store.setValues(3, REG_PV_PRODUCTION,   [pv_raw])
            self.store.setValues(3, REG_AC_OUTPUT,        [ac_raw])
            self.store.setValues(3, REG_BATTERY_POWER,    [battery_raw])
            self.set_timestamp(time.time())

            logger.info(
                f"[Inverter] Hour={self.hour:.1f} | "
                f"PV={state.pv_production:.0f}W | "
                f"AC={state.inverter_ac_output:.0f}W | "
                f"Battery={state.battery_power:.0f}W | "
                f"SOC={self.battery_soc:.0f}%"
            )

            # Advance simulated time — 1 real second = 6 simulated minutes
            self.hour = (self.hour + 0.1) % 24

            # Update battery SOC based on charging/discharging
            soc_change = (-state.battery_power / 10000) * 0.1
            self.battery_soc = max(0, min(100,
                self.battery_soc + soc_change
            ))

            time.sleep(1)

    def start(self, update_registers: bool = True):
        """Start the simulator in background threads."""
        self._running = True

        if update_registers:
            self._update_thread = threading.Thread(
                target=self._update_registers,
                daemon=True,
                name="InverterRegisterUpdater"
            )
            self._update_thread.start()

        # Start Modbus TCP server in background thread
        self._server_thread = threading.Thread(
            target=StartTcpServer,
            kwargs={
                "context": self.context,
                "address": (self.host, self.port)
            },
            daemon=True,
            name="InverterModbusServer"
        )
        self._server_thread.start()

        logger.info(
            f"[Inverter Simulator] Running on "
            f"{self.host}:{self.port}"
        )

    def stop(self):
        """Stop the simulator."""
        self._running = False
        logger.info("[Inverter Simulator] Stopped")

    def set_hour(self, hour: float):
        """Force a specific time of day — useful for testing."""
        self.hour = hour

    def set_register_value(self, register: int, raw_value: int):
        """
        Directly set a register value — for fault injection testing.

        Example:
            sim.set_register_value(REG_PV_PRODUCTION, 99999)
            → Tests how IoT handles out-of-range values

            sim.set_register_value(REG_PV_PRODUCTION, 35000)
            → Golden dataset test — inject known 3500W
        """
        self.store.setValues(3, register, [raw_value])
        logger.info(
            f"[Inverter] Register {register} "
            f"manually set to {raw_value}"
        )

    def simulate_offline(self):
        """
        Stop updating registers but keep server running.
        Tests IoT timeout handling — server responds but
        values go stale.

        To fully go offline, stop the server thread.
        """
        self._running = False
        logger.warning("[Inverter] Simulating offline — registers frozen")


if __name__ == "__main__":
    sim = InverterSimulator(host="localhost", port=5020)
    sim.start()

    print("Inverter Simulator running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()
