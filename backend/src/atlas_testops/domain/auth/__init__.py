"""Atlas 平台主体与 Session 领域对象。"""

from atlas_testops.domain.auth.models import (
    AuthenticationMethod,
    BootstrapPrincipal,
    BootstrapPrincipalCommand,
    LoginCommand,
    MembershipStatus,
    PlatformMembership,
    PlatformRole,
    PlatformSessionView,
    PlatformUser,
    PlatformUserStatus,
    normalize_email_address,
)

__all__ = [
    "AuthenticationMethod",
    "BootstrapPrincipal",
    "BootstrapPrincipalCommand",
    "LoginCommand",
    "MembershipStatus",
    "PlatformMembership",
    "PlatformRole",
    "PlatformSessionView",
    "PlatformUser",
    "PlatformUserStatus",
    "normalize_email_address",
]
