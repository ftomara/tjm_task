"""Vision extraction of the order image via the Anthropic API.

The alternative to :mod:`.gemini_vision`. Sends byte-identical instructions,
so switching providers changes the model and nothing else.

Claude's SDK validates structured output against the Pydantic model
server-side via ``messages.parse()``, so there is no schema-rejection rung to
fall back to here.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..errors import ExtractionError
from ..models import NormalisationError, OrderDoc, RawOrderExtraction, normalise
from .imaging import prepare_image
from .prompts import SYSTEM_PROMPT, USER_PROMPT


class AnthropicVisionExtractor:
    """Extract an order using a Claude vision model."""

    name = "anthropic"

    def __init__(self, settings: Settings, client: object | None = None) -> None:
        self._settings = settings
        self._client = client or self._build_client()

    def _build_client(self):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ExtractionError(
                "The anthropic package is not installed. Run "
                "`pip install -r requirements.txt`."
            ) from exc

        if self._settings.anthropic_api_key:
            return anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        # No explicit key: the SDK still resolves ANTHROPIC_AUTH_TOKEN or an
        # `ant auth login` profile, so let it try rather than failing early.
        return anthropic.Anthropic()

    def extract(self, image_path: Path) -> OrderDoc:
        import anthropic

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
