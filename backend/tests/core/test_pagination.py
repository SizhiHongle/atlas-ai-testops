"""不透明 Cursor 单元测试。"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import TimeCursor, decode_cursor, encode_cursor


def test_time_cursor_round_trip() -> None:
    cursor = TimeCursor(
        created_at=datetime(2026, 7, 13, 9, 30, tzinfo=UTC),
        id=uuid7(),
    )

    encoded = encode_cursor(cursor)

    assert "=" not in encoded
    assert decode_cursor(encoded) == cursor
    assert decode_cursor(None) is None


@pytest.mark.parametrize("value", ["%%%", "e30", "a" * 513])
def test_decode_cursor_returns_stable_error(value: str) -> None:
    with pytest.raises(ApplicationError) as captured:
        decode_cursor(value)

    assert captured.value.error_code is ErrorCode.INVALID_REQUEST
    assert captured.value.status_code == 400
