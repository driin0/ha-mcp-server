import colorsys

import httpx

from tools._base import mcp, HA_URL, HEADERS, entity_area_map, envelope, error, observe_actuation


def _rgb_to_hs(rgb: list) -> tuple:
    """Hue (0-360) and saturation (0-100) of an [R, G, B] triple.

    Home Assistant does not store an rgb_color as such for a light whose
    native color mode is 'hs' (color_temp/hs lights, the common case): it
    keeps only hue and saturation, and derives rgb_color back from them at
    full value (V=1.0) for display — value/brightness is a separate
    attribute. Measured live: requesting rgb_color=[10, 20, 30] on
    light.ceiling_lights (modes ['color_temp', 'hs']) reads back rgb_color
    [85, 170, 255] — the same hue/saturation at full brightness, not the
    value we sent. A light whose native mode already stores full-range
    color (rgbw/rgbww) does round-trip rgb_color exactly, but comparing
    hue/saturation instead works for both: it is what the entity's own
    hs_color attribute reports either way, so this is the one comparison
    that does not depend on which color mode the target actually uses.
    """
    r, g, b = rgb
    h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return h * 360, s * 100


def _light_matches(s: dict, requested: dict) -> bool:
    """True when every field in `requested` is reflected in state `s`.

    requested may hold any of brightness_pct, color_temp_k, rgb_color,
    effect — whichever set_light() was actually asked to change. Each is
    checked against the specific attribute Home Assistant reports it
    under; a light that does not support one (no color_temp mode, no
    effect_list) simply will not show the requested value there, so this
    returns False for it the same as a real mismatch — see set_light()'s
    docstring for why that is the honest outcome rather than a distinct
    "unsupported" state: the observed value returned alongside `verified`
    already shows a caller what happened (unchanged, or converted into a
    different color mode).
    """
    if s["state"] != "on":
        return False
    attrs = s.get("attributes", {})
    if "brightness_pct" in requested:
        brightness = attrs.get("brightness")
        observed_pct = round(brightness / 2.55) if brightness is not None else None
        if observed_pct != requested["brightness_pct"]:
            return False
    if "color_temp_k" in requested:
        if attrs.get("color_temp_kelvin") != requested["color_temp_k"]:
            return False
    if "rgb_color" in requested:
        want_h, want_s = _rgb_to_hs(requested["rgb_color"])
        got = attrs.get("hs_color")
        if got is None or abs(got[0] - want_h) > 0.5 or abs(got[1] - want_s) > 0.5:
            return False
    if "effect" in requested:
        if attrs.get("effect") != requested["effect"]:
            return False
    return True


@mcp.tool()
def list_lights(area_id: str = "", search: str = "", state: str = "") -> dict:
    """
    List all light entities with their current state, brightness and color.

    area_id: filter by area_id (use list_areas() to find IDs)
    search:  optional substring filter on entity_id or friendly name (case-insensitive)
    state:   filter by exact state — 'on', 'off', 'unavailable'

    Returns: {total, returned, offset, note?, lights: [...]}

    ⚠️ third-party-settable: `name` is an entity's `friendly_name`, settable
    by any integration that names its own entities - see tools/_base.py's
    "Third-party-settable fields" note.
    """
    area_map = {}
    if area_id:
        # area is entity-registry metadata, not a state attribute - Home
        # Assistant never puts it on states, so the filter has to join the
        # registry the same way search_entities() and get_energy_summary()
        # do. A light's area comes from its own area_id when set and
        # otherwise from its device, so both registries are needed - see
        # entity_area_map().
        area_map, err = entity_area_map()
        if err:
            return err

    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    lights = []
    for s in r.json():
        if not s["entity_id"].startswith("light."):
            continue
        attrs = s.get("attributes", {})
        if area_id and area_map.get(s["entity_id"]) != area_id:
            continue
        if state and s["state"] != state:
            continue
        name = attrs.get("friendly_name", s["entity_id"])
        if search and search.lower() not in s["entity_id"].lower() and search.lower() not in name.lower():
            continue
        lights.append({
            "entity_id": s["entity_id"],
            "name": name,
            "state": s["state"],
            "brightness_pct": round(attrs["brightness"] / 2.55) if attrs.get("brightness") is not None else None,
            "color_temp_k": attrs.get("color_temp_kelvin"),
            "rgb_color": attrs.get("rgb_color"),
            "color_mode": attrs.get("color_mode"),
            "supported_color_modes": attrs.get("supported_color_modes", []),
        })
    return envelope(sorted(lights, key=lambda x: x["name"]), key="lights")


