"""list_repairs - a repair you cannot act on is not a report.

Home Assistant puts the whole content of a repair in
`translation_placeholders`: which integration, which entities, which
blueprint, which token. Without it a caller gets `title: "dead_entities"`
and no way to know what is dead - measured on a real instance, answering
"are these 38 repairs real?" meant querying the WebSocket by hand because
the tool had dropped everything that mattered.
"""


def _issues(fake_ha, *rows):
    fake_ha.ws_responses["repairs/list_issues"] = {
        "id": 1, "type": "result", "success": True,
        "result": {"issues": list(rows)},
    }


def _issue(**kw):
    base = {"issue_id": "i1", "domain": "spook", "severity": "warning",
            "translation_key": "dead_entities", "ignored": False,
            "created": "2026-08-30T09:30:59+00:00", "is_fixable": False,
            "issue_domain": "ibeacon", "translation_placeholders": {}}
    base.update(kw)
    return base


def test_the_placeholders_reach_the_caller(fake_ha):
    from tools.system import list_repairs

    _issues(fake_ha, _issue(
        translation_key="unused_blueprints",
        translation_placeholders={"blueprint": "Manual Light",
                                  "domain": "automation"}))

    row = list_repairs()["repairs"][0]

    assert row["details"]["blueprint"] == "Manual Light"
    assert row["details"]["domain"] == "automation"


def test_a_long_list_is_bounded_and_says_how_long_it_was(fake_ha):
    """One real repair carried 1424 entity ids in a single placeholder.

    Passed through whole it is the 678 KB list_orphan_entities shipped in
    2.0.0, in a different field: an answer too large to deliver. The count
    is the finding; the ids are a sample to act on.
    """
    from tools.system import list_repairs

    listing = "\n".join(f"- `device_tracker.beacon_{n}`" for n in range(1424))
    _issues(fake_ha, _issue(
        translation_placeholders={"integration": "iBeacon Tracker",
                                  "entities": listing}))

    row = list_repairs()["repairs"][0]

    assert row["details"]["entities"]["count"] == 1424
    assert len(row["details"]["entities"]["sample"]) <= 10
    assert row["details"]["entities"]["sample"][0] == "device_tracker.beacon_0"
    assert row["details"]["integration"] == "iBeacon Tracker"


def test_a_short_placeholder_is_not_turned_into_a_sample(fake_ha):
    from tools.system import list_repairs

    _issues(fake_ha, _issue(
        translation_key="stale_access_tokens",
        translation_placeholders={"token": "VS Code", "owner": "Riccardo",
                                  "last_active": "2024-04-30"}))

    row = list_repairs()["repairs"][0]

    assert row["details"]["token"] == "VS Code"
    assert row["details"]["last_active"] == "2024-04-30"


def test_whether_home_assistant_offers_a_guided_fix_is_reported(fake_ha):
    """is_fixable decides whether the answer is 'click the repair' or
    'go and change something yourself'."""
    from tools.system import list_repairs

    _issues(fake_ha,
            _issue(issue_id="fixable", is_fixable=True),
            _issue(issue_id="manual", is_fixable=False))

    rows = {r["issue_id"]: r for r in list_repairs()["repairs"]}

    assert rows["fixable"]["is_fixable"] is True
    assert rows["manual"]["is_fixable"] is False


def test_the_integration_the_repair_is_about_is_reported(fake_ha):
    """`domain` is who RAISED the repair - spook raises most of them.
    `issue_domain` is what it is ABOUT, which is the actionable half."""
    from tools.system import list_repairs

    _issues(fake_ha, _issue(domain="spook", issue_domain="ibeacon"))

    row = list_repairs()["repairs"][0]

    assert row["domain"] == "spook"
    assert row["issue_domain"] == "ibeacon"


def test_ignored_repairs_are_still_left_out(fake_ha):
    from tools.system import list_repairs

    _issues(fake_ha,
            _issue(issue_id="visible", ignored=False),
            _issue(issue_id="hidden", ignored=True))

    ids = [r["issue_id"] for r in list_repairs()["repairs"]]

    assert ids == ["visible"]


def test_no_repairs_is_an_envelope_not_an_error(fake_ha):
    from tools.system import list_repairs

    _issues(fake_ha)

    result = list_repairs()

    assert result["total"] == 0
    assert result["repairs"] == []
    assert "error" not in result
