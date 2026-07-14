"""Platform 领域 PostgreSQL Repository。"""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.platform import (
    CreateEnvironment,
    CreateProject,
    CreateTenant,
    Environment,
    Project,
    Tenant,
    UpdateEnvironment,
    UpdateProject,
)

TENANT_COLUMNS = "id, slug, name, status, revision, created_at, updated_at"
PROJECT_COLUMNS = (
    "id, tenant_id, project_key, name, status, revision, created_at, updated_at"
)
ENVIRONMENT_COLUMNS = (
    "id, tenant_id, project_id, environment_key, name, kind, status, "
    "allowed_origins, revision, created_at, updated_at"
)


class PlatformRepository:
    """只处理持久化，不决定权限、审计和事件语义。"""

    async def create_tenant(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        command: CreateTenant,
    ) -> Tenant | None:
        """创建 Tenant；Slug 冲突时返回 None。"""

        cursor = await connection.execute(
            f"""
            insert into atlas.tenant (id, slug, name)
            values (%s, %s, %s)
            on conflict (slug) do nothing
            returning {TENANT_COLUMNS}
            """,
            (tenant_id, command.slug, command.name.strip()),
        )
        row = await cursor.fetchone()
        return Tenant.model_validate(row) if row is not None else None

    async def get_tenant(
        self,
        connection: AsyncConnection[DictRow],
        tenant_id: UUID,
    ) -> Tenant | None:
        """读取当前 RLS 上下文可见的 Tenant。"""

        cursor = await connection.execute(
            f"select {TENANT_COLUMNS} from atlas.tenant where id = %s",
            (tenant_id,),
        )
        row = await cursor.fetchone()
        return Tenant.model_validate(row) if row is not None else None

    async def create_project(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        tenant_id: UUID,
        command: CreateProject,
    ) -> Project | None:
        """创建 Project；同 Tenant Key 冲突时返回 None。"""

        cursor = await connection.execute(
            f"""
            insert into atlas.project (id, tenant_id, project_key, name)
            values (%s, %s, %s, %s)
            on conflict (tenant_id, project_key) do nothing
            returning {PROJECT_COLUMNS}
            """,
            (project_id, tenant_id, command.project_key, command.name.strip()),
        )
        row = await cursor.fetchone()
        return Project.model_validate(row) if row is not None else None

    async def get_project(
        self,
        connection: AsyncConnection[DictRow],
        project_id: UUID,
    ) -> Project | None:
        """按 ID 读取当前 Tenant 可见的 Project。"""

        cursor = await connection.execute(
            f"select {PROJECT_COLUMNS} from atlas.project where id = %s",
            (project_id,),
        )
        row = await cursor.fetchone()
        return Project.model_validate(row) if row is not None else None

    async def list_projects(
        self,
        connection: AsyncConnection[DictRow],
        *,
        cursor: TimeCursor | None,
        limit: int,
        allowed_project_ids: frozenset[UUID] | None,
    ) -> tuple[Project, ...]:
        """按 created_at / id 倒序读取 limit + 1 条记录。"""

        if allowed_project_ids is None and cursor is None:
            result = await connection.execute(
                f"""
                select {PROJECT_COLUMNS}
                from atlas.project
                order by created_at desc, id desc
                limit %s
                """,
                (limit + 1,),
            )
        elif allowed_project_ids is None:
            assert cursor is not None
            result = await connection.execute(
                f"""
                select {PROJECT_COLUMNS}
                from atlas.project
                where (created_at, id) < (%s, %s)
                order by created_at desc, id desc
                limit %s
                """,
                (cursor.created_at, cursor.id, limit + 1),
            )
        elif cursor is None:
            result = await connection.execute(
                f"""
                select {PROJECT_COLUMNS}
                from atlas.project
                where id = any(%s)
                order by created_at desc, id desc
                limit %s
                """,
                (list(allowed_project_ids), limit + 1),
            )
        else:
            result = await connection.execute(
                f"""
                select {PROJECT_COLUMNS}
                from atlas.project
                where id = any(%s) and (created_at, id) < (%s, %s)
                order by created_at desc, id desc
                limit %s
                """,
                (
                    list(allowed_project_ids),
                    cursor.created_at,
                    cursor.id,
                    limit + 1,
                ),
            )
        return tuple(Project.model_validate(row) for row in await result.fetchall())

    async def update_project(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        expected_revision: int,
        command: UpdateProject,
    ) -> Project | None:
        """使用 Revision CAS 更新 Project。"""

        cursor = await connection.execute(
            f"""
            update atlas.project
            set name = coalesce(%s, name),
                status = coalesce(%s, status),
                revision = revision + 1
            where id = %s and revision = %s
            returning {PROJECT_COLUMNS}
            """,
            (
                command.name.strip() if command.name is not None else None,
                command.status,
                project_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return Project.model_validate(row) if row is not None else None

    async def create_environment(
        self,
        connection: AsyncConnection[DictRow],
        *,
        environment_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        command: CreateEnvironment,
    ) -> Environment | None:
        """创建 Environment；同 Project Key 冲突时返回 None。"""

        cursor = await connection.execute(
            f"""
            insert into atlas.environment (
              id, tenant_id, project_id, environment_key, name, kind, allowed_origins
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, project_id, environment_key) do nothing
            returning {ENVIRONMENT_COLUMNS}
            """,
            (
                environment_id,
                tenant_id,
                project_id,
                command.environment_key,
                command.name.strip(),
                command.kind,
                list(command.allowed_origins),
            ),
        )
        row = await cursor.fetchone()
        return Environment.model_validate(row) if row is not None else None

    async def get_environment(
        self,
        connection: AsyncConnection[DictRow],
        environment_id: UUID,
    ) -> Environment | None:
        """按 ID 读取当前 Tenant 可见的 Environment。"""

        cursor = await connection.execute(
            f"select {ENVIRONMENT_COLUMNS} from atlas.environment where id = %s",
            (environment_id,),
        )
        row = await cursor.fetchone()
        return Environment.model_validate(row) if row is not None else None

    async def get_environment_for_share(
        self,
        connection: AsyncConnection[DictRow],
        environment_id: UUID,
    ) -> Environment | None:
        """共享锁定 Environment，阻止 Origin 或状态与敏感操作交错。"""

        cursor = await connection.execute(
            f"""
            select {ENVIRONMENT_COLUMNS}
            from atlas.environment
            where id = %s
            for share
            """,
            (environment_id,),
        )
        row = await cursor.fetchone()
        return Environment.model_validate(row) if row is not None else None

    async def get_environment_for_update(
        self,
        connection: AsyncConnection[DictRow],
        environment_id: UUID,
    ) -> Environment | None:
        """排他锁定 Environment，供策略和子资源依赖检查。"""

        cursor = await connection.execute(
            f"""
            select {ENVIRONMENT_COLUMNS}
            from atlas.environment
            where id = %s
            for update
            """,
            (environment_id,),
        )
        row = await cursor.fetchone()
        return Environment.model_validate(row) if row is not None else None

    async def list_environments(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[Environment, ...]:
        """按 Project 和稳定 Cursor 顺序读取 Environment。"""

        if cursor is None:
            result = await connection.execute(
                f"""
                select {ENVIRONMENT_COLUMNS}
                from atlas.environment
                where project_id = %s
                order by created_at desc, id desc
                limit %s
                """,
                (project_id, limit + 1),
            )
        else:
            result = await connection.execute(
                f"""
                select {ENVIRONMENT_COLUMNS}
                from atlas.environment
                where project_id = %s and (created_at, id) < (%s, %s)
                order by created_at desc, id desc
                limit %s
                """,
                (project_id, cursor.created_at, cursor.id, limit + 1),
            )
        return tuple(Environment.model_validate(row) for row in await result.fetchall())

    async def update_environment(
        self,
        connection: AsyncConnection[DictRow],
        *,
        environment_id: UUID,
        expected_revision: int,
        command: UpdateEnvironment,
    ) -> Environment | None:
        """使用 Revision CAS 更新 Environment。"""

        cursor = await connection.execute(
            f"""
            update atlas.environment
            set name = coalesce(%s, name),
                status = coalesce(%s, status),
                allowed_origins = coalesce(%s, allowed_origins),
                revision = revision + 1
            where id = %s and revision = %s
            returning {ENVIRONMENT_COLUMNS}
            """,
            (
                command.name.strip() if command.name is not None else None,
                command.status,
                (
                    list(command.allowed_origins)
                    if command.allowed_origins is not None
                    else None
                ),
                environment_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return Environment.model_validate(row) if row is not None else None
