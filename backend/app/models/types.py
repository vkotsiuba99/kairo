import json
from typing import Any, Optional, List
from sqlalchemy import TypeDecorator, JSON, Text
from sqlalchemy.dialects.postgresql import JSONB


class CompatibleJSON(TypeDecorator):
    """
    Uses JSONB on PostgreSQL and JSON on SQLite.
    """
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class VectorType(TypeDecorator):
    """
    Stores 768-dimensional float embedding vector.
    Under PostgreSQL with pgvector, compiles to vector(768).
    Under SQLite / fallback, stores JSON-serialized list of floats.
    """
    impl = Text
    cache_ok = True

    def __init__(self, dim: int = 768, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector
                return dialect.type_descriptor(Vector(self.dim))
            except ImportError:
                return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Optional[Any], dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            if dialect.name == "postgresql":
                return list(value)
            return json.dumps(list(value))
        return value

    def process_result_value(self, value: Optional[Any], dialect) -> Optional[List[float]]:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return [float(x.strip()) for x in value.strip("[]").split(",") if x.strip()]
        if isinstance(value, (list, tuple)):
            return [float(x) for x in value]
        # numpy array support
        if hasattr(value, "tolist"):
            return value.tolist()
        return value


class JsonList(TypeDecorator):
    """
    Stores a list (e.g. hashtags text[]) as JSON or text array.
    """
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Optional[Any], dialect) -> Any:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return value

    def process_result_value(self, value: Optional[Any], dialect) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                res = json.loads(value)
                return list(res) if isinstance(res, list) else [value]
            except Exception:
                return [value]
        if isinstance(value, (list, tuple)):
            return list(value)
        return []
