#!/usr/bin/env python3
"""Mock minimo dell'API di Home Assistant, per gli screenshot della dashboard.

Serve solo i due endpoint che la dashboard interroga — /api/config e /api/states
— con dati inventati. Esiste perché uno screenshot preso da un'istanza vera
mostrerebbe il nome della località, il numero di entità e lo stato dell'allarme
di casa di qualcuno: la dashboard mette `location_name` nel titolo della pagina.

    python3 scripts/mock_ha.py [porta]        default 8123
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8123

CONFIG = {
    "version": "2026.8.1",
    "location_name": "Casa Esempio",
    "time_zone": "Europe/Rome",
}

# entity_count è len(states); lights_on conta i light.* accesi; alarm_state è il
# primo alarm_control_panel.*. Il resto serve solo a dare volume all'elenco.
def _states() -> list:
    out = [
        {"entity_id": "alarm_control_panel.casa", "state": "disarmed"},
        {"entity_id": "light.soggiorno", "state": "on"},
        {"entity_id": "light.cucina", "state": "on"},
        {"entity_id": "light.camera", "state": "off"},
        {"entity_id": "light.studio", "state": "on"},
        {"entity_id": "climate.termostato", "state": "heat"},
        {"entity_id": "cover.tapparella_salone", "state": "open"},
        {"entity_id": "media_player.soggiorno", "state": "playing"},
    ]
    # Riempitivo, per un conteggio di entità plausibile.
    out += [{"entity_id": f"sensor.esempio_{i}", "state": str(20 + i % 5)} for i in range(1, 235)]
    return out


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/config"):
            body = json.dumps(CONFIG).encode()
        elif self.path.startswith("/api/states"):
            body = json.dumps(_states()).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silenzio
        pass


if __name__ == "__main__":
    print(f"mock Home Assistant su http://127.0.0.1:{PORT} — dati inventati", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