@mcp.tool()
def set_light(
    entity_id: str,
    state: str = "",
    brightness_pct: int = None,
    color_temp_k: int = None,
    rgb_color: list = None,
    effect: str = "",
    transition: int = None,
) -> dict:
    """
    Control a light entity.

    state: 'on' | 'off' | 'toggle' | '' (empty defaults to 'on' — apply the given attributes)
    brightness_pct: 0–100
    color_temp_k: color temperature in Kelvin (e.g. 2700 warm, 4000 neutral, 6500 cool)
    rgb_color: [R, G, B] list, e.g. [255, 100, 0]
    effect: named effect (e.g. 'Night', 'Day', 'Candle', 'Twinkle') — see entity's effect_list
    transition: fade duration in seconds

    Returns: {entity_id, state, verified, observed_state, ...} on a call
    Home Assistant accepted, or {error: "entity_not_found"/"invalid_state",
    ...} otherwise. An unrecognised `state` (anything other than '', 'on',
    'off' or 'toggle') is rejected rather than silently treated as 'on'.

    `verified` is true only when the light's own state, read back after the
    call, matches — "on" for a turn_on/attribute call, "off" for 'off', and
    (since 'toggle' has no fixed target) any state different from what the
    light reported just before the call, for 'toggle'. For a turn_on call
    that also requests brightness_pct/color_temp_k/rgb_color/effect,
    `verified` covers those too: true only when every attribute actually
    requested is reflected in the read-back, not just the on/off state — a
    light that ignores an unsupported effect, or converts a color request
    into a mode it does not support, used to still report `verified: true`
    because only state=="on" was checked. Each requested attribute is
    echoed back under its own key (e.g. `brightness_pct`, `color_temp_k`,
    `rgb_color`, `effect`) with what was actually observed — not what was
    asked for — so a caller can see exactly what did or did not take
    effect. brightness_pct is compared via Home Assistant's own
    brightness (0-255) attribute, converted back to a percentage the same
    way list_lights() reports it. rgb_color is compared by hue/saturation
    (see _rgb_to_hs()'s docstring): Home Assistant does not preserve an
    rgb_color's value/brightness component for a light whose native color
    mode is 'hs', only hue and saturation, so an exact rgb_color
    round-trip would report `verified: false` on lights where the call
    genuinely worked.
    """
    if state not in ("", "on", "off", "toggle"):
        return error("invalid_state",
                     f"Unrecognised state {state!r} — use '', 'on', 'off', or 'toggle'.",
                     entity_id=entity_id, state=state)

    prior = None
    if state == "toggle":
        # No fixed target state for a toggle: whether it ends up "on" or
        # "off" depends on what the light was doing before the call, so
        # that has to be read first rather than assumed.
        with httpx.Client() as client:
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code != 404:
            r.raise_for_status()
            prior = r.json()["state"]

    with httpx.Client() as client:
        if state == "off":
            data: dict = {"entity_id": entity_id}
            if transition is not None:
                data["transition"] = transition
            r = client.post(f"{HA_URL}/api/services/light/turn_off", headers=HEADERS, json=data, timeout=10)
        elif state == "toggle":
            r = client.post(f"{HA_URL}/api/services/light/toggle", headers=HEADERS,
                            json={"entity_id": entity_id}, timeout=10)
        else:
            data = {"entity_id": entity_id}
            if brightness_pct is not None:
                data["brightness_pct"] = brightness_pct
            if color_temp_k is not None:
                data["color_temp_kelvin"] = color_temp_k
            if rgb_color is not None:
                data["rgb_color"] = rgb_color
            if effect:
                data["effect"] = effect
            if transition is not None:
                data["transition"] = transition
            r = client.post(f"{HA_URL}/api/services/light/turn_on", headers=HEADERS, json=data, timeout=10)
        r.raise_for_status()

    requested: dict = {}
    if state not in ("off", "toggle"):
        if brightness_pct is not None:
            requested["brightness_pct"] = brightness_pct
        if color_temp_k is not None:
            requested["color_temp_k"] = color_temp_k
        if rgb_color is not None:
            requested["rgb_color"] = rgb_color
        if effect:
            requested["effect"] = effect

    if state == "off":
        satisfied = lambda s: s["state"] == "off"
    elif state == "toggle":
        satisfied = lambda s: isinstance(prior, str) and s["state"] != prior
    else:
        satisfied = lambda s: _light_matches(s, requested)

    obs = observe_actuation(entity_id, satisfied)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, state=state)
    out = {
        "entity_id": entity_id,
        "state": state or "on",
        "verified": obs["verified"],
        "observed_state": obs["state"]["state"],
    }
    attrs = obs["state"].get("attributes", {})
    if "brightness_pct" in requested:
        brightness = attrs.get("brightness")
        out["brightness_pct"] = round(brightness / 2.55) if brightness is not None else None
    if "color_temp_k" in requested:
        out["color_temp_k"] = attrs.get("color_temp_kelvin")
    if "rgb_color" in requested:
        out["rgb_color"] = attrs.get("rgb_color")
    if "effect" in requested:
        out["effect"] = attrs.get("effect")
    return out
