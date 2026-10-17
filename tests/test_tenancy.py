import pytest
from sqlalchemy import select

from trustbench.db import ApiKey
from trustbench.tenancy import (
    KEY_PREFIX, Principal, TenancyError, authenticate, create_api_key, create_workspace, ensure_default_workspace,
    get_workspace, list_api_keys, list_workspaces, revoke_api_key,
)


def test_workspace_names_are_unique_and_found_by_name_or_id(factory):
    a = create_workspace(factory, "acme")
    assert get_workspace(factory, "acme").id == a.id and get_workspace(factory, a.id).name == "acme"
    with pytest.raises(TenancyError, match="exists"):
        create_workspace(factory, "acme")
    with pytest.raises(TenancyError, match="needs a name"):
        create_workspace(factory, "  ")
    with pytest.raises(TenancyError, match="No workspace"):
        get_workspace(factory, "nobody")


def test_default_workspace_is_made_once(factory):
    assert ensure_default_workspace(factory).id == ensure_default_workspace(factory).id
    assert [w.name for w in list_workspaces(factory)] == ["default"]


def test_the_key_is_returned_once_and_only_its_hash_is_stored(factory):
    ws = create_workspace(factory, "acme")
    row, token = create_api_key(factory, ws.id, "ci", "write")
    assert token.startswith(KEY_PREFIX) and len(token) > 40
    with factory() as s:
        stored = s.scalars(select(ApiKey)).one()
    assert token not in (stored.key_hash, stored.prefix, stored.name) and stored.key_hash != token and len(stored.key_hash) == 64
    assert stored.prefix == token[:11]


def test_authenticate_gives_the_workspace_and_role(factory):
    ws = create_workspace(factory, "acme")
    row, token = create_api_key(factory, ws.id, "ci", "read")
    p = authenticate(factory, token)
    assert p == Principal(ws.id, row.id, "read")
    assert p.allows("read") and not p.allows("write") and not p.allows("admin")


@pytest.mark.parametrize("bad", [None, "", "nope", "tb_", "tb_wrongwrongwrongwrongwrong", "Bearer x"])
def test_bad_tokens_are_refused(factory, bad):
    create_api_key(factory, create_workspace(factory, "acme").id, "ci")
    assert authenticate(factory, bad) is None


def test_a_token_with_the_right_prefix_but_other_content_is_refused(factory):
    _, token = create_api_key(factory, create_workspace(factory, "acme").id, "ci")
    assert authenticate(factory, token[:11] + "x" * 30) is None
    assert authenticate(factory, token + "x") is None


def test_revoked_keys_stop_working_and_others_do_not(factory):
    ws = create_workspace(factory, "acme")
    k1, t1 = create_api_key(factory, ws.id, "one")
    k2, t2 = create_api_key(factory, ws.id, "two")
    revoke_api_key(factory, k1.id)
    revoke_api_key(factory, k1.id)           # Twice is fine.
    assert authenticate(factory, t1) is None and authenticate(factory, t2).key_id == k2.id
    assert [k.revoked_at is not None for k in list_api_keys(factory, ws.id)] == [True, False]
    with pytest.raises(TenancyError, match="No key"):
        revoke_api_key(factory, 999)


def test_keys_of_two_workspaces_stay_apart(factory):
    a, b = create_workspace(factory, "a"), create_workspace(factory, "b")
    _, ta = create_api_key(factory, a.id, "k")
    _, tb = create_api_key(factory, b.id, "k")
    assert authenticate(factory, ta).workspace_id == a.id and authenticate(factory, tb).workspace_id == b.id
    assert len(list_api_keys(factory, a.id)) == 1


def test_role_and_workspace_are_checked_when_a_key_is_made(factory):
    ws = create_workspace(factory, "acme")
    with pytest.raises(TenancyError, match="Role must be"):
        create_api_key(factory, ws.id, "x", "owner")
    with pytest.raises(TenancyError, match="No workspace"):
        create_api_key(factory, "nope", "x")


def test_last_use_is_recorded(factory):
    ws = create_workspace(factory, "acme")
    row, token = create_api_key(factory, ws.id, "ci")
    assert list_api_keys(factory)[0].last_used_at is None
    authenticate(factory, token)
    assert list_api_keys(factory)[0].last_used_at is not None
