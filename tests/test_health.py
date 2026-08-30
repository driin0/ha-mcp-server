"""instance_health - one call for the state an instance is actually in.

The founding incident's diagnostic signature was every entity of one device
unavailable for weeks while its own ping still reported `on`: a live system
with a dead integration. Assembling it took five separate calls.
"""


def test_the_summary_counts_the_whole_population(fake_ha):
    from tools.health import instance_health

    fake_ha.states.extend([
        {"entity_id": "sensor.a", "state": "unavailable",
         "attributes": {}, "last_changed": "2026-08-01T00:00:00+00:00"},
        {"entity_id": "sensor.b", "state": "unknown",
         "attributes": {}, "last_changed": "2026-08-30T11:00:00+00:00"},
    ])
    fake_ha.registry.extend([
        {"entity_id": "sensor.a", "platform": "synology_dsm",
         "area_id": None, "device_id": None, "labels": []},
        {"entity_id": "sensor.b", "platform": "synology_dsm",
         "area_id": None, "device_id": None, "labels": []},
    ])

    result = instance_health()

    assert result["summary"]["unavailable"] >= 1
    assert result["summary"]["unknown"] >= 1


def test_the_counts_do_not_shrink_with_the_listing(fake_ha):
    """The property the whole tool turns on.

    A filter narrows what is shown. If it also narrowed what is counted,
    the tool would report "nothing found" about a population it had stopped
    looking at - which is the failure this module's neighbours exist to
    prevent.
    """
    from tools.health import instance_health

    fake_ha.states.append(
        {"entity_id": "sensor.recent", "state": "unavailable",
         "attributes": {}, "last_changed": "2026-08-30T11:59:00+00:00"})
    fake_ha.registry.append(
        {"entity_id": "sensor.recent", "platform": "fresh_integration",
         "area_id": None, "device_id": None, "labels": []})

    wide = instance_health(unavailable_hours=0)
    narrow = instance_health(unavailable_hours=100000)

    assert narrow["summary"] == wide["summary"]
    assert narrow["total"] < wide["total"]


def test_unavailable_entities_are_grouped_by_integration(fake_ha):
    """25 entities of one device down is one fault, not 25."""
    from tools.health import instance_health

    for n in range(25):
        fake_ha.states.append(
            {"entity_id": f"sensor.nas_{n}", "state": "unavailable",
             "attributes": {}, "last_changed": "2026-08-01T00:00:00+00:00"})
        fake_ha.registry.append(
            {"entity_id": f"sensor.nas_{n}", "platform": "synology_dsm",
             "area_id": None, "device_id": None, "labels": []})

    result = instance_health(unavailable_hours=0)

    groups = {g["platform"]: g for g in result["integrations"]}
    assert groups["synology_dsm"]["unavailable"] == 25


def test_the_duration_is_reported_as_a_lower_bound(fake_ha):
    """A Home Assistant restart resets last_changed, so the real duration
    can only ever be longer than what is measured here. Reporting it as the
    true duration would be wrong in one direction only - the direction that
    makes a fault look newer than it is."""
    from tools.health import instance_health

    fake_ha.states.append(
        {"entity_id": "sensor.old", "state": "unavailable",
         "attributes": {}, "last_changed": "2026-08-01T00:00:00+00:00"})
    fake_ha.registry.append(
        {"entity_id": "sensor.old", "platform": "synology_dsm",
         "area_id": None, "device_id": None, "labels": []})

    result = instance_health(unavailable_hours=0)

    assert result["hours_are_a_lower_bound"] is True
    group = next(g for g in result["integrations"] if g["platform"] == "synology_dsm")
    assert group["oldest_unavailable_hours"] > 0


def test_an_entity_with_no_readable_timestamp_is_never_hidden_by_the_filter(fake_ha):
    """Unknown duration is not zero duration.

    An entity whose last_changed cannot be read has an UNKNOWN age. Treating
    that as 0.0 would file it at the "just happened" end of the very ordering
    this tool exists to expose, and the duration filter would then hide it.
    """
    from tools.health import instance_health

    fake_ha.states.append(
        {"entity_id": "sensor.no_timestamp", "state": "unavailable",
         "attributes": {}})
    fake_ha.registry.append(
        {"entity_id": "sensor.no_timestamp", "platform": "mystery",
         "area_id": None, "device_id": None, "labels": []})

    result = instance_health(unavailable_hours=100000)

    platforms = [g["platform"] for g in result["integrations"]]
    assert "mystery" in platforms


def test_a_section_that_cannot_be_read_is_named_and_does_not_fail_the_report(fake_ha):
    """A health report missing a section must not look clean.

    The fake answers repairs/list_issues with unknown_command, which is the
    shape a Supervisor-proxied connection produces for a command it will
    not forward.
    """
    from tools.health import instance_health

    result = instance_health()

    assert "error" not in result
    assert "repairs" in result["sections_unavailable"]
    assert "incomplete" in result["note"].lower()


