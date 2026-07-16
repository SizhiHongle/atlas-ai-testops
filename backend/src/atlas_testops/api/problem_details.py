"""HTTP Problem Details 协议和异常处理器。"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.request_context import get_request_id

LOGGER = logging.getLogger(__name__)
PROBLEM_CONTENT_TYPE = "application/problem+json"


class FieldViolation(FrozenWireModel):
    """请求字段的单个校验问题。"""

    field: str
    message: str
    code: str


class ProblemDetails(FrozenWireModel):
    """Atlas 扩展的 RFC Problem Details。"""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    error_code: ErrorCode
    request_id: str
    violations: tuple[FieldViolation, ...] = Field(default_factory=tuple)


def _response(problem: ProblemDetails, headers: dict[str, str] | None = None) -> JSONResponse:
    response_headers = {
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "Vary": "Cookie, Authorization, Origin",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    response_headers.update(headers or {})
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json", by_alias=True),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=response_headers,
    )


def _problem(
    request: Request,
    *,
    status: int,
    error_code: ErrorCode,
    title: str,
    detail: str,
    violations: tuple[FieldViolation, ...] = (),
) -> ProblemDetails:
    return ProblemDetails(
        type=f"https://atlas.test/problems/{error_code.value.lower().replace('_', '-')}",
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        error_code=error_code,
        request_id=get_request_id(),
        violations=violations,
    )


async def handle_application_error(request: Request, error: ApplicationError) -> JSONResponse:
    """转换应用层主动声明的错误。"""

    return _response(
        _problem(
            request,
            status=error.status_code,
            error_code=error.error_code,
            title=error.title,
            detail=error.detail,
        ),
        error.headers,
    )


async def handle_validation_error(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    """把 FastAPI 字段错误转换为稳定且不泄露输入值的协议。"""

    violations = tuple(
        FieldViolation(
            field=".".join(str(part) for part in item["loc"]),
            message=str(item["msg"]),
            code=str(item["type"]),
        )
        for item in error.errors()
    )
    return _response(
        _problem(
            request,
            status=422,
            error_code=ErrorCode.VALIDATION_FAILED,
            title="请求校验失败",
            detail="一个或多个请求字段不符合接口契约。",
            violations=violations,
        )
    )


async def handle_http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
    """统一 FastAPI 和路由层 HTTP 错误。"""

    error_code = ErrorCode.NOT_FOUND if error.status_code == 404 else ErrorCode.INVALID_REQUEST
    return _response(
        _problem(
            request,
            status=error.status_code,
            error_code=error_code,
            title="资源不存在" if error.status_code == 404 else "请求失败",
            detail=str(error.detail),
        ),
        dict(error.headers or {}),
    )


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    """记录未知异常，但不向调用方泄露堆栈和内部实现。"""

    LOGGER.exception("未处理的请求异常", exc_info=error)
    return _response(
        _problem(
            request,
            status=500,
            error_code=ErrorCode.INTERNAL_ERROR,
            title="服务内部错误",
            detail="服务未能完成请求，请携带 requestId 联系维护人员。",
        )
    )


def register_exception_handlers(application: FastAPI) -> None:
    """集中注册异常处理器，防止不同 Router 返回不同错误结构。"""

    application.add_exception_handler(ApplicationError, handle_application_error)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    application.add_exception_handler(StarletteHTTPException, handle_http_error)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, handle_unexpected_error)


def problem_openapi_response(description: str) -> dict[int | str, dict[str, Any]]:
    """为 Endpoint 声明可复用的 Problem Details OpenAPI 响应。"""

    return {
        "default": {
            "description": description,
            "model": ProblemDetails,
        }
    }
