"""GCP Cloud Function: commit an event YAML straight to main (no PR).

Called by the site's in-site editor. Holds the GitHub PAT server-side and is
guarded by a shared admin secret, so the browser never sees a token. Commits to
`main`, which triggers the Pages deploy (the CI build re-validates as a backstop).

Env: GITHUB_REPO, GITHUB_BRANCH (default main), GITHUB_TOKEN, ADMIN_SECRET,
ALLOW_ORIGIN (default https://mting314.github.io).
"""

from __future__ import annotations

import base64
import json
import os

import functions_framework
import requests
from validate import valid_slug, validate_event_yaml

API = "https://api.github.com"
REPO = os.environ["GITHUB_REPO"]
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
TOKEN = os.environ["GITHUB_TOKEN"]
SECRET = os.environ["ADMIN_SECRET"]
ALLOW_ORIGIN = os.environ.get("ALLOW_ORIGIN", "https://mting314.github.io")


def _cors():
    return {
        "Access-Control-Allow-Origin": ALLOW_ORIGIN,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Admin-Secret",
        "Content-Type": "application/json",
    }


@functions_framework.http
def commit(request):
    headers = _cors()
    if request.method == "OPTIONS":
        return ("", 204, headers)
    if request.method != "POST":
        return (json.dumps({"error": "POST only"}), 405, headers)
    if request.headers.get("X-Admin-Secret") != SECRET:
        return (json.dumps({"error": "unauthorized"}), 401, headers)

    body = request.get_json(silent=True) or {}
    slug, text = body.get("slug", ""), body.get("yaml", "")
    deleting = bool(body.get("delete"))
    if deleting:
        if not valid_slug(slug):
            return (json.dumps({"error": "invalid slug"}), 400, headers)
    else:
        try:
            validate_event_yaml(slug, text)
        except Exception as exc:  # noqa: BLE001
            return (json.dumps({"error": f"invalid: {exc}"}), 400, headers)

    h = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}
    path = f"events/{slug}.yaml"
    # need the current blob sha to update or delete an existing file
    g = requests.get(
        f"{API}/repos/{REPO}/contents/{path}", headers=h, params={"ref": BRANCH}, timeout=20
    )
    sha = g.json().get("sha") if g.status_code == 200 else None

    if deleting:
        if not sha:
            return (json.dumps({"error": "event not found"}), 404, headers)
        r = requests.delete(
            f"{API}/repos/{REPO}/contents/{path}",
            headers=h,
            json={"message": f"delete event: {slug}", "branch": BRANCH, "sha": sha},
            timeout=20,
        )
        if r.status_code == 200:
            commit_url = (r.json().get("commit") or {}).get("html_url")
            return (json.dumps({"ok": True, "deleted": True, "commit": commit_url}), 200, headers)
        return (json.dumps({"error": f"github {r.status_code}: {r.text[:200]}"}), 502, headers)

    payload = {
        "message": f"web edit: {slug}",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(f"{API}/repos/{REPO}/contents/{path}", headers=h, json=payload, timeout=20)
    if r.status_code in (200, 201):
        commit_url = (r.json().get("commit") or {}).get("html_url")
        return (json.dumps({"ok": True, "updated": bool(sha), "commit": commit_url}), 200, headers)
    return (json.dumps({"error": f"github {r.status_code}: {r.text[:200]}"}), 502, headers)