def test_it_returns_a_dict_on_a_healthy_instance(fake_ha):
    from tools.health import instance_health

    result = instance_health()

    assert isinstance(result, dict)
    assert result["summary"]["checked_entities"] > 0
    assert result["integrations"] == []


def test_the_core_snapshot_failing_is_an_error_not_a_clean_report(fake_ha):
    """Zero findings because nothing could be read is not zero findings."""
    import httpx

    from tools.health import instance_health

    fake_ha.raise_rest("/api/states", httpx.ConnectError("refused"))

    result = instance_health()

    assert result["error"] == "connection_failed"


def test_the_response_stays_bounded_on_a_real_sized_registry(fake_ha):
    """The 2.1.0 lesson, applied before it can happen again.

    list_orphan_entities shipped unbounded and answered a 3249-entity
    instance with 678 KB, past what an MCP client accepts - so the finding
    it had computed correctly never arrived. An aggregate over the same
    registry repeats that exactly unless every part of it is bounded,
    including the parts inside each row.
    """
    import json

    from tools.health import instance_health

    for n in range(3249):
        platform = f"integration_{n % 40}"
        fake_ha.states.append(
            {"entity_id": f"sensor.e{n}", "state": "unavailable",
             "attributes": {}, "last_changed": "2026-08-01T00:00:00+00:00"})
        fake_ha.registry.append(
            {"entity_id": f"sensor.e{n}", "platform": platform,
             "area_id": None, "device_id": None, "labels": []})

    result = instance_health(unavailable_hours=0)

    assert result["summary"]["unavailable"] == 3249
    assert result["returned"] <= 20
    assert len(json.dumps(result)) < 20_000


def test_an_integration_with_every_entity_down_is_flagged_and_ranked_first(fake_ha):
    """The signature, restated in the terms real data supports.

    "Unavailable for weeks" cannot be measured: last_changed resets on every
    Home Assistant restart, and measured on a real instance all 29
    integrations with unavailable entities reported the same 3.1 hours - the
    uptime. What survives is the RATIO: every entity of one integration down
    is an integration that is down, whatever the clock says.
    """
    from tools.health import instance_health

    for n in range(4):
        fake_ha.states.append(
            {"entity_id": f"sensor.nas_{n}", "state": "unavailable",
             "attributes": {}})
        fake_ha.registry.append(
            {"entity_id": f"sensor.nas_{n}", "platform": "synology_dsm",
             "area_id": None, "device_id": None, "labels": []})
    # one integration only partly down - a real signal, a lesser one
    fake_ha.states.append(
        {"entity_id": "sensor.partial", "state": "unavailable", "attributes": {}})
    fake_ha.states.append(
        {"entity_id": "sensor.partial_ok", "state": "on", "attributes": {}})
    for eid in ("sensor.partial", "sensor.partial_ok"):
        fake_ha.registry.append(
            {"entity_id": eid, "platform": "shelly",
             "area_id": None, "device_id": None, "labels": []})

    result = instance_health()

    assert result["integrations"][0]["platform"] == "synology_dsm"
    assert result["integrations"][0]["all_unavailable"] is True
    shelly = next(g for g in result["integrations"] if g["platform"] == "shelly")
    assert shelly["all_unavailable"] is False


def test_the_config_entry_state_is_joined_onto_the_integration(fake_ha):
    """The two halves of the diagnosis in one row.

    "Every entity down" says something is wrong; "and its config entry is in
    setup_retry" says what. Reading them from two separate sections is the
    five-calls-by-hand this tool replaces.
    """
    from tools.health import instance_health

    fake_ha.ws_responses["config_entries/get"] = {
        "id": 1, "type": "result", "success": True,
        "result": [{"entry_id": "e1", "domain": "synology_dsm",
                    "title": "NAS", "state": "setup_retry"}],
    }
    fake_ha.states.append(
        {"entity_id": "sensor.nas", "state": "unavailable", "attributes": {}})
    fake_ha.registry.append(
        {"entity_id": "sensor.nas", "platform": "synology_dsm",
         "area_id": None, "device_id": None, "labels": []})

    result = instance_health()

    nas = next(g for g in result["integrations"] if g["platform"] == "synology_dsm")
    assert nas["config_entry_state"] == "setup_retry"


def test_the_hours_filter_says_how_much_it_hid(fake_ha):
    """Measured on the real instance, a 24-hour default listed ZERO of 30
    integrations holding 1857 unavailable entities, under the envelope's own
    "no integrations found". A filter that hides everything must say so, the
    way list_orphan_entities' excluded_disabled does."""
    from tools.health import instance_health

    fake_ha.states.append(
        {"entity_id": "sensor.recent", "state": "unavailable",
         "attributes": {}, "last_changed": "2026-08-30T11:59:00+00:00"})
    fake_ha.registry.append(
        {"entity_id": "sensor.recent", "platform": "fresh",
         "area_id": None, "device_id": None, "labels": []})

    result = instance_health(unavailable_hours=100000)

    assert result["excluded_below_threshold"] == 1
    assert "100000" in result["note"]
    assert "not listed" in result["note"]
