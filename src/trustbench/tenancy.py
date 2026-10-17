"""Workspaces, API keys and roles.

Every request to the server carries an API key. The key belongs to one workspace and has one role:
  read    see data
  write   send traces and change the review state
  admin   change settings and manage keys
A key can see only the data of its own workspace.
"""

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .db import ApiKey, Workspace, utcnow

ROLES = ("read", "write", "admin")
KEY_PREFIX = "tb_"
DEFAULT_WORKSPACE = "default"


class TenancyError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    workspace_id: str
    key_id: int
    role: str

    def allows(self, needed: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(needed)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_workspace(factory: sessionmaker, name: str, settings: dict | None = None) -> Workspace:
    name = name.strip()
    if not name:
        raise TenancyError("The workspace needs a name.")
    with factory.begin() as s:
        if s.scalar(select(Workspace).where(Workspace.name == name)):
            raise TenancyError(f"A workspace named '{name}' exists.")
        ws = Workspace(id=uuid.uuid4().hex[:12], name=name, settings=settings or {})
        s.add(ws)
        s.flush()
        return ws


def get_workspace(factory: sessionmaker, name_or_id: str) -> Workspace:
    with factory() as s:
        ws = s.get(Workspace, name_or_id) or s.scalar(select(Workspace).where(Workspace.name == name_or_id))
        if ws is None:
            raise TenancyError(f"No workspace '{name_or_id}'.")
        return ws


def ensure_default_workspace(factory: sessionmaker) -> Workspace:
    try:
        return get_workspace(factory, DEFAULT_WORKSPACE)
    except TenancyError:
        return create_workspace(factory, DEFAULT_WORKSPACE)


def list_workspaces(factory: sessionmaker) -> list[Workspace]:
    with factory() as s:
        return list(s.scalars(select(Workspace).order_by(Workspace.name)))


def create_api_key(factory: sessionmaker, workspace_id: str, name: str, role: str = "write") -> tuple[ApiKey, str]:
    """Returns the stored row and the key. The key cannot be read again, so show it now."""
    if role not in ROLES:
        raise TenancyError(f"Role must be one of: {', '.join(ROLES)}.")
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    with factory.begin() as s:
        if s.get(Workspace, workspace_id) is None:
            raise TenancyError(f"No workspace '{workspace_id}'.")
        row = ApiKey(workspace_id=workspace_id, name=name, prefix=token[:11], key_hash=_hash(token), role=role)
        s.add(row)
        s.flush()
        return row, token


def authenticate(factory: sessionmaker, token: str | None) -> Principal | None:
    """The principal for a valid, active key. None for anything else. The answer never says why."""
    if not token or not token.startswith(KEY_PREFIX):
        return None
    candidate = _hash(token)
    with factory.begin() as s:
        for row in s.scalars(select(ApiKey).where(ApiKey.prefix == token[:11], ApiKey.revoked_at.is_(None))):
            if hmac.compare_digest(row.key_hash, candidate):
                now = utcnow()
                if row.last_used_at is None or (now - row.last_used_at) > dt.timedelta(minutes=1):
                    row.last_used_at = now
                return Principal(row.workspace_id, row.id, row.role)
    return None


def revoke_api_key(factory: sessionmaker, key_id: int) -> None:
    with factory.begin() as s:
        row = s.get(ApiKey, key_id)
        if row is None:
            raise TenancyError(f"No key {key_id}.")
        row.revoked_at = row.revoked_at or utcnow()


def list_api_keys(factory: sessionmaker, workspace_id: str | None = None) -> list[ApiKey]:
    with factory() as s:
        q = select(ApiKey).order_by(ApiKey.id)
        if workspace_id:
            q = q.where(ApiKey.workspace_id == workspace_id)
        return list(s.scalars(q))


def owned(column, ws: Workspace):
    """A filter for the rows of a workspace. Old rows without a workspace belong to the workspace 'default'."""
    from sqlalchemy import or_
    return or_(column == ws.id, column.is_(None)) if ws.name == DEFAULT_WORKSPACE else column == ws.id
