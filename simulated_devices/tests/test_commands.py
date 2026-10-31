"""Command dedup / expiry / motor sequencing on the device side (no network)."""

from .conftest import Mono, flat_response, list_cmd, make_device


def statuses(dev, cid):
    return [a["status"] for a in dev.ack_log if a["command_id"] == cid]


def test_flat_control_dedup_and_clamp():
    d = make_device()
    d.handle_desired_control(flat_response("c1", 1, 40.0))
    d.handle_desired_control(flat_response("c1", 1, 40.0))   # re-served: no re-ack
    assert d.setpoint_c == 35.0
    assert statuses(d, "c1") == ["accepted"]
    ack = d.ack_log[0]
    assert ack["applied_setpoint_c"] == 35.0 and "clamped" in ack["detail"]


def test_flat_control_expired_not_applied():
    d = make_device()
    d.handle_desired_control(flat_response("c1", 1, 24.0, ttl=0))
    assert d.setpoint_c is None
    assert statuses(d, "c1") == ["expired"]


def test_stale_sequence_ignored_and_epoch_reset():
    d = make_device()
    d.handle_desired_control(flat_response("c5", 5, 24.0))
    d.handle_desired_control(flat_response("c4", 4, 30.0))
    assert d.setpoint_c == 24.0 and statuses(d, "c4") == []
    d.handle_desired_control(flat_response("c1", 1, 26.0, epoch="e2"))  # DB wiped
    assert d.setpoint_c == 26.0


def test_flat_motor_duration_and_ack_sequencing():
    clock = Mono()
    d = make_device(clock=clock)
    motor = {"command_id": "m1", "action": "run", "run": True, "duration_s": 3.0, "pattern": "nod",
             "sequence_number": 1, "ttl_s": 60, "expires_at_utc": None}
    d.handle_desired_control(flat_response(motor=motor))
    assert d.motor.running and statuses(d, "m1") == ["accepted"]
    for _ in range(2):
        clock.t += 1.0
        d.tick()
        assert d.build_telemetry()["motor_running"] is True
    assert statuses(d, "m1") == ["accepted"]           # not completed early
    clock.t += 1.0
    d.tick()
    assert not d.motor.running and statuses(d, "m1") == ["accepted", "completed"]
    d.handle_desired_control(flat_response(motor=motor))  # re-served: never re-run
    assert not d.motor.running and statuses(d, "m1") == ["accepted", "completed"]


def test_motor_clamped_to_local_max_and_expired_motor():
    clock = Mono()
    d = make_device(clock=clock)
    d.handle_desired_control(flat_response(motor={"command_id": "m1", "action": "run", "duration_s": 30,
                                                  "sequence_number": 1, "ttl_s": 60}))
    assert d.motor.current.duration_s == 10.0
    d.handle_desired_control(flat_response(motor={"command_id": "m2", "action": "run", "duration_s": 2,
                                                  "sequence_number": 2, "ttl_s": 0}))
    assert statuses(d, "m2") == ["expired"] and d.motor.current.command_id == "m1"


def test_flat_motor_stop_directive_acks_cancelled_once():
    clock = Mono()
    d = make_device(clock=clock)
    d.handle_desired_control(flat_response(motor={"command_id": "m1", "action": "run", "duration_s": 8,
                                                  "sequence_number": 1, "ttl_s": 60}))
    stop = {"command_id": "m1", "action": "stop", "run": False, "sequence_number": 1}
    d.handle_desired_control(flat_response(motor=stop))
    d.handle_desired_control(flat_response(motor=stop))
    assert not d.motor.running
    assert statuses(d, "m1") == ["accepted", "cancelled"]


def test_door_open_rejects_motor():
    d = make_device()
    d.door_override = True
    d.handle_desired_control(flat_response(motor={"command_id": "m1", "action": "run", "duration_s": 2,
                                                  "sequence_number": 1, "ttl_s": 60}))
    assert statuses(d, "m1") == ["rejected"] and not d.motor.running


