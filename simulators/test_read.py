# simulators/test_read.py
# Quick manual test — reads from the inverter simulator

import time
from pymodbus.client import ModbusTcpClient

SCALING_FACTOR = 10

# test_read.py

def raw_to_watts(raw: int, signed: bool = False) -> float:
    """
    Convert raw Modbus register to Watts.

    signed=True  → register can be negative (battery power)
    signed=False → register always positive (PV, AC output)
    """
    if signed and raw > 32767:
        raw = raw - 65536
    return raw / SCALING_FACTOR

def read_inverter(host="localhost", port=5020):
    client = ModbusTcpClient(host=host, port=port)
    client.connect()

    result = client.read_holding_registers(
        address=0,
        count=3,
        slave=1
    )

    if result.isError():
        print(f"Error reading registers: {result}")
        return

    regs = result.registers

    # PV and AC are always positive → signed=False
    # Battery can be negative → signed=True
    pv      = raw_to_watts(regs[0], signed=False)
    ac      = raw_to_watts(regs[1], signed=False)
    battery = raw_to_watts(regs[2], signed=True)

    print(f"PV Production:  {pv:.1f} W")
    print(f"AC Output:      {ac:.1f} W")
    print(f"Battery Power:  {battery:.1f} W "
          f"({'charging' if battery > 0 else 'discharging' if battery < 0 else 'idle'})")

    client.close()


if __name__ == "__main__":
    print("Reading from Inverter Simulator every 2 seconds...")
    print("Make sure inverter_simulator.py is running first.\n")

    while True:
        read_inverter()
        print("---")
        time.sleep(2)