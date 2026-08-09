# Energy IoT System POC — Phase 1
## Inverter Simulator & Unit Test Suite

> **Context:** This proof of concept is a software replica of a decentralized Energy IoT System architecture. It demonstrates a comprehensive test strategy for energy monitoring — built entirely in Python, with no physical hardware required.

---

## Architecture Context

The full system architecture has six interfaces:

```
Battery      ──CAN──────────────> Inverter
Inverter     ──Modbus RTU──────> IoT Device
Power Meter  ──Modbus RTU──────> IoT Device
Power Meter  ──Modbus TCP──────> IoT Device (via Switch)
CT Clamps    ──Physical────────> Power Meter
IoT Device   ──MQTT/Ethernet──> Cloud / Dashboard
```

**Phase 1 covers Interface 2 — Inverter ↔ IoT via Modbus RTU.**

The Inverter exposes three critical measurements via Modbus registers:

| Register | Address | Measurement | Type | Scaling |
|---|---|---|---|---|
| 40001 | 0 | PV Production (W) | Unsigned | ÷ 10 |
| 40002 | 1 | AC Output (W) | Unsigned | ÷ 10 |
| 40003 | 2 | Battery Power (W) | Signed | ÷ 10 |

> Positive battery = charging. Negative battery = discharging.

---

## The Most Critical Calculation

House Consumption is **never directly measured** — it is always derived:

```
House Consumption = Inverter AC Output + Grid Power
AC Output         = PV Production − Battery Power
```

Grid Power uses this sign convention: positive means importing from the
grid; negative means exporting to the grid.

Any error in AC Output silently corrupts House Consumption. This is why register decoding correctness is a first-class test concern.

---

## What Phase 1 Builds

```
energy-iot-system-poc/
├── simulators/
│   ├── scenarios.py              ← Energy state model + day simulation
│   └── inverter_simulator.py     ← Modbus TCP server (Inverter replica)
├── tests/
│   └── unit/
│       └── test_calculator.py    ← 21 unit tests
├── conftest.py
└── requirements.txt
```

---

## Setup

```bash
git clone <your-repo>
cd energy-iot-system-poc
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Running the Simulator

The `IoTDevice` accepts an optional publisher adapter. Tests use an in-memory
publisher, so no MQTT broker is required for unit or Modbus integration tests.
The command-line entry point explicitly enables the real MQTT publisher.

**Terminal 1 — Start the Inverter Simulator:**
```bash
python -m simulators.inverter_simulator
```

Expected output:
```
[Inverter] Hour=8.0 | PV=3027.1W | AC=2002.5W | Battery=1024.6W | SOC=21%
[Inverter] Hour=8.1 | PV=3156.2W | AC=2204.1W | Battery=952.1W  | SOC=22%
```

**Terminal 2 — Read live values:**
```bash
python simulators/test_read.py
```

Expected output:
```
PV Production:  3027.1 W
AC Output:      2002.5 W
Battery Power:  1024.6 W (charging)
---
PV Production:  1381.0 W
AC Output:      2334.2 W
Battery Power:  -953.1 W (discharging)
```

---

## Running the Tests

```bash
pytest tests/unit/test_calculator.py -v
```

Expected output:
```
tests/unit/test_calculator.py::TestInverterACOutput::test_ac_output_when_battery_charging PASSED
tests/unit/test_calculator.py::TestInverterACOutput::test_ac_output_when_battery_discharging PASSED
tests/unit/test_calculator.py::TestInverterACOutput::test_ac_output_never_negative PASSED
tests/unit/test_calculator.py::TestInverterACOutput::test_ac_output_at_night PASSED
tests/unit/test_calculator.py::TestInverterACOutput::test_ac_output_all_zero PASSED
tests/unit/test_calculator.py::TestGridPower::test_grid_importing_when_pv_insufficient PASSED
tests/unit/test_calculator.py::TestGridPower::test_grid_exporting_when_pv_surplus PASSED
tests/unit/test_calculator.py::TestGridPower::test_grid_zero_when_perfectly_balanced PASSED
tests/unit/test_calculator.py::TestGridPower::test_grid_when_battery_discharging_at_night PASSED
...
21 passed in 0.42s
```

---

## Bugs Found in Phase 1

### Bug 1 — Negative AC Output when battery charging exceeds PV

**Symptom:**
```
PV Production:  -1501.9 W   ← impossible value
AC Output:       2051.7 W
Battery Power:   3000.0 W (charging)
```

**Root cause:**
```python
# When battery charging rate > PV production
AC Output = PV - Battery = 1500 - 3000 = -1500W  ← impossible
```

An inverter cannot produce negative AC power. It has no energy source to draw from when PV is insufficient to cover battery charging.

**Architecture connection:**

This is a **Silent Failure on Interface 2 (Inverter ↔ IoT via Modbus RTU).** In a real system the Inverter firmware would write an incorrect value to register 40002. The IoT device reads it faithfully. Cloud receives it. Dashboard displays impossible negative AC output. No error raised anywhere in the pipeline.

**Fix:**
```python
# scenarios.py — EnergyState.inverter_ac_output
@property
def inverter_ac_output(self) -> float:
    output = self.pv_production - self.battery_power
    return max(0.0, output)  # physical constraint enforced

# scenarios.py — get_battery_power
# Only charge when PV is actually producing
if surplus > 0 and battery_soc < 95 and pv > 0:
```

**Test that catches it:**
```python
def test_ac_output_never_negative(self):
    state = EnergyState(
        pv_production=1000,
        house_consumption=2000,
        battery_power=3000   # charging more than PV produces
    )
    assert state.inverter_ac_output >= 0
