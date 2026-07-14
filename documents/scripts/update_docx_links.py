"""Replace stale hyperlinks and accessibility labels in Atlas DOCX documents."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS_DIR = REPOSITORY_ROOT / "documents"

LINK_REPLACEMENTS: dict[str, str] = {
    "https://clickhouse.com/docs/integrations/javascript": (
        "https://clickhouse.com/docs/integrations/python"
    ),
    "https://docs.temporal.io/develop/typescript/workflows/schedules": (
        "https://docs.temporal.io/develop/python/workflows/schedules"
    ),
    "https://docs.temporal.io/develop/typescript/cancellation": (
        "https://docs.temporal.io/develop/python/workflows/cancellation"
    ),
    "https://docs.temporal.io/develop/typescript/workflows/message-passing": (
        "https://docs.temporal.io/develop/python/workflows/message-passing"
    ),
    "https://nodejs.org/en/about/previous-releases": (
        "https://devguide.python.org/versions/"
    ),
    "https://opentelemetry.io/docs/languages/js/getting-started/nodejs/": (
        "https://opentelemetry.io/docs/languages/python/getting-started/"
    ),
}

XML_REPLACEMENTS: tuple[tuple[re.Pattern[bytes], bytes], ...] = (
    (re.compile(rb"UnitUnitAttempt"), b"UnitAttempt"),
    (re.compile(rb"unit_unit_attempt", re.IGNORECASE), b"unit_attempt"),
    (re.compile(rb"unit-unit-attempt", re.IGNORECASE), b"unit-attempt"),
    (re.compile(rb"(?<![A-Za-z])Attempt Deck"), b"UnitAttempt Deck"),
    (
        re.compile(rb"(?<![A-Za-z])case_run(?![A-Za-z])", re.IGNORECASE),
        b"execution_unit",
    ),
    (
        re.compile(rb"(?<![A-Za-z])case_attempt(?![A-Za-z])", re.IGNORECASE),
        b"unit_attempt",
    ),
    (re.compile("ATLAS · v1\\.0".encode()), "ATLAS · v1.1".encode()),
)


def rewrite_docx(path: Path, *, check: bool) -> int:
    """Rewrite relationship targets atomically and return the replacement count."""
    replacements = 0

    with ZipFile(path) as source:
        entries: list[tuple[ZipInfo, bytes]] = []
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.endswith(".rels"):
                for old, new in LINK_REPLACEMENTS.items():
                    count = data.count(old.encode())
                    if count:
                        data = data.replace(old.encode(), new.encode())
                        replacements += count
            if info.filename.endswith(".xml"):
                for pattern, replacement in XML_REPLACEMENTS:
                    data, count = pattern.subn(replacement, data)
                    replacements += count
            entries.append((info, data))

    if check or replacements == 0:
        return replacements

    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    temporary_path = Path(temporary_name)

    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as target:
            for info, data in entries:
                target.writestr(info, data)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return replacements


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when a stale link is still present without modifying documents.",
    )
    args = parser.parse_args()

    total = 0
    for path in sorted(DOCUMENTS_DIR.glob("*.docx")):
        count = rewrite_docx(path, check=args.check)
        total += count
        if count:
            print(f"{path.name}: {count}")

    if args.check and total:
        raise SystemExit(f"Found {total} stale package value(s).")
    print(f"Replaced {total} stale package value(s).")


if __name__ == "__main__":
    main()
