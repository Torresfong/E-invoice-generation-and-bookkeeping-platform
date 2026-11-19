"""The fast local upload check (ticket #14, D20, story 28): accepts a sharp page, bounces the rest with a reason."""

import io

from PIL import Image

from core import quality


def _png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def sharp_page(size=800):
    """A checkerboard: strong, varied edges everywhere, like a photographed page of text."""
    image = Image.new("L", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            pixels[x, y] = 255 if (x // 20 + y // 20) % 2 == 0 else 0
    return _png_bytes(image)


def blank_page(size=800):
    """A single flat colour: no edges at all, like a badly blurred photo."""
    return _png_bytes(Image.new("L", (size, size), color=128))


def tiny_photo():
    return _png_bytes(Image.new("L", (50, 50), color=0))


def png_with_a_damaged_idat_checksum():
    """A valid PNG header with its pixel data chunk corrupted: Pillow raises `SyntaxError` for this, not `OSError`."""
    data = bytearray(sharp_page())
    idat = data.index(b"IDAT")
    data[idat + 20] ^= 0xFF
    return bytes(data)


def test_a_sharp_photo_is_accepted():
    assert quality.check(sharp_page(), quality.PNG) is None


def test_a_blurry_photo_is_bounced_with_a_reason():
    assert quality.check(blank_page(), quality.PNG) == quality.TOO_BLURRY


def test_a_too_small_photo_is_bounced_as_cropped():
    assert quality.check(tiny_photo(), quality.PNG) == quality.TOO_SMALL


def test_a_corrupted_image_is_bounced():
    assert quality.check(b"not an image", quality.JPEG) == quality.CORRUPTED


def test_a_png_with_a_damaged_checksum_is_bounced_not_a_500():
    """Pillow raises SyntaxError, not OSError, for this; it must still bounce with a reason."""
    assert quality.check(png_with_a_damaged_idat_checksum(), quality.PNG) == quality.CORRUPTED


def test_an_image_past_pillows_decompression_safety_limit_is_bounced_not_a_500(monkeypatch):
    """A declared size far past Pillow's own guard must bounce with a reason, never raise an unhandled 500."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    assert quality.check(sharp_page(size=200), quality.PNG) == quality.TOO_LARGE


def test_an_empty_file_is_bounced():
    assert quality.check(b"", quality.PNG) == quality.EMPTY


def test_an_unsupported_file_type_is_bounced():
    assert quality.check(sharp_page(), "application/zip") == quality.UNSUPPORTED_TYPE


def test_a_well_formed_pdf_is_accepted():
    assert quality.check(b"%PDF-1.4\n...\n%%EOF", quality.PDF) is None


def test_a_truncated_pdf_is_bounced_as_incomplete():
    assert quality.check(b"%PDF-1.4\nsome content with no trailer", quality.PDF) == quality.PDF_INCOMPLETE


def test_a_file_with_no_pdf_header_is_bounced():
    assert quality.check(b"this is actually a text file %%EOF", quality.PDF) == quality.NOT_A_PDF


def test_every_reason_code_has_a_message():
    for code in (
        quality.EMPTY, quality.UNSUPPORTED_TYPE, quality.NOT_A_PDF, quality.PDF_INCOMPLETE, quality.CORRUPTED,
        quality.TOO_SMALL, quality.TOO_LARGE, quality.TOO_BLURRY, quality.SALES_INVOICE_AFTER_GO_LIVE,
    ):
        assert quality.reason_text(code)


def test_an_unrecognised_code_renders_a_generic_message_instead_of_raising():
    """A list of several documents must not fail to render because one row holds a code this module never made."""
    assert quality.reason_text("whatever-reached-the-column-some-other-way")
