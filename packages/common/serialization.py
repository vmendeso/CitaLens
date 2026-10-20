import uuid
from datetime import date, datetime
from enum import Enum
from sqlalchemy import inspect


def jsonable(value):
    if isinstance(value, (uuid.UUID, date, datetime)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def record(obj):
    return {attr.key: jsonable(getattr(obj, attr.key)) for attr in inspect(obj).mapper.column_attrs}
