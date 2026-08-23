import httpx

from tools._base import (
    mcp, HA_URL, HEADERS, ALEXA_KEYWORDS, default_language, _ws, confirm_entity_exists, envelope, error, ws_error,
)


@mcp.tool()
def list_media_players() -> dict:
    """
    List all media player entities with current state.

    Returns: {total, returned, offset, note?, media_players: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        result = []
        for s in r.json():
            if not s["entity_id"].startswith("media_player."):
                continue
            attrs = s.get("attributes", {})
            result.append({
                "entity_id": s["entity_id"],
                "name": attrs.get("friendly_name", s["entity_id"]),
                "state": s["state"],
                "media_title": attrs.get("media_title"),
                "media_artist": attrs.get("media_artist"),
                "volume_level": attrs.get("volume_level"),
                "is_volume_muted": attrs.get("is_volume_muted"),
                "source": attrs.get("source"),
                "source_list": attrs.get("source_list", []),
            })
        return envelope(sorted(result, key=lambda x: x["name"]), key="media_players")


@mcp.tool()
def send_tts(entity_id: str, message: str, language: str = "", engine: str = "tts.google_translate") -> dict:
    """
    Send a text-to-speech announcement to a media player.

    entity_id: media_player entity (e.g. 'media_player.echo_living_room')
    message: text to speak
    language: language code; defaults to the language configured in Home Assistant
    engine: TTS engine entity_id for non-Alexa players (default: 'tts.google_translate').
            Options: 'tts.cloud' (HA Cloud), 'tts.google_translate', 'tts.piper'

    For Amazon Echo (Alexa Media Player integration), uses alexa_media announce
    automatically. A player counts as an Echo when its entity_id contains one of
    the configured Alexa keywords ('echo' or 'alexa' unless changed) — set them
    to match speaker groups named after a room or the household.

    Returns: {entity_id, message, method, accepted: true, verified: null,
    detail} once Home Assistant accepts the call, or
    {error: "entity_not_found", ...} when entity_id (or, for a non-Alexa
    player, `engine`) has no state at all. `tts.speak` accepts a
    nonexistent engine entity_id exactly like any other target that does
    not exist — a 200 [] no-op, the same shape as a real announcement
    queued — so `engine` is confirmed to exist before the call is made for
    a non-Alexa player, the same check broadcast_tts() does for the same
    reason; Alexa players go through notify.alexa_media_* instead and are
    unaffected. Whether the announcement was actually heard has no state
    in Home Assistant to read back, so `verified` stays null.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    language = language or default_language()
    name = entity_id.split(".", 1)[1]
    is_alexa = any(kw in name.lower() for kw in ALEXA_KEYWORDS)

    if not is_alexa and (missing := confirm_entity_exists(engine)):
        return missing

    with httpx.Client() as client:
        if is_alexa:
            notify_service = f"alexa_media_{name}"
            r = client.post(
                f"{HA_URL}/api/services/notify/{notify_service}",
                headers=HEADERS,
                json={"message": message, "data": {"type": "announce"}},
                timeout=15,
            )
        else:
            r = client.post(
                f"{HA_URL}/api/services/tts/speak",
                headers=HEADERS,
                json={
                    "entity_id": engine,
                    "media_player_entity_id": entity_id,
                    "message": message,
                    "language": language,
                    "cache": False,
                },
                timeout=15,
            )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "message": message,
        "method": "alexa_announce" if is_alexa else "tts_speak",
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the announcement; whether it "
                  "was actually heard has no state here to confirm.",
    }


