"""Commit an event YAML straight to the default branch via the GitHub REST API.

Used by the bot's /add confirm flow. Needs a token with contents write on the
repo (fine-grained PAT). No PR — the change lands on main and auto-deploys; the
CI build re-validates as a backstop.
"""

from __future__ import annotations

import base64

import requests

API = "https://api.github.com"


def commit_to_main(
    repo: str, branch: str, token: str, slug: str, yaml_text: str, message: str | None = None
) -> str:
    """Create or update events/<slug>.yaml on `branch`; return the commit URL."""
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    path = f"events/{slug}.yaml"
    # need the current blob sha to update an existing file
    g = requests.get(
        f"{API}/repos/{repo}/contents/{path}", headers=h, params={"ref": branch}, timeout=20
    )
    sha = g.json().get("sha") if g.status_code == 200 else None

    payload = {
        "message": message or f"add/update event: {slug}",
        "content": base64.b64encode(yaml_text.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(f"{API}/repos/{repo}/contents/{path}", headers=h, json=payload, timeout=20)
    r.raise_for_status()
    return (r.json().get("commit") or {}).get("html_url", "")
