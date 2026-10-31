from simulated_devices.cli import DEMO_RESTAURANTS, build_parser, make_configs


def test_demo_restaurants_profile():
    args = build_parser().parse_args(["--profile", "demo-restaurants"])
    cfgs = make_configs(args)
    assert [c.chamber_id for c in cfgs] == ["C", "D", "E", "F"]
    assert [c.device_id for c in cfgs] == [f"fermentai-sim-{x}" for x in "cdef"]
    assert len({c.seed for c in cfgs}) == 4 and len({c.room_rh_percent for c in cfgs}) == 4
    assert all(c.door_events for c in cfgs) and all(not c.has_ph_probe for c in cfgs)
    assert [c.plant.ambient_c for c in cfgs] == [DEMO_RESTAURANTS[k]["ambient_c"] for k in "CDEF"]


def test_explicit_chambers_cdef():
    args = build_parser().parse_args(["--chambers", "C", "D", "E", "F"])
    assert [c.device_id for c in make_configs(args)] == [f"fermentai-sim-{x}" for x in "cdef"]