@mcp.tool()
def media_player_control(
    entity_id: str,
    command: str,
    volume: float = None,
    source: str = "",
    media_content_id: str = "",
    media_content_type: str = "music",
) -> dict:
    """
    Control a media player.

    command:
      - 'play'        resume playback
      - 'pause'       pause playback
      - 'stop'        stop playback
      - 'next'        next track
      - 'previous'    previous track
      - 'turn_on'     turn on
      - 'turn_off'    turn off
      - 'mute'        toggle mute
      - 'volume'      set volume (requires volume: 0.0–1.0)
      - 'source'      select source (requires source parameter)
      - 'play_media'  play specific media (requires media_content_id, optional media_content_type)

    Returns: {command, entity_id, accepted: true, verified: null, detail}
    once Home Assistant accepts the call, or {error: "entity_not_found"/
    "invalid_command", ...} otherwise.

    `verified` stays null rather than checked against the player's state:
    the media_player domain spans dozens of unrelated integrations (a
    Chromecast, a Sonos, a browser tab, an Alexa speaker, ...) with no
    single consistent state machine across them — "pause" settles into
    "paused" on some, "idle" on others, and a source/volume change can
    take anywhere from instant to several seconds depending on the
    backend. Use get_states_by_domain('media_player') or
    list_media_players() afterward to see what actually happened.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    cmd_map = {
        "play": "media_play", "pause": "media_pause", "stop": "media_stop",
        "next": "media_next_track", "previous": "media_previous_track",
        "turn_on": "turn_on", "turn_off": "turn_off", "mute": "toggle",
    }
    with httpx.Client() as client:
        if command in cmd_map:
            r = client.post(f"{HA_URL}/api/services/media_player/{cmd_map[command]}",
                            headers=HEADERS, json={"entity_id": entity_id}, timeout=10)
        elif command == "volume" and volume is not None:
            r = client.post(f"{HA_URL}/api/services/media_player/volume_set",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "volume_level": volume}, timeout=10)
        elif command == "source" and source:
            r = client.post(f"{HA_URL}/api/services/media_player/select_source",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "source": source}, timeout=10)
        elif command == "play_media" and media_content_id:
            r = client.post(f"{HA_URL}/api/services/media_player/play_media",
                            headers=HEADERS,
                            json={
                                "entity_id": entity_id,
                                "media_content_id": media_content_id,
                                "media_content_type": media_content_type,
                            }, timeout=10)
        else:
            return error("invalid_command", f"Unknown command or missing parameters: {command}",
                         entity_id=entity_id, command=command)
        r.raise_for_status()
    return {
        "command": command,
        "entity_id": entity_id,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the call; see this tool's "
                  "docstring for why the result cannot be verified here.",
    }


@mcp.tool()
def search_and_play_media(entity_id: str, query: str, media_type: str = "music") -> dict:
    """
    Search for media and play it on a media player.

    entity_id: target media player (e.g. 'media_player.spotify')
    query: search query (e.g. 'Daft Punk', 'Bohemian Rhapsody')
    media_type: content type hint — 'music', 'playlist', 'podcast', 'video' (default: 'music')

    Works on players that support media browsing/search (Spotify, YouTube Music, etc.).
    Uses the HA media_player.play_media service with enqueue=replace.

    Returns: {entity_id, query, media_type, accepted: true, verified: null,
    detail} once Home Assistant accepts the call, or
    {error: "entity_not_found", ...} when entity_id has no state at all.
    See media_player_control() for why the result cannot be verified
    against a single expected state across the many media_player backends.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/media_player/play_media",
            headers=HEADERS,
            json={
                "entity_id": entity_id,
                "media_content_id": query,
                "media_content_type": media_type,
                "enqueue": "replace",
            },
            timeout=15,
        )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "query": query,
        "media_type": media_type,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the call; see media_player_control()'s "
                  "docstring for why the result cannot be verified here.",
    }


