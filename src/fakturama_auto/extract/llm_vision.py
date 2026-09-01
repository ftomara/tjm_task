"""Vision extraction of the order image via the Anthropic API.

Design notes
------------
*Transcribe, never derive.* The prompt insists the model reads the printed
totals off the document rather than computing them from the line items. That
matters: :mod:`..extract.validate` reconciles the totals against the lines,
and if the model had derived one from the other the check would be circular
and would pass on garbage.

*Upscale before sending.* The source is a deliberately small, soft 385x530
render. Claude tokenises an image at roughly ``(w*h)/750`` tokens and only
downsamples above ``MAX_IMAGE_LONG_EDGE``, so a Lanczos upscale to just under
that ceiling buys the model an order of magnitude more image tokens to spend
on small type. It adds no information, but it gives the model room to
represent what is already there.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import anthropic
from PIL import Image

from ..config import MAX_IMAGE_LONG_EDGE, Settings
from ..errors import ExtractionError
from ..models import OrderDoc, RawOrderExtraction, NormalisationError, normalise

SYSTEM_PROMPT = """\
You are a meticulous document-transcription system for an accounting pipeline.
You read a scanned or rendered sales order and return its contents verbatim.

Rules that matter more than anything else:

1. TRANSCRIBE, DO NOT CALCULATE. Every number you return must be one you can
   actually see printed on the document. Never compute a total from the line
   items, and never back-fill a line from the totals. A downstream check
   reconciles the two independently; deriving one from the other defeats it.
2. If a value is genuinely unreadable, still give your best reading, and add
   its dotted path to `low_confidence_fields` (e.g. "items[0].sku").
3. Dates are ISO `yyyy-mm-dd`.
4. Money and percentages are plain decimal strings: "250.00", "19", "0".
   No currency symbols, no thousands separators, no percent signs.
5. `unit_net_price` is the price of ONE unit BEFORE any discount.
   `line_total_net` is the line total AS PRINTED.
6. A blank or absent discount is "0", not null.
7. Use null for fields the document simply does not contain. Do not invent
   placeholder text, and do not copy a value from one field into another.
8. Transcribe names, street lines and SKUs character-for-character, including
   case and punctuation. Do not expand abbreviations or fix apparent typos.
"""

USER_PROMPT = """\
Extract every field from this sales order document.

Pay particular attention to:
- the external reference and the order date,
- the customer company, contact name and alias,
- the billing and delivery addresses (they may differ - transcribe each as printed),
- the payment method, the paid status, and the payment date if shown,
- every line item: position, SKU, description, quantity, unit, unit net price,
  discount percent, VAT percent, and the printed line total,
- the printed net / VAT / gross totals.
"""


class LlmVisionExtractor:
    """Extract an order using a Claude vision model."""

    name = "llm_vision"

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self._settings = settings
        if client is not None:
            self._client = client
        elif settings.anthropic_api_key:
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        else:
            # No explicit key: the SDK still resolves ANTHROPIC_AUTH_TOKEN or an
            # `ant auth login` profile, so let it try rather than failing early.
            self._client = anthropic.Anthropic()

    def extract(self, image_path: Path) -> OrderDoc:
        if not image_path.exists():
            raise ExtractionError(f"order image not found: {image_path}")

        image_b64, media_type = prepare_image(image_path)

        try:
            response = self._client.messages.parse(
                model=self._settings.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": USER_PROMPT},
                        ],
                    }
                ],
                output_format=RawOrderExtraction,
            )
        except anthropic.AuthenticationError as exc:
            raise ExtractionError(
                "Anthropic rejected the credentials. Set ANTHROPIC_API_KEY in .env "
                "or run `ant auth login`."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ExtractionError("Anthropic rate limit hit; retry shortly.") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("Could not reach the Anthropic API.") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("The model declined to transcribe this document.")

        raw = response.parsed_output
        if raw is None:
            raise ExtractionError("The model returned no structured output.")

        try:
            return normalise(raw)
        except NormalisationError as exc:
            raise ExtractionError(f"Extraction could not be normalised: {exc}") from exc


def prepare_image(image_path: Path) -> tuple[str, str]:
    """Load, upscale and base64-encode the order image.

    Returns ``(base64_png, media_type)``. Always re-encodes as PNG so the
    media type is known and the upscale is lossless.
    """
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGB")
    except OSError as exc:
        raise ExtractionError(f"could not read image {image_path}: {exc}") from exc

    long_edge = max(image.size)
    if long_edge < MAX_IMAGE_LONG_EDGE:
        scale = MAX_IMAGE_LONG_EDGE / long_edge
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii"), "image/png"
