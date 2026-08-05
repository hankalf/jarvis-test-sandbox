"""Photo library management for the picture-frame mode.

Uploads are normalised on the way in rather than served as-is: phones produce
12-megapixel HEIC files that a Pi has no business decoding every 45 seconds,
and iPhone photos carry their rotation in EXIF, so an un-rotated one shows up
sideways on the wall.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DISPLAY_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
# Formats we accept on upload but convert; the browser can't show HEIC at all.
UPLOAD_SUFFIXES = DISPLAY_SUFFIXES | {".heic", ".heif", ".tif", ".tiff", ".bmp"}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _pillow():
    """Import Pillow lazily so the server still runs without it."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, None
    try:  # HEIC support is a separate package
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image, ImageOps


def safe_name(raw: str) -> str:
    """A filename that can't escape the photo directory or surprise the shell."""
    name = unicodedata.normalize("NFKD", raw or "").encode("ascii", "ignore").decode()
    name = _SAFE_NAME.sub("_", Path(name).name).strip("._")
    return name[:120] or "photo"


def resolve_in(directory: Path, name: str) -> Path | None:
    """Resolve `name` inside `directory`, or None if it points anywhere else.

    Checked after resolution so symlinks and `..` are both covered.
    """
    directory = directory.resolve()
    try:
        candidate = (directory / Path(name).name).resolve()
        candidate.relative_to(directory)
    except (ValueError, OSError):
        return None
    return candidate


def list_photos(directory: Path) -> list[dict]:
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in DISPLAY_SUFFIXES:
            continue
        stat = path.stat()
        out.append(
            {
                "name": path.name,
                "url": f"/photos/{path.name}",
                "sizeBytes": stat.st_size,
                "addedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    out.sort(key=lambda p: p["addedAt"], reverse=True)
    return out


def _unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for n in range(2, 1000):
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}{suffix}"


def save_upload(
    directory: Path, filename: str, data: bytes, *, resize_long_edge: int = 2560
) -> dict:
    """Normalise and store one uploaded image. Raises ValueError if unusable."""
    directory.mkdir(parents=True, exist_ok=True)
    original = safe_name(filename)
    suffix = Path(original).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise ValueError(
            f"{original}: not an image we can show "
            f"({', '.join(sorted(s.lstrip('.') for s in UPLOAD_SUFFIXES))})"
        )

    Image, ImageOps = _pillow()
    if Image is None:
        if suffix not in DISPLAY_SUFFIXES:
            raise ValueError(
                f"{original}: converting this format needs Pillow "
                "(pip install Pillow pillow-heif)"
            )
        path = _unique_path(directory, original)
        path.write_bytes(data)
        log.info("stored %s unprocessed (Pillow not installed)", path.name)
        return {"name": path.name, "converted": False}

    try:
        with Image.open(io.BytesIO(data)) as image:
            # Applies the EXIF orientation and drops the tag, so the file is
            # upright for anything that reads it later.
            image = ImageOps.exif_transpose(image)
            if resize_long_edge and max(image.size) > resize_long_edge:
                image.thumbnail((resize_long_edge, resize_long_edge), Image.LANCZOS)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
    except ValueError:
        raise
    except Exception as exc:  # unreadable, truncated, or a non-image
        raise ValueError(f"{original}: could not be read as an image ({exc})") from exc

    path = _unique_path(directory, Path(original).stem + ".jpg")
    path.write_bytes(out.getvalue())
    return {"name": path.name, "converted": True}


def delete_photo(directory: Path, name: str) -> bool:
    path = resolve_in(directory, name)
    if path is None or not path.is_file():
        return False
    if path.suffix.lower() not in DISPLAY_SUFFIXES:
        return False
    path.unlink()
    return True
