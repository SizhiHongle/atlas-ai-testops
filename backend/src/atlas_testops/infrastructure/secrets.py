"""本地开发和测试使用的内存 Secret Provider。"""

from re import fullmatch

from atlas_testops.application.ports.secrets import (
    PasswordSecret,
    PasswordSecretOperation,
    SecretProviderError,
)

LOCAL_PUBLIC_WEB_SECRET_REF = "sec_atlas_local_public_web"
LOCAL_PUBLIC_WEB_SECRET_VERSION = "local-v1"
LOCAL_PUBLIC_WEB_USERNAME = "atlas-public-web@example.test"
LOCAL_PUBLIC_WEB_PASSWORD = "atlas-local-public-web-password"


class LocalDevelopmentSecretProvider:
    """Expose one synthetic credential only for the reviewed local web demo."""

    async def with_password_secret[T](
        self,
        *,
        secret_ref: str,
        secret_version: str,
        operation: PasswordSecretOperation[T],
    ) -> T:
        if (
            secret_ref != LOCAL_PUBLIC_WEB_SECRET_REF
            or secret_version != LOCAL_PUBLIC_WEB_SECRET_VERSION
        ):
            raise SecretProviderError("password material is unavailable")
        return await operation(
            PasswordSecret(
                username=LOCAL_PUBLIC_WEB_USERNAME,
                password=LOCAL_PUBLIC_WEB_PASSWORD,
            )
        )


class InMemorySecretProvider:
    """只在进程内保存测试秘密，并通过闭包完成受控消费。"""

    def __init__(self) -> None:
        self._passwords: dict[tuple[str, str], PasswordSecret] = {}

    def put_password(
        self,
        *,
        secret_ref: str,
        secret_version: str,
        username: str,
        password: str,
    ) -> None:
        """为测试环境注册一份不会进入数据库的密码材料。"""

        if fullmatch(r"sec_[A-Za-z0-9_-]{8,200}", secret_ref) is None:
            raise ValueError("secret_ref must be an opaque Atlas reference")
        if not secret_version.strip():
            raise ValueError("secret_version must not be blank")
        self._passwords[(secret_ref, secret_version)] = PasswordSecret(
            username=username,
            password=password,
        )

    async def with_password_secret[T](
        self,
        *,
        secret_ref: str,
        secret_version: str,
        operation: PasswordSecretOperation[T],
    ) -> T:
        """在单个 await 作用域内把秘密交给受控操作。"""

        secret = self._passwords.get((secret_ref, secret_version))
        if secret is None:
            raise SecretProviderError("password material is unavailable")
        return await operation(secret)
