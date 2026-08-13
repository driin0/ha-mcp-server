#!/usr/bin/env python3
"""Genera gli screenshot della dashboard, dal mock e mai da un'istanza reale.

    pip install -r requirements-dev.txt && playwright install chromium
    python3 scripts/screenshots.py

Avvia il mock di Home Assistant, poi il server puntato lì, fotografa la
dashboard e chiude tutto. I PNG finiscono in docs/img/.
"""
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"

MOCK_PORT = 18123
MCP_PORT = 18821
UI_PORT = 18822

VIEWPORT = {"width": 1440, "height": 900}
SCALE = 2  # su schermi retina un PNG 1x si vede sfocato


def wait_for(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.4)
    raise TimeoutError(f"nessuna risposta da {url} entro {timeout:.0f}s")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("serve playwright: pip install -r requirements-dev.txt && playwright install chromium")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "HA_URL": f"http://127.0.0.1:{MOCK_PORT}",
        "HA_TOKEN": "mock",
        "MCP_PORT": str(MCP_PORT),
        "UI_PORT": str(UI_PORT),
        "MCP_ALLOW_NO_AUTH": "true",
    }

    mock = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mock_ha.py"), str(MOCK_PORT)])
    server = None
    try:
        wait_for(f"http://127.0.0.1:{MOCK_PORT}/api/config")
        server = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT, env=env)
        wait_for(f"http://127.0.0.1:{UI_PORT}/api/status")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport=VIEWPORT, device_scale_factor=SCALE,
                locale="it-IT", timezone_id="Europe/Rome",
            )
            page.goto(f"http://127.0.0.1:{UI_PORT}/", wait_until="networkidle")
            page.wait_for_timeout(800)

            # Il titolo contiene la località: se non è quella del mock, i dati
            # arrivano da altrove e lo screenshot non va prodotto.
            title = page.title()
            if "Casa Esempio" not in title:
                raise RuntimeError(f"titolo inatteso ({title!r}): il server non sta usando il mock")

            page.screenshot(path=str(OUT / "dashboard.png"), full_page=False)
            print(f"  ✓ dashboard.png   {title}")

            browser.close()
    finally:
        for proc in (server, mock):
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    print(f"\nScreenshot in {OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