@mcp.tool()
def broadcast_tts(message: str, language: str = "", engine: str = "tts.google_translate") -> dict:
    """
    Send a TTS announcement to all active media players simultaneously.

    Alexa/Echo devices use alexa_media announce automatically.
    All other active media players use the specified TTS engine.

    message: text to speak
    language: language code; defaults to the language configured in Home Assistant
    engine: TTS engine for non-Alexa players (default: 'tts.google_translate')

    Returns: {message, engine, engine_exists, ok_count, total, note?,
    players: [{entity_id, ok, method, error?}]}.

    `ok` means the per-player service call got a 2xx response (or, on a
    raised exception, False with `error` set) AND, for non-Alexa players,
    that `engine` was confirmed to exist before any call was made for it.
    `tts.speak` accepts a nonexistent engine entity_id exactly like any
    other target that does not exist — a 200 [] no-op, the same shape as
    an idempotent call (see confirm_entity_exists()) — so a 2xx response
    alone cannot tell "queued on every player" from "queued on nothing,
    the engine never existed". Measured live: with no tts.* entity
    registered on the instance, calling tts/speak still answered 200 [],
    which the old code reported as ok: true for all 8 players while 0 were
    actually announced. This checks `engine` once up front and, when it is
    missing, marks every non-Alexa player's result ok: False with that
    reason instead of attempting a call that cannot work — Alexa players
    are unaffected, since they go through notify.alexa_media_* instead of
    the TTS engine and Home Assistant already answers a nonexistent notify
    service with a genuine 4xx. This still does not confirm the
    announcement was actually heard, which has no state in Home Assistant
    to read back.
    """
    language = language or default_language()
    engine_missing = confirm_entity_exists(engine) is not None

    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        players = [
            s for s in r.json()
            if s["entity_id"].startswith("media_player.") and s["state"] not in ("unavailable", "unknown")
        ]

    results = []

    with httpx.Client() as client:
        for player in players:
            entity_id = player["entity_id"]
            name = entity_id.split(".", 1)[1]
            is_alexa = any(kw in name.lower() for kw in ALEXA_KEYWORDS)
            if not is_alexa and engine_missing:
                results.append({
                    "entity_id": entity_id, "ok": False, "method": "tts_speak",
                    "error": f"{engine} does not exist on this Home Assistant "
                             "instance - not attempted.",
                })
                continue
            try:
                if is_alexa:
                    notify_service = f"alexa_media_{name}"
                    r = client.post(
                        f"{HA_URL}/api/services/notify/{notify_service}",
                        headers=HEADERS,
                        json={"message": message, "data": {"type": "announce"}},
                        timeout=15,
                    )
                else:
                    r = client.post(
                        f"{HA_URL}/api/services/tts/speak",
                        headers=HEADERS,
                        json={
                            "entity_id": engine,
                            "media_player_entity_id": entity_id,
                            "message": message,
                            "language": language,
                            "cache": False,
                        },
                        timeout=15,
                    )
                results.append({"entity_id": entity_id, "ok": r.is_success, "method": "alexa_announce" if is_alexa else "tts_speak"})
            except Exception as e:
                results.append({"entity_id": entity_id, "ok": False, "error": str(e)})

    ok_count = sum(1 for res in results if res["ok"])
    out = {
        "message": message,
        "engine": engine,
        "engine_exists": not engine_missing,
        "ok_count": ok_count,
        "total": len(results),
        "players": results,
    }
    if engine_missing:
        out["note"] = f"{engine} does not exist - only Alexa players (if any) could be attempted."
    return out


@mcp.tool()
def browse_media(
    entity_id: str,
    media_content_type: str = "",
    media_content_id: str = "",
) -> dict:
    """
    Browse the media library of a media player (Spotify, Plex, YouTube Music, etc.).

    entity_id:         media player to browse, e.g. 'media_player.spotify'
    media_content_type: type of content to browse — leave empty for root level.
                        Examples: 'playlist', 'album', 'artist', 'library', 'favorites'
    media_content_id:  ID of the item to browse into (from a previous browse result).
                       Leave empty to browse the root library.

    Returns the available children (playlists, albums, tracks, etc.) for the given level.
    Use the returned children's media_content_type and media_content_id to browse deeper,
    or pass them to search_and_play_media() to start playback.
    """
    msg: dict = {"type": "media_player/browse_media", "entity_id": entity_id}
    if media_content_type:
        msg["media_content_type"] = media_content_type
    if media_content_id:
        msg["media_content_id"] = media_content_id
    result = _ws(msg)
    if err := ws_error(result):
        return err
    data = result["result"] or {}
    return {
        "title": data.get("title"),
        "media_content_type": data.get("media_content_type"),
        "media_content_id": data.get("media_content_id"),
        "can_play": data.get("can_play", False),
        "can_expand": data.get("can_expand", False),
        "children_media_class": data.get("children_media_class"),
        "children": [
            {
                "title": c.get("title"),
                "media_content_type": c.get("media_content_type"),
                "media_content_id": c.get("media_content_id"),
                "can_play": c.get("can_play", False),
                "can_expand": c.get("can_expand", False),
                "thumbnail": c.get("thumbnail"),
            }
            for c in (data.get("children") or [])
        ],
    }
