# simulators/coordinator.py

import time
import threading
import logging
import os
from simulators.scenarios import get_energy_state
from simulators.inverter_simulator import InverterSimulator
from simulators.power_meter_simulator import PowerMeterSimulator

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

    def __init__(self):
        self.inverter = InverterSimulator(
            host=os.getenv("SIMULATOR_HOST", "localhost"),
            port=int(os.getenv("INVERTER_PORT", "5020"))
        )
        self.power_meter = PowerMeterSimulator(
            host=os.getenv("SIMULATOR_HOST", "localhost"),
            port=int(os.getenv("POWERMETER_PORT", "5021"))
        )
        self.hour = 8.0
        self.battery_soc = 50.0
        self._running = False
        self._manual_until = 0.0

    def _update_loop(self):
        """
        Single source of truth for both simulators.
        Both devices read from the same energy state.
        """
        while self._running:
            if time.monotonic() < self._manual_until:
                measurement_time = time.time()
                self.inverter.set_timestamp(measurement_time)
                self.power_meter.set_timestamp(measurement_time)
                time.sleep(0.1)
                continue

            state = get_energy_state(
                hour=self.hour,
                battery_soc=self.battery_soc
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

            # Update battery SOC
            soc_change = (-state.battery_power / 10000) * 0.1
            self.battery_soc = max(
                0, min(100, self.battery_soc + soc_change)
            )

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
