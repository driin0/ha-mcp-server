import re

import httpx

from tools._base import mcp, HA_URL, HEADERS, _ws, confirm_entity_exists, envelope, error, ws_error


def _resolve_telegram_chat_id(entity_id: str) -> int | None:
    """
    Resolve the Telegram chat_id for a notify entity.

    Strategy:
    1. Query entity registry via WS → extract chat_id from unique_id
       (new-style telegram_bot entities: unique_id = "{bot_id}_{chat_id}")
    2. Fallback: regex on friendly_name for "(chat_id)" suffix
       (legacy YAML-configured entities and group chats)

    Returns None if chat_id cannot be determined - notably for any
    ordinary, non-Telegram notify target (a mobile app, Alexa, a file
    notifier): picking the wrong notify service for send_photo() /
    send_camera_snapshot() is a plausible, ordinary caller mistake, not
    an exceptional condition, so this reports it with None rather than
    raising - see _telegram_target_error() below for the error() built
    from it.

    TODO HA 2026.9.0: migrate callers to use 'chat_id' or 'entity_id' parameter
    directly in telegram_bot service calls once the new API is stable.
    """
    # 1. Entity registry unique_id
    reg = _ws({"type": "config/entity_registry/get", "entity_id": entity_id})
    unique_id = (reg.get("result") or {}).get("unique_id") or ""
    if unique_id:
        # Format: "{bot_id}_{chat_id}" — split on first underscore-separated bot_id
        parts = unique_id.split("_", 1)
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            return int(parts[1])

    # 2. Fallback: friendly_name "(chat_id)" suffix
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            friendly = r.json().get("attributes", {}).get("friendly_name", "")
            m = re.search(r"\((-?\d+)\)\s*$", friendly)
            if m:
                return int(m.group(1))

    return None


def _telegram_type(chat_id: int | None) -> str:
    """Infer Telegram target type from chat_id."""
    if chat_id is None:
        return "other"
    if chat_id > 0:
        return "telegram_private"
    return "telegram_group"  # negative: group or channel


def _telegram_target_error(entity_id: str) -> dict:
    """error() for a notify target send_photo()/send_camera_snapshot() cannot
    use because _resolve_telegram_chat_id() returned None for it - not a
    Telegram entity at all, or a legacy one missing the "(chat_id)"
    friendly-name suffix these two tools depend on.

    Names what these two tools actually support, and lists the other
    notify targets that DO look like Telegram ones - list_notify_services()
    already classifies every notify.* entity this cheaply (one /api/states
    read, reused here rather than repeated), so a caller does not have to
    make a second call just to find out what would work.
    """
    targets = [
        s["entity_id"] for s in list_notify_services().get("services", [])
        if s["type"] in ("telegram_private", "telegram_group")
    ]
    return error(
        "not_a_telegram_target",
        f"{entity_id} does not look like a Telegram notify target. "
        "send_photo() and send_camera_snapshot() only support Telegram - "
        "they call telegram_bot.send_photo directly - and this entity's "
        "registry unique_id and friendly name did not resolve a chat_id. "
        "A legacy YAML-configured Telegram target needs the chat_id in "
        "parentheses in its friendly name, e.g. 'Name (123456)'.",
        entity_id=entity_id,
        telegram_targets=targets,
    )


