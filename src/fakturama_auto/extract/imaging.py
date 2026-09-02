"""Image preparation shared by the vision providers."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from ..config import MAX_IMAGE_LONG_EDGE
from ..errors import ExtractionError


def prepare_image(image_path: Path, max_long_edge: int = MAX_IMAGE_LONG_EDGE) -> tuple[str, str]:
    """Load, upscale and base64-encode the order image.

    The source is a deliberately small, soft 385x530 render. Vision models
    tokenise an image roughly in proportion to its area and only downsample
    above their own ceiling, so a Lanczos upscale to just under that ceiling
    buys the model far more image tokens to spend on small type. It adds no
    information - it gives the model room to represent what is already there.

    Returns ``(base64_png, media_type)``. Always re-encodes as PNG so the media
    type is known and the upscale stays lossless.
    """
    if not image_path.exists():
        raise ExtractionError(f"order image not found: {image_path}")

    try:
        with Image.open(image_path) as source:
            image = source.convert("RGB")
    except OSError as exc:
        raise ExtractionError(f"could not read image {image_path}: {exc}") from exc

    long_edge = max(image.size)
    if long_edge < max_long_edge:
        scale = max_long_edge / long_edge
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.LANCZOS
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii"), "image/png"
