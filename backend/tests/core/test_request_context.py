"""Request ID 上下文测试。"""

from atlas_testops.core.request_context import (
    get_request_id,
    normalize_request_id,
    reset_request_id,
    set_request_id,
)


def test_preserves_valid_upstream_request_id() -> None:
    assert normalize_request_id("ci/run-42") == "ci/run-42"


def test_replaces_unsafe_request_id() -> None:
    generated = normalize_request_id("unsafe request id\n")

    assert generated != "unsafe request id"
    assert len(generated) == 36


def test_request_id_context_is_restored() -> None:
    token = set_request_id("request-1")
    try:
        assert get_request_id() == "request-1"
    finally:
        reset_request_id(token)

    assert get_request_id() != "request-1"
