"""Open a GitHub PR for a drafted event via the REST API.

Used by the bot's /add confirm flow so a user can create the PR with one click.
Needs a token with contents + pull-requests write on the repo (fine-grained PAT).
"""

from __future__ import annotations

import base64

import requests

API = "https://api.github.com"


def create_event_pr(repo: str, base: str, token: str, slug: str, yaml_text: str) -> str:
    """Create branch + commit events/<slug>.yaml + open a PR; return its URL."""
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owner = repo.split("/")[0]
    branch = f"add-{slug}"

    # base branch SHA
    r = requests.get(f"{API}/repos/{repo}/git/ref/heads/{base}", headers=h, timeout=20)
    r.raise_for_status()
    sha = r.json()["object"]["sha"]

    # create the branch (ok if it already exists)
    rb = requests.post(
        f"{API}/repos/{repo}/git/refs", headers=h, timeout=20,
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )
    if rb.status_code not in (201, 422):
        rb.raise_for_status()

    # commit the file on that branch
    rc = requests.put(
        f"{API}/repos/{repo}/contents/events/{slug}.yaml", headers=h, timeout=20,
        json={
            "message": f"add event: {slug}",
            "branch": branch,
            "content": base64.b64encode(yaml_text.encode("utf-8")).decode("ascii"),
        },
    )
    rc.raise_for_status()

    # open the PR (reuse an existing one if already open for this branch)
    rp = requests.post(
        f"{API}/repos/{repo}/pulls", headers=h, timeout=20,
        json={
            "title": f"Add event: {slug}",
            "head": branch,
            "base": base,
            "body": "Drafted via the Discord bot `/add` command. Verify the lottery dates before merging.",
        },
    )
    if rp.status_code == 201:
        return rp.json()["html_url"]
    if rp.status_code == 422:  # PR already exists for this head
        existing = requests.get(
            f"{API}/repos/{repo}/pulls", headers=h, timeout=20,
            params={"head": f"{owner}:{branch}", "state": "open"},
        ).json()
        if existing:
            return existing[0]["html_url"]
    rp.raise_for_status()
    raise RuntimeError("PR creation failed")  # unreachable; for type-checkers
