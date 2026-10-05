"""Bounded, single-frame image decoding at managed-input and verification boundaries."""

import io
import warnings

from PIL import Image, UnidentifiedImageError

FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
MAX_PIXELS = 16 * 1024 * 1024


def decode_image(data, mime_type, *, max_dimension=4096, max_pixels=MAX_PIXELS):
    if mime_type not in FORMATS or not isinstance(data, bytes) or not 0 < len(data) <= 64 * 1024**2:
        raise ValueError("Image media type or byte bound exceeded")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if (
                    image.format != FORMATS[mime_type]
                    or getattr(image, "n_frames", 1) != 1
                    or not 0 < width <= max_dimension
                    or not 0 < height <= max_dimension
                    or width * height > max_pixels
                ):
                    raise ValueError("Image MIME/frame/pixel bound mismatch")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        return {"width": width, "height": height}
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Invalid bounded image payload") from exc
