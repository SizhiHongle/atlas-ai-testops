"""Secret Provider 闭包端口；秘密值不得离开回调作用域。"""

from collections.abc import Awaitable, Callable
from typing import Protocol


class PasswordSecret:
    """禁止默认 repr 暴露的进程内用户名/密码材料。"""

    __slots__ = ("__password", "__username")

    def __init__(self, *, username: str, password: str) -> None:
        self.__username = username
        self.__password = password

    def reveal_username(self) -> str:
        """只允许受控 Adapter 在闭包内读取用户名。"""

        return self.__username

    def reveal_password(self) -> str:
        """只允许受控 Adapter 在闭包内读取密码。"""

        return self.__password

    def __repr__(self) -> str:
        return "PasswordSecret(username=**********, password=**********)"


type PasswordSecretOperation[T] = Callable[[PasswordSecret], Awaitable[T]]


class SecretProvider(Protocol):
    """使用闭包解封密码材料，不提供返回 Secret 的读取接口。"""

    async def with_password_secret[T](
        self,
        *,
        secret_ref: str,
        secret_version: str,
        operation: PasswordSecretOperation[T],
    ) -> T: ...


class SecretProviderError(Exception):
    """只携带安全摘要的 Secret Provider 错误。"""


class PasswordSecretScope:
    """Bind a secret locator privately and expose only scoped consumption."""

    __slots__ = ("__provider", "__secret_ref", "__secret_version")

    def __init__(
        self,
        *,
        provider: SecretProvider,
        secret_ref: str,
        secret_version: str,
    ) -> None:
        self.__provider = provider
        self.__secret_ref = secret_ref
        self.__secret_version = secret_version

    async def with_password_secret[T](
        self,
        operation: PasswordSecretOperation[T],
    ) -> T:
        return await self.__provider.with_password_secret(
            secret_ref=self.__secret_ref,
            secret_version=self.__secret_version,
            operation=operation,
        )

    def __repr__(self) -> str:
        return "PasswordSecretScope(**********)"
