"""跨领域共享的线协议基础模型。"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    """统一拒绝未知字段，并在 JSON 边界使用 camelCase。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class FrozenWireModel(WireModel):
    """用于不可变值对象和事实协议。"""

    model_config = ConfigDict(frozen=True)


def new_entity_id() -> UUID:
    """生成适合数据库索引顺序写入的 UUIDv7。"""

    return uuid7()


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(UTC)
