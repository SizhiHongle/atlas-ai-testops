"""平台用户密码的 Argon2id 单向保护。"""

from dataclasses import dataclass

from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    """密码校验结果与渐进式参数升级信号。"""

    valid: bool
    needs_rehash: bool = False


class PasswordService:
    """集中配置 Password Hash，避免业务代码自行选择弱算法。"""

    def __init__(
        self,
        *,
        memory_cost_kib: int = 19_456,
        time_cost: int = 2,
        parallelism: int = 1,
        maximum_concurrency: int = 4,
    ) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost_kib,
            parallelism=parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._limiter = CapacityLimiter(maximum_concurrency)
        self._dummy_hash = (
            "$argon2id$v=19$m=19456,t=2,p=1$8eFR3ldPMn7/WuONkNRnkQ$"
            "czlSu1SgALIQLQ7zSRudSp0cdEWHKDRP8ehaENUuooo"
        )

    @property
    def dummy_hash(self) -> str:
        """未知用户也执行同类计算，降低账号枚举的时序差异。"""

        return self._dummy_hash

    def hash_password(self, password: str) -> str:
        """使用随机 Salt 生成自描述 Argon2id Hash。"""

        return self._hasher.hash(password)

    async def hash_password_async(self, password: str) -> str:
        """在线程池执行内存密集计算，并限制单进程并发。"""

        return await to_thread.run_sync(
            self.hash_password,
            password,
            limiter=self._limiter,
        )

    def verify_password(self, password_hash: str, password: str) -> PasswordVerification:
        """校验密码；损坏 Hash 与密码不匹配都不泄露内部差异。"""

        try:
            valid = self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return PasswordVerification(valid=False)
        return PasswordVerification(
            valid=valid,
            needs_rehash=valid and self._hasher.check_needs_rehash(password_hash),
        )

    async def verify_password_async(
        self,
        password_hash: str,
        password: str,
    ) -> PasswordVerification:
        """异步请求不在 Event Loop 上执行 Password Hash。"""

        return await to_thread.run_sync(
            self.verify_password,
            password_hash,
            password,
            limiter=self._limiter,
        )
