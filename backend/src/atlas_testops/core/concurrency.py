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


def format_control_epoch_etag(control_epoch: int) -> str:
    """Generate the strong ETag used by live-control transitions."""

    if control_epoch < 1:
        raise ValueError("control epoch must be positive")
    return f'"control-epoch-{control_epoch}"'


def parse_control_epoch_etag(value: str) -> int:
    """Parse an exact live-control epoch and reject weak or wildcard ETags."""

    matched = fullmatch(r'"control-epoch-([1-9][0-9]*)"', value.strip())
    if matched is None:
        raise ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="If-Match 格式无效",
            detail='If-Match 必须使用形如 "control-epoch-7" 的强 ETag。',
            status_code=400,
        )
    return int(matched.group(1))
