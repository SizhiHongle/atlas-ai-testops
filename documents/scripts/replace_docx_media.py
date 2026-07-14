"""Replace one embedded media file in a DOCX package atomically."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def replace_media(docx_path: Path, member: str, replacement_path: Path) -> None:
    """Replace a package member while preserving all other ZIP entries."""
    replacement = replacement_path.read_bytes()
    if member.lower().endswith(".png") and not replacement.startswith(PNG_SIGNATURE):
        raise ValueError(f"Replacement is not a PNG file: {replacement_path}")

    with ZipFile(docx_path) as source:
        entries: list[tuple[ZipInfo, bytes]] = []
        found = False
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == member:
                data = replacement
                found = True
            entries.append((info, data))

    if not found:
        raise KeyError(f"DOCX member does not exist: {member}")

    fd, temporary_name = tempfile.mkstemp(
        dir=docx_path.parent,
        prefix=f".{docx_path.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    temporary_path = Path(temporary_name)

    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as target:
            for info, data in entries:
                target.writestr(info, data)
        temporary_path.replace(docx_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("docx", type=Path)
    parser.add_argument(
        "member", help="Package member, for example word/media/image1.png"
    )
    parser.add_argument("replacement", type=Path)
    args = parser.parse_args()

    replace_media(args.docx, args.member, args.replacement)
    print(f"Replaced {args.docx}:{args.member} from {args.replacement}")


if __name__ == "__main__":
    main()
