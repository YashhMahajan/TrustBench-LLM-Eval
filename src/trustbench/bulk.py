"""Insert many rows with one statement.

The usual way (session.add for each object, then flush) sends one INSERT for each row when the database must
return the new ids. That is how SQLite works with SQLAlchemy. This helper sends all rows in one call. It does not read
the ids back: ask for them with one query afterwards, using a key that you know (for example the trace_id).
"""

from sqlalchemy import insert
from sqlalchemy.orm import Session


def _row(obj, table) -> dict:
    out = {}
    for col in table.columns:
        if col.primary_key and getattr(obj, col.key, None) is None:
            continue
        value = getattr(obj, col.key, None)
        if value is None and (col.default is not None or col.server_default is not None):
            continue                       # Let the column default apply, for example created_at.
        out[col.key] = value
    return out


def insert_many(session: Session, model, objects, chunk: int = 2000) -> None:
    objects = list(objects)
    for i in range(0, len(objects), chunk):
        session.execute(insert(model), [_row(o, model.__table__) for o in objects[i:i + chunk]])