```

---

### Bug 2 — Large positive PV values decoded as negative

**Symptom:**
```
PV Production:  -762.5 W   ← impossible, sunny afternoon
AC Output:      2791.1 W
Battery Power:  3000.0 W (charging)
```

**Root cause:**
```python
# raw_to_watts applied two's complement to ALL registers
def raw_to_watts(raw: int) -> float:
    if raw > 32767:          # ← wrong: triggers for large positive PV
        raw = raw - 65536
    return raw / SCALING_FACTOR

# Trace:
# PV = 5791.1W → raw = 57911
# 57911 > 32767 → True
# raw = 57911 - 65536 = -7625
# -7625 / 10 = -762.5W  ← silent failure
```

Two's complement is only valid for **signed registers** (Battery Power). PV Production and AC Output are **unsigned** — always positive. The same decoding function cannot be used for all registers.

**Architecture connection:**

This is the most common silent failure in real Modbus implementations. Inverter manufacturers document registers as signed or unsigned in their protocol specification. If the IoT developer applies the wrong decoder — every value above 3276.7W (raw 32767) reports as negative. In production this means any home with a solar system producing more than ~3.3kW sees negative PV production on the dashboard. No error raised. No alert fired.

**Fix:**
```python
def raw_to_watts(raw: int, signed: bool = False) -> float:
    """
    signed=True  → Battery Power (can be negative)
    signed=False → PV Production, AC Output (always positive)
    """
    if signed and raw > 32767:
        raw = raw - 65536
    return raw / SCALING_FACTOR

# Usage:
pv      = raw_to_watts(regs[0], signed=False)  # always positive
ac      = raw_to_watts(regs[1], signed=False)  # always positive
battery = raw_to_watts(regs[2], signed=True)   # can be negative
```

**Test that catches it:**
```python
def test_pv_large_value_decodes_correctly(self):
    """
    PV = 5791W produces raw = 57911
    57911 > 32767 but it is NOT negative
    Must NOT apply two's complement to PV register
    """
    pv_watts = 5791.0
    raw = self.watts_to_raw(pv_watts)
    decoded = self.raw_to_watts_unsigned(raw)
    assert decoded == pytest.approx(pv_watts, abs=0.1)
```

---

## Key Learnings

### Learning 1 — Register map is a contract

Every Modbus register has a defined:
- Address
- Data type (signed / unsigned)
- Scaling factor
- Unit (W, V, A, %)

Any mismatch between what the device writes and what the IoT reads is a **silent failure.** The IoT receives a valid integer — it just means something completely different. This contract must be version-controlled and tested against every firmware combination.

---

### Learning 2 — Physical constraints must be enforced in code

| Constraint | Enforcement |
|---|---|
| AC Output cannot be negative | `max(0.0, output)` |
| Battery cannot charge from nothing | `pv > 0` check |
| PV cannot be negative | Clamped in scenario generator |
| Battery rate limited to 3000W | `min/max` in strategy |

Every physical constraint not enforced in code is a potential silent failure. The IoT device should also validate received register values against physical bounds — if it receives a negative PV value it should raise a fault, not forward it to the cloud.

---

### Learning 3 — Silent failures require reference values to detect

Both bugs were found by the same method:

```
Step 1 → Observe unexpected output
Step 2 → Calculate what the value SHOULD be independently
Step 3 → Compare expected vs reported
Step 4 → Mismatch reveals the bug
```

This is the **golden dataset principle.** In the full system test we inject a known value at the simulator and assert the exact value appears at the cloud. The unit tests automate this at the component level.

---

### Learning 4 — Unit tests are the cheapest safety net

Both bugs were pure logic errors:
- No hardware involved
- No protocol involved
- No network involved

A unit test catches them in milliseconds. Without unit tests:
- Bug 1 might appear in production when a large battery is installed
- Bug 2 would appear on any home with PV production above 3.3kW

Both would generate support tickets, field engineer visits, and customer trust damage — for bugs that a 5-second unit test prevents.

---

## Test Coverage — Phase 1

| Test Class | Tests | What it covers |
|---|---|---|
| TestInverterACOutput | 5 | AC Output formula, physical constraints |
| TestGridPower | 4 | All 4 energy flow quadrants, sign convention |
| TestRegisterEncoding | 5 | Signed vs unsigned, scaling factor, Bug 2 |
| TestBatteryStrategy | 7 | Battery logic, Bug 1, boundary conditions |
| **Total** | **21** | **All passing, < 1 second** |

---

## Interview Story — Phase 1

> *"I built an Inverter Simulator using pymodbus that exposes three Modbus registers — PV Production, AC Output, and Battery Power — with realistic energy scenarios simulating a full day. While building it I found two bugs: first, AC Output going negative when battery charging exceeded PV production — a physical impossibility that would appear as a silent failure in production. Second, large positive PV values being decoded as negative due to incorrect two's complement applied to unsigned registers — a real Modbus implementation bug that would report negative PV production on sunny afternoons with no error raised anywhere in the pipeline. I wrote 21 unit tests covering both bugs, all four energy flow quadrants, and boundary conditions. These now run in under a second and catch both bugs automatically on any future code change."*

---

## What Comes Next — Phase 2

```
Phase 2 → Power Meter Simulator
           Modbus TCP server exposing Grid Power register
           IoT Device reads both Inverter and Power Meter
           House Consumption calculated and verified
           Integration tests — timing gap between reads
           Fault injection — silent failure detection
```

---

*Energy IoT System proof of concept for architecture and test automation.*
*Architecture: IoT · Modbus RTU · Modbus TCP · CAN · MQTT · HiL Testing*
