from tools._base import mcp, _ws, envelope, ws_error


def _hacs_check(result: dict):
    """Return an error dict if the WS call failed (e.g. HACS not installed).

    `if not result.get("success", True)` used to be the whole check - which
    treats a dict with no "success" key at all as a success, since
    `.get("success", True)` then falls back to the default. _ws returns
    exactly that shape, {"error": "Auth failed: ..."}, when the connection
    or the authentication fails, so a transport failure passed straight
    through: install_hacs_repo reported {"installed": True} for a write
    that never reached Home Assistant. Routing through ws_error() first -
    the same helper every other WS-backed tool in this codebase uses -
    catches that shape too, while still keeping the translation below for
    the one case ws_error does not know is special to HACS: an
    unknown_command/not_found response means the custom component simply
    is not loaded, not a real error.
    """
    if err := ws_error(result):
        if err["error"] in ("unknown_command", "not_found"):
            return {
                "error": "hacs_not_available",
                "detail": "HACS is not installed or not running on this Home Assistant instance.",
            }
        return err
    return None


@mcp.tool()
def hacs_info() -> dict:
    """
    Get general HACS status: version, stage, categories, pending tasks.
    Useful to confirm HACS is installed and running before other HACS operations.
    """
    result = _ws({"type": "hacs/info"})
    err = _hacs_check(result)
    if err:
        return err
    return result.get("result", {})


@mcp.tool()
def list_hacs_repos(
    category: str = "",
    installed_only: bool = False,
    updates_only: bool = False,
) -> dict:
    """
    List repositories known to HACS (installed and available in the catalog).

    category:      filter by category — 'integration', 'plugin', 'theme',
                   'appdaemon', 'python_script', 'template'. Leave empty for all.
    installed_only: if True, return only installed repositories.
    updates_only:  if True, return only repositories with a pending update.

    Returns: {total, returned, offset, note?, repositories: [{id, full_name,
             name, category, installed, installed_version, available_version,
             pending_upgrade, custom, stars, description}]}

    A failed call because HACS is not installed returns
    {error: hacs_not_available, detail: ...}, same as every other HACS
    tool in this file. Any other failure returns the WebSocket command's
    own code and message, unmodified.
    """
    msg: dict = {"type": "hacs/repositories/list"}
    if category:
        msg["categories"] = [category]
    result = _ws(msg)
    if err := ws_error(result):
        return _hacs_check(result) or err
    repos = result["result"]
    if installed_only:
        repos = [r for r in repos if r.get("installed")]
    if updates_only:
        repos = [r for r in repos if r.get("pending_upgrade")]
    out = [
        {
            "id": r.get("id"),
            "full_name": r.get("full_name"),
            "name": r.get("name") or r.get("full_name", ""),
            "category": r.get("category"),
            "installed": r.get("installed", False),
            "installed_version": r.get("installed_version"),
            "available_version": r.get("available_version"),
            "pending_upgrade": r.get("pending_upgrade", False),
            "custom": r.get("custom", False),
            "stars": r.get("stars", 0),
            "description": r.get("description", ""),
        }
        for r in repos
    ]
    return envelope(out, key="repositories")


@mcp.tool()
def search_hacs(query: str, category: str = "", limit: int = 20) -> dict:
    """
    Search HACS catalog for repositories matching a query string.

    query:    substring to search in name, full_name, or description (case-insensitive)
    category: optional filter — 'integration', 'plugin', 'theme', 'appdaemon',
              'python_script', 'template'
    limit:    max results to return, sorted by stars descending (default 20)

    Returns: {total, returned, offset, note?, repositories: [{id, full_name,
             name, category, installed, installed_version, available_version,
             stars, description}]}

    `total` counts every repository that matched the query/category, not
    just the page returned - raise `limit` or narrow the query when it
    exceeds `returned`. A failed call because HACS is not installed returns
    {error: hacs_not_available, detail: ...}, same as every other HACS tool
    in this file. Any other failure returns the WebSocket command's own
    code and message, unmodified.
    """
    msg: dict = {"type": "hacs/repositories/list"}
    if category:
        msg["categories"] = [category]
    result = _ws(msg)
    if err := ws_error(result):
        return _hacs_check(result) or err
    repos = result["result"]
    q = query.lower()
    matches = [
        r for r in repos
        if q in (r.get("name") or "").lower()
        or q in (r.get("full_name") or "").lower()
        or q in (r.get("description") or "").lower()
    ]
    matches.sort(key=lambda x: x.get("stars", 0), reverse=True)
    out = [
        {
            "id": r.get("id"),
            "full_name": r.get("full_name"),
            "name": r.get("name") or r.get("full_name", ""),
            "category": r.get("category"),
            "installed": r.get("installed", False),
            "installed_version": r.get("installed_version"),
            "available_version": r.get("available_version"),
            "stars": r.get("stars", 0),
            "description": r.get("description", ""),
        }
        for r in matches
    ]
    return envelope(out, key="repositories", limit=limit)


