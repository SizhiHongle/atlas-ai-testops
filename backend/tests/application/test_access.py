"""平台 RBAC Actor Context 测试。"""

from uuid import uuid7

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.domain.auth import PlatformRole


def actor_with(*grants: AccessGrant, development_override: bool = False) -> ActorContext:
    return ActorContext(
        tenant_id=uuid7(),
        actor_id=uuid7(),
        request_id="access-test",
        grants=grants,
        development_override=development_override,
    )


def test_development_override_retains_bootstrap_capabilities() -> None:
    actor = actor_with(development_override=True)
    project_id = uuid7()

    assert actor.is_organization_admin()
    assert actor.can_create_project()
    assert actor.can_read_project(project_id)
    assert actor.can_manage_project(project_id)
    assert actor.visible_project_ids() is None


def test_organization_admin_can_manage_all_projects() -> None:
    actor = actor_with(AccessGrant(role=PlatformRole.ORG_ADMIN, project_id=None))

    assert actor.can_manage_project(uuid7())
    assert actor.visible_project_ids() is None


def test_observer_can_only_read_the_authorized_project() -> None:
    project_id = uuid7()
    other_project_id = uuid7()
    actor = actor_with(AccessGrant(role=PlatformRole.OBSERVER, project_id=project_id))

    assert actor.can_create_project() is False
    assert actor.can_read_project(project_id)
    assert actor.can_read_project(other_project_id) is False
    assert actor.can_manage_project(project_id) is False
    assert actor.visible_project_ids() == frozenset({project_id})


def test_project_admin_can_manage_only_its_project() -> None:
    project_id = uuid7()
    actor = actor_with(AccessGrant(role=PlatformRole.PROJECT_ADMIN, project_id=project_id))

    assert actor.can_manage_project(project_id)
    assert actor.can_manage_project(uuid7()) is False