def test_command_list_actions_and_dedup_with_flat():
    clock = Mono()
    d = make_device(clock=clock)
    cmds = [list_cmd("s1", "set_setpoint", 10, {"setpoint_c": 25.5, "control_mode": "auto"}),
            list_cmd("m1", "animate_motor", 11, {"duration_ms": 2000, "pattern": "spin"}),
            list_cmd("n1", "noop", 12),
            list_cmd("i1", "capture_image", 13)]
    # the same motor command also appears in the flat v1.0 directive -> executed once
    flat_motor = {"command_id": "m1", "action": "run", "duration_s": 2.0, "sequence_number": 3,
                  "ttl_s": 60}
    d.handle_desired_control(flat_response("c1", 1, 24.0, motor=flat_motor, commands=cmds))
    d.handle_desired_control(flat_response("c1", 1, 24.0, motor=flat_motor, commands=cmds))
    assert statuses(d, "s1") == ["accepted"]  # standing setpoint: accepted only
    assert statuses(d, "m1") == ["accepted"] and d.motor.runs == 1
    assert statuses(d, "n1") == ["accepted", "completed"]
    assert statuses(d, "i1") == ["accepted"] and d._image_requests == ["i1"]
    # the flat control command (processed after the list) is the setpoint finally in force
    assert d.setpoint_c == 24.0 and statuses(d, "c1") == ["accepted"]
    clock.t += 2.0
    d.tick()
    assert statuses(d, "m1") == ["accepted", "completed"]


def test_command_list_expiry_relative_to_server_time():
    d = make_device()
    cmds = [list_cmd("old", "set_setpoint", 1, {"setpoint_c": 30.0}, expires_in_s=-5),
            list_cmd("m", "animate_motor", 2, {"duration_ms": 1000}, expires_in_s=0)]
    resp = flat_response(commands=cmds)
    resp.pop("command_id")
    d.handle_desired_control(resp)
    assert statuses(d, "old") == ["expired"] and statuses(d, "m") == ["expired"]
    assert d.setpoint_c is None and not d.motor.running


def test_command_list_stale_sequence_ignored():
    d = make_device()
    d.handle_desired_control({**flat_response(commands=[list_cmd("a", "noop", 5)]), "command_id": None})
    d.handle_desired_control({**flat_response(commands=[list_cmd("b", "noop", 3)]), "command_id": None})
    assert statuses(d, "a") == ["accepted", "completed"] and statuses(d, "b") == []


def test_stop_motor_command_cancels_running_motor():
    clock = Mono()
    d = make_device(clock=clock)
    base = {**flat_response(), "command_id": None}
    d.handle_desired_control({**base, "commands": [list_cmd("m", "animate_motor", 1, {"duration_ms": 9000})]})
    clock.t += 1.0
    d.tick()
    d.handle_desired_control({**base, "commands": [
        list_cmd("x", "stop_motor", 2, {"target_command_id": "m"})]})
    assert not d.motor.running
    assert statuses(d, "m") == ["accepted", "cancelled"]
    assert statuses(d, "x") == ["accepted", "completed"]


def test_door_opening_stops_running_motor():
    clock = Mono()
    d = make_device(clock=clock)
    d.handle_desired_control(flat_response(motor={"command_id": "m1", "action": "run", "duration_s": 8,
                                                  "sequence_number": 1, "ttl_s": 60}))
    d.door_override = True
    clock.t += 1.0
    d.tick()
    assert not d.motor.running and statuses(d, "m1") == ["accepted", "failed"]


def test_scripted_door_events():
    clock = Mono()
    d = make_device(clock=clock, door_events=[(10.0, 5.0)])
    seen = []
    for _ in range(20):
        clock.t += 1.0
        d.tick()
        seen.append(d.door_open)
    assert seen[:9] == [False] * 9 and seen[9:14] == [True] * 5 and not any(seen[14:])