@mcp.tool()
def list_notify_services() -> dict:
    """
    List all available notification services.

    Returns: {total, returned, offset, note?, services: [{entity_id, name,
             type, state}]}
    type: 'telegram_private', 'telegram_group', or 'other' (mobile app, Alexa, file, etc.)
    Includes Telegram targets, mobile app, Alexa and other notify entities.
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        results = []
        for entity in r.json():
            if not entity.get("entity_id", "").startswith("notify."):
                continue
            friendly = entity.get("attributes", {}).get("friendly_name", "")
            m = re.search(r"\((-?\d+)\)\s*$", friendly)
            chat_id = int(m.group(1)) if m else None
            results.append({
                "entity_id": entity["entity_id"],
                "name": friendly,
                "type": _telegram_type(chat_id),
                "state": entity.get("state"),
            })
        results.sort(key=lambda x: x["entity_id"])
    return envelope(results, key="services")


@mcp.tool()
def send_notification(message: str, title: str = "", target: str = "notify.notify") -> dict:
    """
    Send a notification to a notify entity.

    target: entity_id of the notify target (e.g. 'notify.telegram_home', 'notify.mobile_app_myphone').
            Use list_notify_services() to discover available targets.

    Returns: {target, message, accepted: true, verified: null, detail} once
    Home Assistant accepts the call, or {error: "entity_not_found", ...}
    when the target entity has no state at all. Delivery to the underlying
    channel (a push notification actually reaching a phone, a Telegram
    message actually being delivered) has no state in Home Assistant to
    read back, so `verified` stays null rather than claiming a delivery
    this tool cannot observe.
    """
    entity_id = target if target.startswith("notify.") else f"notify.{target}"
    if missing := confirm_entity_exists(entity_id):
        return missing
    data: dict = {"message": message}
    if title:
        data["title"] = title
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/notify/send_message",
            headers=HEADERS,
            json={"entity_id": entity_id, **data},
            timeout=15,
        )
        r.raise_for_status()
    return {
        "target": entity_id,
        "message": message,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the notification; delivery to "
                  "the underlying channel has no state here to confirm it.",
    }


@mcp.tool()
def send_notification_with_buttons(
    target: str,
    message: str,
    buttons: list,
    title: str = "",
) -> dict:
    """
    Send a Telegram message with inline keyboard buttons.

    target: notify entity_id (e.g. 'notify.telegram_home')
    buttons: list of button rows. Each row is a list of button dicts.
      - URL button:  {"text": "Open", "url": "https://..."}
      - Callback:    {"text": "Yes", "callback_data": "/yes"}

    Example:
      buttons: [[{"text": "Open HA", "url": "https://homeassistant.local:8123"}]]
      buttons: [[{"text": "Yes", "callback_data": "/yes"}, {"text": "No", "callback_data": "/no"}]]

    Returns: {target, message, accepted: true, verified: null, detail} once
    Home Assistant accepts the call, or {error: "entity_not_found", ...}
    when the target entity has no state at all. See send_notification()
    for why delivery itself is not verifiable here.
    """
    entity_id = target if target.startswith("notify.") else f"notify.{target}"
    if missing := confirm_entity_exists(entity_id):
        return missing
    payload: dict = {
        "entity_id": entity_id,
        "message": message,
        "data": {"inline_keyboard": buttons},
    }
    if title:
        payload["title"] = title
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/notify/send_message",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
    return {
        "target": entity_id,
        "message": message,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the message; delivery to "
                  "Telegram has no state here to confirm it.",
    }


@mcp.tool()
def send_photo(target: str, photo_url: str, caption: str = "") -> dict:
    """
    Send a photo via Telegram using telegram_bot.send_photo.

    target: notify entity_id (e.g. 'notify.telegram_home')
    photo_url: publicly accessible direct URL of the photo (no redirects)
    caption: optional caption text

    Returns: {target, chat_id, photo, accepted: true, verified: null,
    detail} once Home Assistant accepts the call, or an error() envelope -
    {error: "entity_not_found", ...} when the target entity has no state
    at all, or {error: "not_a_telegram_target", telegram_targets: [...],
    ...} when it exists but is not a Telegram notify entity (this tool
    only works with Telegram; the error lists the notify targets that
    are). See send_notification() for why delivery itself is not
    verifiable here.
    """
    entity_id = target if target.startswith("notify.") else f"notify.{target}"
    if missing := confirm_entity_exists(entity_id):
        return missing
    chat_id = _resolve_telegram_chat_id(entity_id)
    if chat_id is None:
        return _telegram_target_error(entity_id)
    payload: dict = {"url": photo_url, "target": [chat_id]}
    if caption:
        payload["caption"] = caption
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/telegram_bot/send_photo",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
    return {
        "target": entity_id,
        "chat_id": chat_id,
        "photo": photo_url,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the photo; delivery to Telegram "
                  "has no state here to confirm it.",
    }


@mcp.tool()
def send_camera_snapshot(camera_entity_id: str, target: str, caption: str = "") -> dict:
    """
    Fetch a camera snapshot and send it to a Telegram chat.

    Uses the camera entity's access_token to build a public URL (no Bearer auth needed),
    then sends it via telegram_bot.send_photo. Requires HA to have an external_url configured
    (Settings → System → Network → Home Assistant URL).

    camera_entity_id: e.g. 'camera.gate_snapshot'
    target: notify entity_id (e.g. 'notify.telegram_home')
    caption: optional caption text

    Returns: {camera, target, chat_id, photo_url, accepted: true,
    verified: null, detail} once Home Assistant accepts the call, or an
    error() envelope - {error: "entity_not_found", ...} when either the
    camera or the notify target has no state at all, or
    {error: "not_a_telegram_target", telegram_targets: [...], ...} when
    the notify target exists but is not a Telegram notify entity. See
    send_notification() for why delivery itself is not verifiable here.
    """
    # 1. Confirm the notify target exists
    notify_id = target if target.startswith("notify.") else f"notify.{target}"
    if missing := confirm_entity_exists(notify_id):
        return missing

    with httpx.Client() as client:
        # 2. Get camera access_token from entity state - checked before
        # resolving the notify target's chat_id, so a bad camera_entity_id
        # is reported as itself rather than masked by a chat_id error for
        # the *other* argument.
        r = client.get(f"{HA_URL}/api/states/{camera_entity_id}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return error("entity_not_found",
                         f"{camera_entity_id} does not exist on this Home Assistant instance.",
                         entity_id=camera_entity_id)
        r.raise_for_status()
        access_token = r.json().get("attributes", {}).get("access_token")
        if not access_token:
            return {"error": "no_access_token", "entity_id": camera_entity_id,
                    "detail": "Camera entity has no access_token attribute."}

        # 2b. Resolve the notify target's chat_id
        chat_id = _resolve_telegram_chat_id(notify_id)
        if chat_id is None:
            return _telegram_target_error(notify_id)

        # 3. Get HA external URL
        cfg = client.get(f"{HA_URL}/api/config", headers=HEADERS, timeout=10)
        cfg.raise_for_status()
        cfg_data = cfg.json()
        external_url = cfg_data.get("external_url") or cfg_data.get("internal_url", "")
        if not external_url:
            return {"error": "no_external_url", "detail": "Set an external URL in HA Settings → System → Network."}

        external_url = external_url.rstrip("/")

        # 4. Build token-authenticated URL (no Bearer auth required — Telegram can fetch it)
        photo_url = f"{external_url}/api/camera_proxy/{camera_entity_id}?token={access_token}"

        # 5. Send via telegram_bot.send_photo
        payload: dict = {"url": photo_url, "target": [chat_id]}
        if caption:
            payload["caption"] = caption
        r = client.post(
            f"{HA_URL}/api/services/telegram_bot/send_photo",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()

    return {
        "camera": camera_entity_id,
        "target": notify_id,
        "chat_id": chat_id,
        "photo_url": photo_url,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the photo; delivery to Telegram "
                  "has no state here to confirm it.",
    }


def _persistent_notification_ids() -> set | dict:
    """The set of currently active notification_ids, or a ws_error() dict."""
    result = _ws({"type": "persistent_notification/get"})
    if err := ws_error(result):
        return err
    return {n.get("notification_id") for n in result["result"]}


@mcp.tool()
def create_persistent_notification(message: str, title: str = "", notification_id: str = "") -> dict:
    """
    Create a persistent notification in the Home Assistant UI.

    notification_id: optional — if provided, a subsequent call with the same ID
                     will update the existing notification instead of creating a new one.

    Returns: {notification_id, title, message, verified} when
    notification_id was given — `verified` is true only when that id is
    present in persistent_notification/get's list after the call, read
    back rather than assumed. When notification_id is left empty, Home
    Assistant generates one internally but never returns it from the
    create service call — the response is an opaque, sometimes-empty list
    like every other service call in this codebase — so there is nothing
    stable to look up afterward; that case instead returns
    {notification_id: null, title, message, accepted: true,
    verified: null}.
    """
    data: dict = {"message": message}
    if title:
        data["title"] = title
    if notification_id:
        data["notification_id"] = notification_id
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/persistent_notification/create",
            headers=HEADERS,
            json=data,
            timeout=10,
        )
        r.raise_for_status()

    if not notification_id:
        return {
            "notification_id": None,
            "title": title,
            "message": message,
            "accepted": True,
            "verified": None,
            "detail": "Home Assistant generates the id internally and does "
                      "not return it from this call, so there is nothing "
                      "stable to verify against - pass notification_id "
                      "explicitly to get a verified result.",
        }

    ids = _persistent_notification_ids()
    if isinstance(ids, dict):  # ws_error()
        return ids
    return {
        "notification_id": notification_id,
        "title": title,
        "message": message,
        "verified": notification_id in ids,
    }


@mcp.tool()
def list_persistent_notifications() -> dict:
    """
    List all active persistent notifications in Home Assistant.

    Returns: {total, returned, offset, note?, notifications: [{notification_id,
             title, message, created_at}]}

    ⚠️ third-party-settable: `title` and `message` come from whatever called
    persistent_notification.create - any integration, any automation, not
    necessarily this installation's owner. See tools/_base.py's
    "Third-party-settable fields" note.
    """
    result = _ws({"type": "persistent_notification/get"})
    if err := ws_error(result):
        return err
    out = [
        {
            "notification_id": n.get("notification_id"),
            "title": n.get("title", ""),
            "message": n.get("message", ""),
            "created_at": n.get("created_at"),
        }
        for n in result["result"]
    ]
    return envelope(out, key="notifications")


@mcp.tool()
def dismiss_persistent_notification(notification_id: str) -> dict:
    """Dismiss a persistent notification by its notification_id.

    Returns: {notification_id, verified} once Home Assistant accepts the
    call, or {error: "entity_not_found", ...} when notification_id was
    already absent before the call — dismissing something already gone
    would otherwise report the same success as dismissing something real.
    `verified` is true only when notification_id no longer appears in
    persistent_notification/get's list, read back after the call.
    """
    before = _persistent_notification_ids()
    if isinstance(before, dict):  # ws_error()
        return before
    if notification_id not in before:
        return error("entity_not_found",
                     f"No active persistent notification with id {notification_id!r}.",
                     notification_id=notification_id)

    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/persistent_notification/dismiss",
            headers=HEADERS,
            json={"notification_id": notification_id},
            timeout=10,
        )
        r.raise_for_status()

    after = _persistent_notification_ids()
    if isinstance(after, dict):  # ws_error()
        return after
    return {"notification_id": notification_id, "verified": notification_id not in after}
