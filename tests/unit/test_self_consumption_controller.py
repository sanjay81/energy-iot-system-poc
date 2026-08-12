from iot_device.calculator import EnergyMeasurement
from iot_device.self_consumption_controller import SelfConsumptionController


class RecordingStep4:
    def __init__(self, status="accepted", actual_power_w=None, reason=None):
        self.commands = []
        self.status = status
        self.actual_power_w = actual_power_w
        self.reason = reason

    def __call__(self, payload, now=None):
        self.commands.append(payload)
        return {
            "status": self.status,
            "actual_power_w": (
                payload["requested_power_w"]
                if self.actual_power_w is None
                else self.actual_power_w
            ),
            "rejection_reason": self.reason,
        }


def measurement(timestamp=1000, pv=3000, house=1000):
    return EnergyMeasurement(
        pv_production=pv,
        ac_output=pv,
        battery_power=0,
        grid_power=house - pv,
        house_consumption=house,
        inverter_timestamp=timestamp,
        powermeter_timestamp=timestamp,
    )


def controller(handler, **kwargs):
    return SelfConsumptionController(
        "home-1", handler, mode="automatic", **kwargs
    )


def test_surplus_requests_charge_through_step4():
    step4 = RecordingStep4()
    decision = controller(step4).evaluate(measurement(), now=1000)

    assert decision.desired_power_w == -2000
    assert decision.command_status == "accepted"
    assert step4.commands[0]["requested_power_w"] == -2000


def test_deficit_requests_discharge_and_controller_limit_applies():
    step4 = RecordingStep4()
    decision = controller(step4, max_power_w=1500).evaluate(
        measurement(pv=0, house=2500), now=1000
    )

    assert decision.desired_power_w == 1500


def test_balanced_or_stale_measurement_requests_idle():
    balanced = RecordingStep4()
    stale = RecordingStep4()

    assert controller(balanced).evaluate(
        measurement(pv=1000, house=1025), now=1000
    ).desired_power_w == 0
    decision = controller(stale).evaluate(measurement(), now=1100)
    assert decision.reason == "stale_measurement"
    assert stale.commands[0]["requested_power_w"] == 0


def test_manual_mode_never_commands_battery():
    step4 = RecordingStep4()
    policy = SelfConsumptionController("home-1", step4, mode="manual")

    decision = policy.evaluate(measurement(), now=1000)

    assert decision.reason == "manual_mode"
    assert step4.commands == []


def test_duplicate_and_unchanged_measurements_do_not_repeat_commands():
    step4 = RecordingStep4()
    policy = controller(step4)
    first = measurement()
    policy.evaluate(first, now=1000)

    duplicate = policy.evaluate(first, now=1000)
    unchanged = policy.evaluate(measurement(timestamp=1005), now=1005)

    assert duplicate.reason == "duplicate_or_out_of_order"
    assert unchanged.status == "unchanged"
    assert len(step4.commands) == 1


def test_step4_rejection_and_actual_power_are_reported_not_overridden():
    step4 = RecordingStep4(
        status="rejected", actual_power_w=0, reason="maximum_soc_reached"
    )

    decision = controller(step4).evaluate(measurement(), now=1000)

    assert decision.desired_power_w == -2000
    assert decision.command_status == "rejected"
    assert decision.actual_power_w == 0
    assert decision.rejection_reason == "maximum_soc_reached"


def test_restart_does_not_repeat_last_command(tmp_path):
    state = tmp_path / "controller.json"
    first_step4 = RecordingStep4()
    first = controller(first_step4, state_file=str(state))
    first.evaluate(measurement(), now=1000)

    restored_step4 = RecordingStep4()
    restored = controller(restored_step4, state_file=str(state))
    decision = restored.evaluate(measurement(timestamp=1005), now=1005)

    assert decision.status == "unchanged"
    assert restored_step4.commands == []


def test_measurement_loss_requests_idle_once():
    step4 = RecordingStep4()
    policy = controller(step4)
    policy.evaluate(measurement(), now=1000)

    decision = policy.fail_safe(now=1001)
    repeated = policy.fail_safe(now=1002)

    assert decision.status == "fail_safe_commanded"
    assert step4.commands[-1]["requested_power_w"] == 0
    assert repeated.status == "unchanged"
    assert len(step4.commands) == 2
