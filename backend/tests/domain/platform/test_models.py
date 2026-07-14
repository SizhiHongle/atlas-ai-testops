"""Platform 命令与领域模型测试。"""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from atlas_testops.domain.platform import (
    CreateEnvironment,
    CreateProject,
    CreateTenant,
    EnvironmentKind,
    ProjectStatus,
    UpdateEnvironment,
    UpdateProject,
)


def test_commands_strip_user_facing_text() -> None:
    tenant = CreateTenant(slug=" atlas-dev ", name=" Atlas Dev ")
    project = CreateProject(project_key=" ATLAS_CORE ", name=" Control Plane ")
    environment = CreateEnvironment(
        environment_key=" dev-east ",
        name=" Dev East ",
        kind=EnvironmentKind.TEST,
    )

    assert tenant.model_dump(by_alias=True) == {"slug": "atlas-dev", "name": "Atlas Dev"}
    assert project.project_key == "ATLAS_CORE"
    assert project.name == "Control Plane"
    assert environment.environment_key == "dev-east"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CreateTenant(slug="atlas-dev", name="   "),
        lambda: CreateProject(project_key="ATLAS_CORE", name="   "),
        lambda: UpdateProject(name="   "),
        lambda: UpdateEnvironment(name="   "),
    ],
)
def test_commands_reject_blank_names(factory: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_patch_requires_at_least_one_change() -> None:
    with pytest.raises(ValidationError):
        UpdateProject()
    with pytest.raises(ValidationError):
        UpdateEnvironment()

    assert UpdateProject(status=ProjectStatus.ARCHIVED).status is ProjectStatus.ARCHIVED
