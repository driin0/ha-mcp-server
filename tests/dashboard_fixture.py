"""A dashboard shaped like the one on casa where cards were replaced by
index and the neighbours were lost: a section that holds the washing
machine block AND a clock, a scheduler and a temperature stack."""
import copy

CASA = {
    "views": [{
        "title": "Casa", "path": "casa",
        "sections": [{
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Lavatrice"},
                {"type": "tile", "entity": "sensor.lavatrice"},
                {"type": "clock"},
                {"type": "custom:scheduler-card", "title": "Programmi"},
                {"type": "vertical-stack", "cards": [
                    {"type": "tile", "entity": "sensor.temperatura", "name": "Temp"},
                    {"type": "entities", "title": "Dettagli",
                     "entities": [{"entity": "sensor.lavatrice_run_state"},
                                  {"type": "section", "label": "Altro"},
                                  "sensor.lavatrice_remaining"]},
                ]},
            ],
        }, {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Energia"},
                {"type": "custom:button-card", "template": "wm_btn",
                 "entity": "switch.lavatrice",
                 "name": "[[[ return states['sensor.lavatrice'].state ]]]"},
            ],
        }],
        "badges": [{"type": "entity", "entity": "person.riccardo"}],
    }],
    "button_card_templates": {"wm_btn": {"show_icon": True}},
}


def install(fake_ha):
    fake_ha.lovelace["casa"] = copy.deepcopy(CASA)
    fake_ha.lovelace["yamlboard"] = {"views": []}
    fake_ha.lovelace["strat"] = {"strategy": {"type": "original-states"}}
    fake_ha.dashboards = [
        {"id": "casa", "url_path": "casa", "mode": "storage", "title": "Casa"},
        {"id": "yamlboard", "url_path": "yamlboard", "mode": "yaml", "title": "Y"},
        {"id": "strat", "url_path": "strat", "mode": "storage", "title": "S"},
    ]
    return fake_ha