@mcp.tool()
def get_hacs_repo(repository_id: str) -> dict:
    """
    Get detailed info about a specific HACS repository.

    repository_id: numeric ID string (use list_hacs_repos() or search_hacs() to find it)

    Returns full details including releases, authors, topics, and install status.
    """
    result = _ws({"type": "hacs/repository/info", "repository_id": repository_id})
    err = _hacs_check(result)
    if err:
        return err
    return result.get("result", {})


@mcp.tool()
def install_hacs_repo(repository_id: str, version: str = "") -> dict:
    """
    Install or update a HACS repository.

    repository_id: numeric ID string (use search_hacs() or list_hacs_repos() to find it)
    version:       specific version/tag to install (leave empty for latest)

    ⚠️ Integrations require a Home Assistant restart to take effect.
    Lovelace plugins and themes are active immediately. Installs files
    from the repository onto disk - review an unfamiliar repository before
    installing it, the same as you would before installing anything else
    from a third party.

    Returns: {installed: true, repository_id, version}. A failed call
    because HACS is not installed returns {error: hacs_not_available,
    detail: ...}, same as every other HACS tool in this file.
    """
    msg: dict = {"type": "hacs/repository/download", "repository": repository_id}
    if version:
        msg["version"] = version
    result = _ws(msg)
    err = _hacs_check(result)
    if err:
        return err
    return {
        "installed": True,
        "repository_id": repository_id,
        "version": version or "latest",
    }


@mcp.tool()
def remove_hacs_repo(repository_id: str) -> dict:
    """
    Uninstall a HACS repository (removes files from disk).

    repository_id: numeric ID string (use list_hacs_repos(installed_only=True) to find it)

    Note: this removes the custom component / plugin files. A HA restart may be needed
    to fully remove the integration. To only remove the repository from the HACS list
    without deleting files, this is not the right tool.

    ⚠️ This is irreversible: the files are deleted from disk. Any config
    entry or entity depending on this integration stops working until it
    is reinstalled.

    Returns: {removed: true, repository_id}. A failed call because HACS is
    not installed returns {error: hacs_not_available, detail: ...}, same
    as every other HACS tool in this file.
    """
    result = _ws({"type": "hacs/repository/remove", "repository": repository_id})
    err = _hacs_check(result)
    if err:
        return err
    return {"removed": True, "repository_id": repository_id}


@mcp.tool()
def add_hacs_custom_repo(repository: str, category: str) -> dict:
    """
    Add a custom repository to HACS (does not install it — just registers it).

    repository: GitHub URL or 'owner/repo' string
                e.g. 'https://github.com/custom-cards/button-card' or 'custom-cards/button-card'
    category:   repository type — 'integration', 'plugin', 'theme',
                'appdaemon', 'python_script', 'template'

    After adding, use search_hacs() to find the repo ID and install_hacs_repo() to install it.

    Returns: {added: true, repository, category}. A failed call because
    HACS is not installed returns {error: hacs_not_available, detail:
    ...}, same as every other HACS tool in this file.
    """
    result = _ws({"type": "hacs/repositories/add", "repository": repository, "category": category})
    err = _hacs_check(result)
    if err:
        return err
    return {"added": True, "repository": repository, "category": category}
