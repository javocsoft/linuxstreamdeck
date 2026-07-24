"""Static full-deck image preparation for clean application shutdown."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from ..core.config import (
    MAX_EXIT_IMAGE_BYTES,
    SUPPORTED_EXIT_IMAGE_EXTENSIONS,
)
from ..core.icons import RENDER_LOCK

GRID_COLUMNS = 5
MAX_EXIT_IMAGE_PIXELS = 50_000_000


def validate_exit_image(path: str | Path) -> Path:
    """Return a usable local image path or raise a user-facing ValueError."""
    source = Path(path).expanduser()
    if source.suffix.lower() not in SUPPORTED_EXIT_IMAGE_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXIT_IMAGE_EXTENSIONS))
        raise ValueError(f"Choose a supported image file ({supported})")
    try:
        if not source.is_file():
            raise ValueError("The selected image file does not exist")
        if source.stat().st_size > MAX_EXIT_IMAGE_BYTES:
            raise ValueError("The selected image is larger than 50 MB")
        with RENDER_LOCK, Image.open(source) as image:
            if image.width * image.height > MAX_EXIT_IMAGE_PIXELS:
                raise ValueError("The selected image dimensions are too large")
            image.verify()
    except ValueError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as error:
        raise ValueError("The selected file is not a readable image") from error
    return source


def exit_image_tiles(
    path: str | Path,
    key_count: int,
    key_size: tuple[int, int],
    columns: int = GRID_COLUMNS,
) -> tuple[Image.Image, ...]:
    """Crop one image across the complete key grid and return one tile per key."""
    source = validate_exit_image(path)
    count = max(1, int(key_count))
    key_width, key_height = (max(1, int(value)) for value in key_size)
    cols = max(1, min(int(columns), count))
    rows = math.ceil(count / cols)
    canvas_size = (cols * key_width, rows * key_height)

    with RENDER_LOCK, Image.open(source) as image:
        canvas = ImageOps.fit(
            image.convert("RGB"),
            canvas_size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        return tuple(
            canvas.crop(
                (
                    (index % cols) * key_width,
                    (index // cols) * key_height,
                    (index % cols + 1) * key_width,
                    (index // cols + 1) * key_height,
                )
            )
            for index in range(count)
        )


def blank_exit_tiles(
    key_count: int,
    key_size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    """Return black images for every physical key."""
    count = max(1, int(key_count))
    size = tuple(max(1, int(value)) for value in key_size)
    return tuple(Image.new("RGB", size, "black") for _index in range(count))
