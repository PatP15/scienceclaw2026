from simulated_devices.limits import LIMITS, load_local_limits
from simulated_devices.virtual_thermal_plant import ThermalChamber

from .conftest import Mono, flat_response, make_device


def run(ch: ThermalChamber, seconds: int, setpoint, *, door=False, t0=0.0, offline=False):
    t = t0
    for _ in range(seconds):
        t += 1.0
        ch.step(t, 1.0, setpoint_c=setpoint, door_open=door, offline=offline)
    return t


def test_converges_toward_setpoint_heating_and_cooling():
    for sp in (27.0, 15.0):
        ch = ThermalChamber(seed=3)
        start_err = abs(ch.plant.chamber_c - sp)
        run(ch, 2 * 3600, sp)
        assert abs(ch.plant.chamber_c - sp) < 0.5, (sp, ch.plant.chamber_c)
        assert abs(ch.plant.starter_c - sp) < 0.8          # starter lags but follows
        assert abs(ch.plant.chamber_c - sp) < start_err
        assert 0.0 <= ch.controller.output <= 1.0


def test_output_bounded_and_ramped():
    ch = ThermalChamber(seed=1)
    ch.step(1.0, 1.0, setpoint_c=35.0, door_open=False, offline=False)
    assert ch.controller.output <= 0.05 + 1e-9            # slew-limited, not stepped
    run(ch, 600, 35.0, t0=1.0)
    assert ch.controller.mode == "heat" and ch.controller.output <= 1.0


def test_dead_time_on_polarity_reversal():
    ch = ThermalChamber(seed=1)
    t = run(ch, 1800, 27.0)
    assert ch.controller.mode == "heat"
    modes = []
    for _ in range(120):
        t += 1.0
        ch.step(t, 1.0, setpoint_c=15.0, door_open=False, offline=False)
        modes.append(ch.controller.mode)
    assert modes[0] == "switching"
    assert "cool" in modes
    first_cool = modes.index("cool")
    assert first_cool >= int(LIMITS.heat_cool_dead_time_s) - 1
    assert all(m == "switching" for m in modes[:first_cool])


def test_door_open_idles_peltier_and_perturbs_temperature():
    ch = ThermalChamber(seed=1)
    t = run(ch, 3600, 27.0)
    before = ch.plant.chamber_c
    run(ch, 120, 27.0, door=True, t0=t)
    assert ch.controller.mode == "idle" and ch.controller.output == 0.0
    assert ch.plant.chamber_c < before - 1.5            # pulled toward the ~21 C room


def test_boot_off_and_offline_hold():
    ch = ThermalChamber(seed=1)
    ch.step(1.0, 1.0, setpoint_c=None, door_open=False, offline=True)
    assert ch.controller.mode == "off"
    ch.step(2.0, 1.0, setpoint_c=27.0, door_open=False, offline=True)
    assert ch.controller.mode == "idle"


def test_local_limits_identical_to_hardware_yaml():
    import yaml

    from simulated_devices.limits import HARDWARE_YAML

    raw = yaml.safe_load(HARDWARE_YAML.read_text())["firmware_local_limits"]
    lim = load_local_limits()
    for k, v in raw.items():
        assert getattr(lim, k) == float(v), k
    assert lim.clamp_setpoint(40.0) == raw["setpoint_max_c"]
    assert lim.clamp_setpoint(-5.0) == raw["setpoint_min_c"]


def test_independent_state_per_chamber():
    clock = Mono()
    a, b = make_device("A", clock=clock), make_device("B", clock=clock)
    a.handle_desired_control(flat_response("a1", 1, 30.0))
    b.handle_desired_control(flat_response("b1", 1, 12.0))
    for _ in range(1700):  # < offline_hold_s (1800 s) since the single command
        clock.t += 1.0
        a.tick()
        b.tick()
    assert a.setpoint_c == 30.0 and b.setpoint_c == 12.0
    assert a.thermal.plant is not b.thermal.plant
    assert a.thermal.controller.mode == "heat" and b.thermal.controller.mode == "cool"
    assert a.thermal.plant.chamber_c > 27.0
    assert b.thermal.plant.chamber_c < 15.0
    # a door event on A does not touch B
    a.door_override = True
    tb = b.thermal.plant.chamber_c
    clock.t += 1.0
    a.tick()
    b.tick()
    assert a.thermal.controller.mode == "idle" and b.thermal.controller.mode == "cool"
    assert abs(b.thermal.plant.chamber_c - tb) < 0.1
    assert a.boot_id != b.boot_id and a.seen is not b.seen


def test_overtemp_and_heatsink_faults_latch_then_clear():
    from simulated_devices.virtual_thermal_plant import PeltierController

    c = PeltierController()
    kw = dict(setpoint_c=30.0, heatsink_c=30.0, door_open=False, offline=False)
    c.update(0.0, 1.0, chamber_c=25.0, **kw)
    assert c.mode == "heat"
    c.update(1.0, 1.0, chamber_c=39.0, **kw)
    assert c.mode == "fault" and c.output == 0.0 and "overtemp" in c.fault_codes
    c.update(2.0, 1.0, chamber_c=30.0, **kw)
    assert c.mode == "fault"                      # latched
    c.update(63.0, 1.0, chamber_c=30.0, **kw)
    assert c.fault_codes == [] and c.mode != "fault"
    c.update(64.0, 1.0, chamber_c=25.0, **{**kw, "heatsink_c": 61.0})
    assert c.mode == "fault" and "heatsink_overtemp" in c.fault_codes
