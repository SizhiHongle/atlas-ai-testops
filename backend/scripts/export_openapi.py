"""导出供前端生成类型的稳定 OpenAPI 文档。"""

import argparse
import json
from pathlib import Path

from atlas_testops.core.config import Settings
from atlas_testops.main import create_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_DOCUMENT = REPOSITORY_ROOT / "contracts" / "openapi.json"


def render_openapi() -> str:
    """使用无外部依赖的确定性配置生成 OpenAPI。"""

    application = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            docs_enabled=True,
            database_url=None,
        )
    )
    return json.dumps(
        application.openapi(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> None:
    """写入 OpenAPI，或在 CI 中检查契约漂移。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_openapi()

    if args.check:
        if not OPENAPI_DOCUMENT.exists():
            raise SystemExit(f"Missing generated OpenAPI document: {OPENAPI_DOCUMENT}")
        if OPENAPI_DOCUMENT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Generated OpenAPI document is stale")
        return

    OPENAPI_DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_DOCUMENT.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
