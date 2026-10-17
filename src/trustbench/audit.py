from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .db import AuditLog
from .tenancy import Principal


def record(factory: sessionmaker, principal: Principal, action: str, target: str = "") -> None:
    with factory.begin() as s:
        s.add(AuditLog(workspace_id=principal.workspace_id, key_id=principal.key_id, action=action, target=target[:200]))


def entries(factory: sessionmaker, workspace_id: str, limit: int = 200) -> list[AuditLog]:
    with factory() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.workspace_id == workspace_id).order_by(AuditLog.id.desc()).limit(limit)))
