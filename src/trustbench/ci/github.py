"""Post the TrustBench comment on a pull request, or update the one it posted before.

Only the standard library of Actions is used: the GITHUB_TOKEN of the workflow. The token is never printed.
We only edit a comment that a bot wrote and that starts with our marker, so a person cannot make us edit
their comment by copying the marker.
"""

import re

import httpx

from .comment import MARKER

API = "https://api.github.com"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GithubError(RuntimeError):
    pass


def _check(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code >= 400:
        hint = " The token may lack 'pull-requests: write', or the pull request comes from a fork." if r.status_code in (401, 403, 404) else ""
        raise GithubError(f"GitHub answered {r.status_code} when we tried to {what}.{hint}")
    return r


def post_or_update(token: str, repo: str, pr: int, body: str, api: str = API, client: httpx.Client | None = None) -> tuple[str, int]:
    """Returns ("created" or "updated", comment id)."""
    if not REPO.match(repo):
        raise GithubError(f"'{repo}' is not a repository name like owner/name.")
    if pr <= 0:
        raise GithubError(f"'{pr}' is not a pull request number.")
    if not body.startswith(MARKER):
        raise GithubError("The comment does not start with the TrustBench marker.")
    own = client or httpx.Client(timeout=30)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        base = f"{api}/repos/{repo}/issues"
        existing = None
        for page in range(1, 51):                                  # 5,000 comments is more than any pull request has.
            batch = _check(own.get(f"{base}/{pr}/comments", params={"per_page": 100, "page": page}, headers=headers),
                           "read the comments").json()
            existing = next((c for c in batch if (c.get("body") or "").startswith(MARKER) and (c.get("user") or {}).get("type") == "Bot"), None)
            if existing or len(batch) < 100:
                break
        if existing:
            _check(own.patch(f"{base}/comments/{existing['id']}", json={"body": body}, headers=headers), "update the comment")
            return "updated", existing["id"]
        made = _check(own.post(f"{base}/{pr}/comments", json={"body": body}, headers=headers), "post the comment").json()
        return "created", made["id"]
    except httpx.HTTPError as e:
        raise GithubError(f"Cannot reach GitHub: {type(e).__name__}") from e         # No URL, no token in the message.
    finally:
        if client is None:
            own.close()
