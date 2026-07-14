"""幂等请求摘要测试。"""

from atlas_testops.infrastructure.idempotency import hash_request


def test_request_hash_is_independent_of_object_key_order() -> None:
    first = hash_request({"name": "Atlas", "count": 2})
    second = hash_request({"count": 2, "name": "Atlas"})

    assert first == second
    assert len(first) == 64


def test_request_hash_changes_with_payload() -> None:
    assert hash_request({"count": 1}) != hash_request({"count": 2})
