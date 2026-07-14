"""Revision ETag 单元测试。"""

import pytest

from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.core.errors import ApplicationError, ErrorCode


def test_revision_etag_round_trip() -> None:
    assert format_revision_etag(12) == '"revision-12"'
    assert parse_revision_etag('  "revision-12"  ') == 12


@pytest.mark.parametrize(
    "value",
    ["*", 'W/"revision-1"', '"revision-0"', '"revision-x"', "revision-1"],
)
def test_revision_etag_rejects_weak_or_malformed_values(value: str) -> None:
    with pytest.raises(ApplicationError) as captured:
        parse_revision_etag(value)

    assert captured.value.error_code is ErrorCode.INVALID_REQUEST
    assert captured.value.status_code == 400
