"""认证结果向应用层传递的 Actor Context。"""

from dataclasses import dataclass
from uuid import UUID

from atlas_testops.domain.auth import PlatformRole
from atlas_testops.infrastructure.database import DatabaseContext


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """一项经过 Session 校验的组织级或项目级授权。"""

    role: PlatformRole
    project_id: UUID | None


@dataclass(frozen=True, slots=True)
class ActorContext:
    """只由可信认证依赖构造，不能从业务 Request Body 反序列化。"""

    tenant_id: UUID
    actor_id: UUID | None
    request_id: str
    current_project_id: UUID | None = None
    grants: tuple[AccessGrant, ...] = ()
    development_override: bool = False

    def database_context(self) -> DatabaseContext:
        """转换为事务级 RLS Context。"""

        return DatabaseContext(
            tenant_id=self.tenant_id,
            actor_id=self.actor_id,
            request_id=self.request_id,
        )

    def is_organization_admin(self) -> bool:
        """判断 Actor 是否能管理 Tenant 全部项目。"""

        return self.development_override or any(
            grant.role is PlatformRole.ORG_ADMIN and grant.project_id is None
            for grant in self.grants
        )

    def can_create_project(self) -> bool:
        """Project 创建只允许组织管理员。"""

        return self.is_organization_admin()

    def can_read_project(self, project_id: UUID) -> bool:
        """组织管理员可读全部项目，其他角色只能读取授权项目。"""

        return self.is_organization_admin() or any(
            grant.project_id == project_id for grant in self.grants
        )

    def can_manage_project(self, project_id: UUID) -> bool:
        """Project 管理要求组织管理员或该项目的项目管理员。"""

        return self.is_organization_admin() or any(
            grant.project_id == project_id and grant.role is PlatformRole.PROJECT_ADMIN
            for grant in self.grants
        )

    def can_operate_project(self, project_id: UUID) -> bool:
        """运行账号租约要求组织管理员、项目管理员或运行操作员。"""

        return self.is_organization_admin() or any(
            grant.project_id == project_id
            and grant.role in {PlatformRole.PROJECT_ADMIN, PlatformRole.RUN_OPERATOR}
            for grant in self.grants
        )

    def can_maintain_components(self, project_id: UUID) -> bool:
        """Fixture asset authoring requires a component maintenance role."""

        return self.is_organization_admin() or any(
            grant.project_id == project_id
            and grant.role in {PlatformRole.PROJECT_ADMIN, PlatformRole.COMPONENT_MAINTAINER}
            for grant in self.grants
        )

    def can_publish_components(self, project_id: UUID) -> bool:
        """Publication is separated from authoring and requires reviewer authority."""

        return self.is_organization_admin() or any(
            grant.project_id == project_id
            and grant.role in {PlatformRole.PROJECT_ADMIN, PlatformRole.CASE_REVIEWER}
            for grant in self.grants
        )

    def visible_project_ids(self) -> frozenset[UUID] | None:
        """None 表示允许全部；集合用于 Repository 下推项目过滤。"""

        if self.is_organization_admin():
            return None
        return frozenset(grant.project_id for grant in self.grants if grant.project_id is not None)
