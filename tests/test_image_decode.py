import io

import pytest
from PIL import Image

from flamoris_generation_controller.image_decode import decode_image


def image_bytes(format="PNG", size=(64, 64), **kwargs):
    stream = io.BytesIO()
    Image.new("RGB", size, "red").save(stream, format=format, **kwargs)
    return stream.getvalue()


@pytest.mark.parametrize(
    "format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_valid_bounded_images(format, mime):
    assert decode_image(image_bytes(format), mime) == {"width": 64, "height": 64}


@pytest.mark.parametrize(
    "tamper", ["signature", "truncated", "mime", "dimension", "pixels", "animation"]
)
def test_invalid_images_rejected_before_upload(tamper):
    data = image_bytes()
    mime = "image/png"
    options = {}
    if tamper == "signature":
        data = b"\x89PNG\r\n\x1a\nfixture"
    elif tamper == "truncated":
        data = data[: len(data) // 2]
    elif tamper == "mime":
        mime = "image/jpeg"
    elif tamper == "dimension":
        options["max_dimension"] = 32
    elif tamper == "pixels":
        options["max_pixels"] = 1024
    else:
        stream = io.BytesIO()
        Image.new("RGB", (64, 64), "red").save(
            stream, format="PNG", save_all=True, append_images=[Image.new("RGB", (64, 64), "blue")]
        )
        data = stream.getvalue()
    with pytest.raises(ValueError):
        decode_image(data, mime, **options)


@pytest.mark.parametrize("size", [(4097, 16), (16, 4097), (8192, 512)])
def test_managed_image_rejects_each_dimension_above_4096(size):
    with pytest.raises(ValueError, match="bound mismatch"):
        decode_image(image_bytes(size=size), "image/png")


def test_managed_image_accepts_4096_boundary():
    assert decode_image(image_bytes(size=(4096, 16)), "image/png") == {"width": 4096, "height": 16}
