"""The fast local check that bounces an unusable upload within seconds (D20, story 28, ticket #14).

Runs synchronously during the upload, before anything is stored: `check()` returns None when the file passes, or a
reason code when it does not. The code, not a rendered message, is what gets stored (`models.Document.bounce_reason`):
the code is a stable identifier, so a document uploaded while one viewer's browser was in English still shows its
reason in Chinese to a viewer whose language is Chinese. `reason_text(code)` renders a code to the client-facing
message in whichever language is active when it is called, never when the code was produced.

A phone photo or scan (JPEG or PNG) gets a real check with Pillow: minimum pixel dimensions (a crude stand-in for
"page found", catching a cropped or far-too-small shot) and a sharpness score (the mean response of a Sobel-like
edge filter, a fast, well-known proxy for blur: a sharp page has strong edge responses throughout; a blurred one is
nearly flat). A PDF is checked only structurally (a valid header and a trailer, not empty or truncated): rendering
a PDF page to pixels to run the same sharpness check needs a PDF renderer (poppler or PyMuPDF), a much heavier
dependency the pilot does not carry, so a blurry scanned PDF is not caught here. This is a known, accepted gap for
this ticket, not an oversight.
"""

import io

from django.utils.translation import gettext as _
from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

PDF, JPEG, PNG = "application/pdf", "image/jpeg", "image/png"
ALLOWED_CONTENT_TYPES = (PDF, JPEG, PNG)

# Reason codes `check()` returns; stable identifiers, never shown to the client directly (see `reason_text`).
EMPTY = "empty"
UNSUPPORTED_TYPE = "unsupported_type"
NOT_A_PDF = "not_a_pdf"
PDF_INCOMPLETE = "pdf_incomplete"
CORRUPTED = "corrupted"
TOO_SMALL = "too_small"
TOO_LARGE = "too_large"
TOO_BLURRY = "too_blurry"

# A page shorter or narrower than this is not a readable document: a crude stand-in for "page found" (D20).
MIN_PIXELS_PER_SIDE = 400
# Below this, the edge-filtered image is too flat to be a sharp photo of text (tuned against the test fixtures).
MIN_EDGE_SCORE = 10.0

PDF_TRAILER_WINDOW = 2048


def check(data, content_type):
    """None if `data` passes the fast local check; otherwise one of this module's reason codes."""
    if not data:
        return EMPTY
    if content_type not in ALLOWED_CONTENT_TYPES:
        return UNSUPPORTED_TYPE
    if content_type == PDF:
        return _check_pdf(data)
    return _check_image(data)


def reason_text(code):
    """The client-facing message for a bounce reason code, in whichever language is active right now.

    A code this module never produced (stored data outlives the code, or reaches the database by some other
    path than `check()`) renders as a generic message rather than raising: a list screen showing several
    documents must not fail to render one bad row.
    """
    return {
        EMPTY: _("The file is empty. Upload it again."),
        UNSUPPORTED_TYPE: _("Upload a PDF, a scan or a phone photo (JPEG or PNG)."),
        NOT_A_PDF: _("This does not look like a PDF. Upload the original file."),
        PDF_INCOMPLETE: _("The PDF looks incomplete. Upload it again."),
        CORRUPTED: _("The file is corrupted. Upload it again."),
        TOO_SMALL: _("The photo is too small or cropped. Retake it with the whole page in frame."),
        TOO_LARGE: _("The photo is too large to check. Try a smaller photo, or compress it first."),
        TOO_BLURRY: _("The photo is too blurry. Retake it in good light, holding the phone steady."),
    }.get(code, _("Upload it again."))


def _check_pdf(data):
    if not data.startswith(b"%PDF-"):
        return NOT_A_PDF
    if b"%%EOF" not in data[-PDF_TRAILER_WINDOW:]:
        return PDF_INCOMPLETE
    return None


def _check_image(data):
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
    # SyntaxError is not a real syntax error: several Pillow plugins (PNG among them) still raise it for malformed
    # chunk data, a convention inherited from very old PIL releases. Scoped to Pillow's own calls here, so it is
    # never a real syntax error from this module's own code.
    except (UnidentifiedImageError, OSError, SyntaxError):
        return CORRUPTED
    except Image.DecompressionBombError:
        # Pillow's own safety guard against a declared size built to exhaust memory; kept on, not caught away.
        return TOO_LARGE
    width, height = image.size
    if width < MIN_PIXELS_PER_SIDE or height < MIN_PIXELS_PER_SIDE:
        return TOO_SMALL
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    if ImageStat.Stat(edges).mean[0] < MIN_EDGE_SCORE:
        return TOO_BLURRY
    return None
