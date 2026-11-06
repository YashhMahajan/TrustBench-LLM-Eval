"""Posting the comment: create or update in place, never touch other people's comments, never leak the token."""

import json

import httpx
import pytest

from trustbench.ci.comment import MARKER
from trustbench.ci.github import GithubError, post_or_update

TOKEN = "ghs_SECRET_TOKEN_123"
BODY = MARKER + "\n## TrustBench: pass"


class FakeGithub:
    """Just enough of the issue comments API. `comments` is a list of dicts like GitHub returns."""

    def __init__(self, comments=None, fail=None):
        self.comments, self.fail, self.calls, self.next_id = list(comments or []), fail, [], 1000

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if self.fail:
            if isinstance(self.fail, Exception):
                raise self.fail
            return httpx.Response(self.fail, json={"message": "nope"})
        path = request.url.path
        if request.method == "GET":
            q = dict(request.url.params)
            page, per = int(q["page"]), int(q["per_page"])
            return httpx.Response(200, json=self.comments[(page - 1) * per: page * per])
        if request.method == "PATCH":
            cid = int(path.rsplit("/", 1)[1])
            c = next(c for c in self.comments if c["id"] == cid)
            c["body"] = json.loads(request.read())["body"]
            return httpx.Response(200, json=c)
        self.next_id += 1
        made = {"id": self.next_id, "body": json.loads(request.read())["body"], "user": {"type": "Bot"}}
        self.comments.append(made)
        return httpx.Response(201, json=made)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def post(gh, repo="acme/app", pr=7, body=BODY):
    return post_or_update(TOKEN, repo, pr, body, client=gh.client())


def comment(cid, body, kind="Bot"):
    return {"id": cid, "body": body, "user": {"type": kind}}


def test_a_new_comment_is_created_when_there_is_none():
    gh = FakeGithub([comment(1, "looks good", "User")])
    assert post(gh) == ("created", 1001)
    assert gh.comments[-1]["body"] == BODY
    assert gh.calls[-1][:2] == ("POST", "/repos/acme/app/issues/7/comments")


def test_the_comment_posted_before_is_updated_in_place():
    gh = FakeGithub([comment(5, BODY.replace("pass", "fail"))])
    assert post(gh) == ("updated", 5)
    assert [c["body"] for c in gh.comments] == [BODY] and gh.calls[-1][:2] == ("PATCH", "/repos/acme/app/issues/comments/5")


def test_a_comment_from_a_person_is_never_edited_even_if_it_copies_the_marker():
    gh = FakeGithub([comment(5, BODY + " (copied)", "User")])
    assert post(gh)[0] == "created" and gh.comments[0]["body"].endswith("(copied)")


def test_a_bot_comment_that_only_mentions_the_marker_is_left_alone():
    gh = FakeGithub([comment(5, "see " + MARKER, "Bot")])
    assert post(gh)[0] == "created"


def test_the_comment_is_found_on_a_later_page():
    gh = FakeGithub([comment(i, "chat", "User") for i in range(1, 251)] + [comment(999, BODY)])
    assert post(gh) == ("updated", 999)
    assert [c[1] for c in gh.calls if c[0] == "GET"].__len__() == 3


def test_the_token_is_sent_as_a_bearer_header_and_nowhere_else():
    gh = FakeGithub()
    post(gh)
    assert all(auth == f"Bearer {TOKEN}" for _, _, auth in gh.calls)
    assert all(TOKEN not in path for _, path, _ in gh.calls)


@pytest.mark.parametrize("repo", ["acme", "a/b/c", "../etc/passwd", "acme/app/../x", "acme app/x", "acme/app?x=1", "", "/app", "acme/"])
def test_a_bad_repository_name_is_refused_before_any_request(repo):
    gh = FakeGithub()
    with pytest.raises(GithubError, match="not a repository name"):
        post(gh, repo=repo)
    assert gh.calls == []


@pytest.mark.parametrize("pr", [0, -3])
def test_a_bad_pull_request_number_is_refused(pr):
    with pytest.raises(GithubError, match="pull request number"):
        post(FakeGithub(), pr=pr)


def test_only_a_trustbench_comment_can_be_posted():
    with pytest.raises(GithubError, match="marker"):
        post(FakeGithub(), body="Free crypto here")


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_permission_problem_explains_the_likely_cause_and_hides_the_token(status):
    with pytest.raises(GithubError) as e:
        post(FakeGithub(fail=status))
    assert "pull-requests: write" in str(e.value) and "fork" in str(e.value) and TOKEN not in str(e.value)


def test_other_errors_and_network_failures_are_clear_and_leak_nothing():
    with pytest.raises(GithubError, match="500"):
        post(FakeGithub(fail=500))
    with pytest.raises(GithubError) as e:
        post(FakeGithub(fail=httpx.ConnectError("boom to https://api.github.com with Bearer " + TOKEN)))
    assert "Cannot reach GitHub" in str(e.value) and TOKEN not in str(e.value) and "api.github.com" not in str(e.value)


def test_a_failure_while_updating_is_reported_not_swallowed():
    gh = FakeGithub([comment(5, BODY)])
    original = gh.handler

    def failing(request):
        return httpx.Response(403, json={}) if request.method == "PATCH" else original(request)
    with pytest.raises(GithubError, match="update the comment"):
        post_or_update(TOKEN, "acme/app", 7, BODY, client=httpx.Client(transport=httpx.MockTransport(failing)))
