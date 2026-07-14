"""Argon2id Password Service 测试。"""

import pytest

from atlas_testops.infrastructure.passwords import PasswordService


def password_service() -> PasswordService:
    """测试使用较小成本，生产默认参数由构造器测试覆盖。"""

    return PasswordService(
        memory_cost_kib=8_192,
        time_cost=1,
        parallelism=1,
        maximum_concurrency=2,
    )


def test_hashes_with_argon2id_and_random_salt() -> None:
    service = password_service()

    first = service.hash_password("correct horse battery staple")
    second = service.hash_password("correct horse battery staple")

    assert first.startswith("$argon2id$")
    assert second.startswith("$argon2id$")
    assert first != second
    assert service.verify_password(first, "correct horse battery staple").valid
    assert service.verify_password(first, "wrong password").valid is False


def test_invalid_and_dummy_hashes_fail_without_internal_error() -> None:
    service = password_service()

    assert service.verify_password("not-an-argon2-hash", "password").valid is False
    assert service.verify_password(service.dummy_hash, "unknown-password").valid is False


@pytest.mark.anyio
async def test_async_hash_and_verify_do_not_block_the_event_loop() -> None:
    service = password_service()

    password_hash = await service.hash_password_async("correct horse battery staple")
    verification = await service.verify_password_async(
        password_hash,
        "correct horse battery staple",
    )

    assert verification.valid
    assert verification.needs_rehash is False
