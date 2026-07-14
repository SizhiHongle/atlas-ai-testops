"""请求级上下文。"""

from contextvars import ContextVar, Token
from re import compile as compile_pattern
from uuid import uuid7

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = compile_pattern(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

_request_id: ContextVar[str | None] = ContextVar("atlas_request_id", default=None)


def normalize_request_id(candidate: str | None) -> str:
    """只接受有限字符的上游 Request ID，否则创建新的 UUIDv7。"""

    if candidate is not None:
        normalized = candidate.strip()
        if REQUEST_ID_PATTERN.fullmatch(normalized):
            return normalized
    return str(uuid7())


def set_request_id(request_id: str) -> Token[str | None]:
    """将 Request ID 写入当前异步上下文。"""

    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """在请求结束后恢复父上下文。"""

    _request_id.reset(token)


def get_request_id() -> str:
    """取得当前 Request ID；非请求上下文也返回可追踪值。"""

    current = _request_id.get()
    return current if current is not None else str(uuid7())
