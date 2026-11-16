from contextlib import contextmanager

import openai
from pydantic import ValidationError

from backend.core.errors import AppError


@contextmanager
def safe_openai_errors():
    try:
        yield
    except openai.AuthenticationError:
        raise AppError("openai_authentication", "OpenAI rejected the API credentials.", 502) from None
    except openai.PermissionDeniedError:
        raise AppError(
            "openai_permission", "This account does not have permission for the requested model or tool.", 502
        ) from None
    except openai.RateLimitError as error:
        code = "openai_quota" if error.code == "insufficient_quota" else "openai_rate_limit"
        raise AppError(
            code, "OpenAI quota or rate limit reached. Check the account and retry later.", 429
        ) from None
    except openai.APITimeoutError:
        raise AppError("openai_timeout", "OpenAI request timed out. Please try again.", 504) from None
    except openai.APIConnectionError:
        raise AppError("openai_connection", "Could not connect to OpenAI.", 502) from None
    except openai.APIStatusError as error:
        if error.status_code >= 500:
            raise AppError(
                "openai_unavailable", "OpenAI is temporarily unavailable. Please try later.", 502
            ) from None
        raise AppError(
            "openai_request_failed", "OpenAI rejected the request. Check model and Web Search access.", 502
        ) from None
    except (ValidationError, openai.APIResponseValidationError, openai.LengthFinishReasonError):
        raise AppError(
            "invalid_model_output", "OpenAI returned an incomplete or invalid structured response.", 502
        ) from None
