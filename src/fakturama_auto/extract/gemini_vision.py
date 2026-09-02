"""Vision extraction of the order image via the Gemini API.

Uses the ``google-genai`` SDK's ``interactions.create`` surface with a
server-enforced JSON schema, falling back to prompt-described JSON if the
schema is rejected.

That fallback is the same idea as the locator ladder in :mod:`..uia.locator`:
prefer the strongest guarantee available, degrade deliberately rather than
fail, and validate the result either way. Whichever rung answers, the output
goes through the identical Pydantic parse, :func:`~..models.normalise`, and
arithmetic reconciliation - so a weaker rung cannot smuggle through weaker data.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..errors import ExtractionError
from ..models import NormalisationError, OrderDoc, RawOrderExtraction, normalise
from .imaging import prepare_image
from .prompts import SCHEMA_FALLBACK_SUFFIX, SYSTEM_PROMPT, USER_PROMPT
from .schema import inline_refs, strip_json_fences

#: Latest-generation multimodal model. Small, soft type is the hard part of
#: this document, so override with GEMINI_MODEL to trade cost for accuracy.
DEFAULT_MODEL = "gemini-3.7-flash"


class GeminiVisionExtractor:
    """Extract an order using a Gemini vision model."""

    name = "gemini"

    def __init__(self, settings: Settings, client: object | None = None) -> None:
        self._settings = settings
        self._model = settings.gemini_model
        self._client = client or self._build_client()

    def _build_client(self):
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ExtractionError(
                "The google-genai package is not installed. Run "
                "`pip install -r requirements.txt`."
            ) from exc

        if not self._settings.gemini_api_key:
            raise ExtractionError(
                "No GEMINI_API_KEY found. Copy .env.example to .env and set it."
            )
        return genai.Client(api_key=self._settings.gemini_api_key)

    # -- extraction --------------------------------------------------------

    def extract(self, image_path: Path) -> OrderDoc:
        image_b64, media_type = prepare_image(image_path)
        image_part = {"type": "image", "data": image_b64, "mime_type": media_type}

        payload = self._request_with_schema(image_part)
        if payload is None:
            payload = self._request_with_prompted_schema(image_part)

        try:
            raw = RawOrderExtraction.model_validate_json(payload)
        except ValueError as exc:
            raise ExtractionError(
                f"Gemini returned JSON that does not match the expected shape: {exc}"
            ) from exc

        try:
            return normalise(raw)
        except NormalisationError as exc:
            raise ExtractionError(f"Extraction could not be normalised: {exc}") from exc

    # -- the two rungs -----------------------------------------------------

    def _request_with_schema(self, image_part: dict) -> str | None:
        """Preferred rung: the server enforces the schema.

        Returns ``None`` (rather than raising) when the schema itself is
        rejected, so the caller can drop to the prompted rung. Any other
        failure is a real error and propagates.
        """
        schema = inline_refs(RawOrderExtraction.model_json_schema())
        try:
            interaction = self._client.interactions.create(
                model=self._model,
                system_instruction=SYSTEM_PROMPT,
                input=[{"type": "text", "text": USER_PROMPT}, image_part],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
            )
        except Exception as exc:  # noqa: BLE001 - SDK error types vary by version
            if _looks_like_schema_rejection(exc):
                return None
            raise ExtractionError(f"Gemini request failed: {exc}") from exc

        return _output_text(interaction)

    def _request_with_prompted_schema(self, image_part: dict) -> str:
        """Fallback rung: ask for JSON in the prompt and validate it ourselves."""
        schema = json.dumps(inline_refs(RawOrderExtraction.model_json_schema()), indent=2)
        prompt = USER_PROMPT + SCHEMA_FALLBACK_SUFFIX.format(schema=schema)

        try:
            interaction = self._client.interactions.create(
                model=self._model,
                system_instruction=SYSTEM_PROMPT,
                input=[{"type": "text", "text": prompt}, image_part],
                response_format={"type": "text", "mime_type": "application/json"},
            )
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Gemini request failed: {exc}") from exc

        return strip_json_fences(_output_text(interaction))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _output_text(interaction: object) -> str:
    text = getattr(interaction, "output_text", None)
    if not text:
        raise ExtractionError(
            "Gemini returned no text output. The model may have declined the request."
        )
    return str(text)


def _looks_like_schema_rejection(exc: Exception) -> bool:
    """Whether the failure is the schema being unacceptable rather than a real error.

    Matched on message text because the SDK does not expose a distinct
    exception type for it. Kept narrow: a false positive only costs one extra
    request on the fallback rung, and a false negative still surfaces the
    original error.
    """
    message = str(exc).lower()
    schema_words = ("schema", "response_format", "responseformat")
    reject_words = ("invalid", "unsupported", "not supported", "unknown field", "400")
    return any(word in message for word in schema_words) and any(
        word in message for word in reject_words
    )
