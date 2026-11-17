import openai
from pydantic import ValidationError

from backend.core.config import ROOT, Settings
from backend.core.errors import AppError
from backend.providers.base import ProgressCallback
from backend.providers.discovery import ExternalDiscovery
from backend.schemas.models import SearchStrategy, StoredBrief, StrategyDraft, normalize_sources


class OpenAIProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def strategy(self, brief: StoredBrief) -> SearchStrategy:
        if not self.settings.openai_configured:
            raise AppError(
                "openai_not_configured", "Configure OPENAI_API_KEY and OPENAI_MODEL in the backend .env.", 503
            )
        try:
            with openai.OpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
                timeout=self.settings.request_timeout_seconds,
                max_retries=1,
            ) as client:
                response = client.responses.parse(
                    model=self.settings.openai_model,
                    instructions=(ROOT / "prompts/strategy.txt").read_text(encoding="utf-8"),
                    input=brief.model_copy(
                        update={"preferred_sources": normalize_sources(brief.preferred_sources)}
                    ).model_dump_json(exclude={"id", "created_at"}),
                    text_format=StrategyDraft,
                    max_output_tokens=2200,
                    store=False,
                )
            if response.status != "completed" or response.output_parsed is None:
                raise AppError(
                    "model_output_unavailable",
                    "The model declined or did not complete a valid strategy.",
                    502,
                )
            return SearchStrategy(
                **response.output_parsed.model_dump(),
                brief_id=brief.id,
                preferred_sources=normalize_sources(brief.preferred_sources),
                is_mock=False,
            )
        except openai.AuthenticationError:
            raise AppError("openai_authentication", "OpenAI rejected the API credentials.", 502) from None
        except openai.RateLimitError:
            raise AppError(
                "openai_rate_limit",
                "OpenAI quota or rate limit reached. Check the account and retry later.",
                429,
            ) from None
        except openai.APITimeoutError:
            raise AppError("openai_timeout", "OpenAI request timed out. Please try again.", 504) from None
        except openai.APIConnectionError:
            raise AppError("openai_connection", "Could not connect to OpenAI.", 502) from None
        except openai.APIStatusError:
            raise AppError(
                "openai_request_failed",
                "OpenAI rejected the request. Check model access and compatibility.",
                502,
            ) from None
        except (ValidationError, openai.APIResponseValidationError):
            raise AppError(
                "invalid_model_output", "OpenAI returned an invalid structured response.", 502
            ) from None

    def search(self, brief: StoredBrief, strategy: SearchStrategy, progress: ProgressCallback | None = None):
        return ExternalDiscovery(self.settings).search(brief, strategy, progress)
