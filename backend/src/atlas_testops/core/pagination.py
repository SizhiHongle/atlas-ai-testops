"""不透明 Cursor 编解码。"""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, ValidationError

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.core.errors import ApplicationError, ErrorCode


class TimeCursor(FrozenWireModel):
    """由稳定排序键构成的内部 Cursor。"""

    created_at: AwareDatetime
    id: UUID


def encode_cursor(cursor: TimeCursor) -> str:
    """把内部排序键编码为无填充 Base64URL。"""

    payload = json.dumps(
        cursor.model_dump(mode="json", by_alias=True),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(value: str | None) -> TimeCursor | None:
    """解析不可信 Cursor，并统一转换为 400 错误。"""

    if value is None:
        return None
    try:
        if len(value) > 512:
            raise ValueError("cursor is too long")
        padded = value + "=" * (-len(value) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        parsed = json.loads(payload)
        return TimeCursor.model_validate(parsed)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error, ValidationError):
        raise ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="Cursor 无效",
            detail="分页 Cursor 已损坏或不属于当前接口。",
            status_code=400,
        ) from None


def next_time_cursor(created_at: datetime, entity_id: UUID) -> str:
    """由当前页最后一条记录生成下一页 Cursor。"""

    return encode_cursor(TimeCursor(created_at=created_at, id=entity_id))
