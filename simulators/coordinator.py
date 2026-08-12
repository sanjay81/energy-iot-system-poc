# simulators/coordinator.py

import time
import threading
import logging
import os
from simulators.scenarios import (
    EnergyState,
    get_house_consumption,
    get_pv_production,
)
from simulators.inverter_simulator import InverterSimulator
from simulators.power_meter_simulator import PowerMeterSimulator
from simulators.battery_device import BatteryDevice
from iot_device.modbus_client import registers_to_watts_signed_32

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SystemCoordinator:
    """
    Coordinates both simulators from a single energy state.

    In a real installation both devices measure the
    same physical reality — they are always consistent.

    In simulation we must enforce this consistency
    by deriving all values from one shared state.

    This is the difference between:
    - Testing interfaces in isolation (Phase 1)
    - Testing the combined system (Phase 2)
    """

    def __init__(
        self,
        host: str | None = None,
        inverter_port: int | None = None,
        powermeter_port: int | None = None,
    ):
        host = host or os.getenv("SIMULATOR_HOST", "localhost")
        self.inverter = InverterSimulator(
            host=host,
            port=inverter_port or int(os.getenv("INVERTER_PORT", "5020"))
        )
        self.power_meter = PowerMeterSimulator(
            host=host,
            port=powermeter_port or int(os.getenv("POWERMETER_PORT", "5021"))
        )
        self.hour = 8.0
        self.battery_soc = 50.0
        self.battery = BatteryDevice(
            state_file=os.getenv("BATTERY_STATE_FILE")
        )
        self._last_command_sequence = 0
        self._running = False
        self._manual_until = 0.0

    def _update_loop(self):
        """
        Single source of truth for both simulators.
        Both devices read from the same energy state.
        """
        while self._running:
            self._apply_battery_request()
            if time.monotonic() < self._manual_until:
                measurement_time = time.time()
                self.inverter.set_timestamp(measurement_time)
                self.power_meter.set_timestamp(measurement_time)
                time.sleep(0.1)
                continue

            self.battery.advance(1.0)
            battery = self.battery.snapshot()
            state = EnergyState(
                pv_production=get_pv_production(self.hour),
                house_consumption=get_house_consumption(self.hour),
                battery_power=battery["actual_power_w"],
            )

            # Update Inverter registers
            self.inverter.store.setValues(
                3, 0,
                [
                    self.inverter._watts_to_raw(
                        state.pv_production
                    ),
                    self.inverter._watts_to_raw(
                        state.inverter_ac_output
                    ),
                    self.inverter._watts_to_raw(
                        state.battery_power
                    ),
                ]
            )
            measurement_time = time.time()
            self.inverter.set_timestamp(measurement_time)
            self._write_battery_telemetry()

            # Grid power derived from SAME state
            # This is what makes the two simulators consistent
            grid_power = (
                state.house_consumption
                - state.inverter_ac_output
            )
            self.power_meter.set_grid_power(grid_power, measurement_time)

            logger.info(
                f"[Coordinator] Hour={self.hour:.1f} | "
                f"PV={state.pv_production:.0f}W | "
                f"AC={state.inverter_ac_output:.0f}W | "
                f"Battery={state.battery_power:.0f}W | "
                f"Grid={grid_power:.0f}W | "
                f"House={state.house_consumption:.0f}W"
            )

            # Advance time
            self.hour = (self.hour + 0.1) % 24

            time.sleep(1)

    def start(self):
        # The coordinator is the sole register updater. Starting the inverter's
        # independent scenario thread as well would create two competing states.
        self.inverter.start(update_registers=False)
        self.power_meter.start()
        self._running = True

        self._update_thread = threading.Thread(
            target=self._update_loop,
            daemon=True,
            name="SystemCoordinator"
        )
        self._update_thread.start()
        logger.info("[Coordinator] Both simulators started")

    def start_manual(self):
        """Start Modbus servers without a wall-clock scenario update loop."""
        self.inverter.start(update_registers=False)
        self.power_meter.start()
        logger.info("[Coordinator] Manual-clock simulators started")

    def _apply_battery_request(self):
        registers = self.inverter.store.getValues(3, 14, count=3)
        sequence = registers[2]
        if sequence == self._last_command_sequence:
            return
        self._last_command_sequence = sequence
        requested = registers_to_watts_signed_32(registers[0], registers[1])
        acknowledgement = self.battery.command(
            f"modbus-{sequence}", requested
        )
        if acknowledgement["status"] == "accepted":
            self._manual_until = 0.0
            self.inverter.store.setValues(
                3,
                2,
                [
                    self.inverter._watts_to_raw(
                        acknowledgement["actual_power_w"]
                    )
                ],
            )
        status = 1 if acknowledgement["status"] == "accepted" else 2
        reasons = {
            None: 0,
            "device_unavailable": 1,
            "charge_power_limit_exceeded": 2,
            "discharge_power_limit_exceeded": 3,
            "maximum_soc_reached": 4,
            "minimum_soc_reached": 5,
        }
        self.inverter.store.setValues(
            3,
            17,
            [
                sequence,
                status,
                reasons[acknowledgement["rejection_reason"]],
            ],
        )

    def _write_battery_telemetry(self):
        battery = self.battery.snapshot()
        self.inverter.store.setValues(
            3,
            7,
            [
                int(battery["soc_percent"] * 10),
                int(battery["usable_capacity_kwh"] * 1000),
                int(battery["max_charge_power_w"] * 10),
                int(battery["max_discharge_power_w"] * 10),
                int(battery["min_soc_percent"] * 10),
                int(battery["max_soc_percent"] * 10),
                int(battery["available"]),
            ],
        )

    def stop(self):
        self._running = False
        self.inverter.stop()
        self.power_meter.stop()
        logger.info("[Coordinator] Stopped")

    def inject_scenario(
        self,
        pv: float,
        house: float,
        battery: float
    ):
        """
        Inject a specific scenario for testing.
        Both simulators update consistently.

        This is the golden dataset approach —
        inject known values, assert correct output.
        """
        from simulators.scenarios import EnergyState
        state = EnergyState(
            pv_production=pv,
            house_consumption=house,
            battery_power=battery
        )

        # Keep the golden dataset stable long enough for several gateway polls.
        self._manual_until = time.monotonic() + 5.0

        self.inverter.store.setValues(
            3, 0,
            [
                self.inverter._watts_to_raw(state.pv_production),
                self.inverter._watts_to_raw(state.inverter_ac_output),
                self.inverter._watts_to_raw(state.battery_power),
            ]
        )
        measurement_time = time.time()
        self.inverter.set_timestamp(measurement_time)

        grid_power = (
            state.house_consumption - state.inverter_ac_output
        )
        self.power_meter.set_grid_power(grid_power, measurement_time)

        logger.info(
            f"[Coordinator] Scenario injected → "
            f"PV={pv}W House={house}W Battery={battery}W "
            f"Grid={grid_power}W "
            f"House_calc={state.house_consumption}W"
        )

        return state, grid_power

    def inject_timestamped_scenario(
        self,
        pv: float,
        house: float,
        battery: float,
        timestamp: float,
    ):
        """Write one deterministic shared state with an explicit UTC time."""
        from simulators.scenarios import EnergyState

        state = EnergyState(pv, house, battery)
        self.inverter.store.setValues(
            3,
            0,
            [
                self.inverter._watts_to_raw(state.pv_production),
                self.inverter._watts_to_raw(state.inverter_ac_output),
                self.inverter._watts_to_raw(state.battery_power),
            ],
        )
        self.inverter.set_timestamp(timestamp)
        grid_power = state.house_consumption - state.inverter_ac_output
        self.power_meter.set_grid_power(grid_power, timestamp)
        return state, grid_power


if __name__ == "__main__":
    coordinator = SystemCoordinator()
    coordinator.start()

    print("\nBoth simulators running with shared state.")
    print("Start IoT Device in another terminal:")
    print("python -m iot_device.iot_device\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        coordinator.stop()
