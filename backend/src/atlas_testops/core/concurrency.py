"""HTTP 乐观并发控制。"""

from re import fullmatch

from atlas_testops.core.errors import ApplicationError, ErrorCode


def format_revision_etag(revision: int) -> str:
    """生成强 ETag，明确表示资源 Revision。"""

    return f'"revision-{revision}"'


def parse_revision_etag(value: str) -> int:
    """解析 If-Match，并拒绝通配符和弱 ETag。"""

    matched = fullmatch(r'"revision-([1-9][0-9]*)"', value.strip())
    if matched is None:
        raise ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="If-Match 格式无效",
            detail='If-Match 必须使用形如 "revision-3" 的强 ETag。',
            status_code=400,
        )
    return int(matched.group(1))
