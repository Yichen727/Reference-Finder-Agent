import os

import httpx


class APIError(Exception):
    pass


class APIClient:
    def __init__(self, base_url: str | None = None):
        # The frontend reads no OpenAI credentials and never calls the model directly.
        self.base_url = (base_url or os.getenv("BACKEND_URL", "http://127.0.0.1:8000")).rstrip("/")

    def request(self, method: str, path: str, payload: dict | None = None, *, binary=False):
        try:
            with httpx.Client(base_url=self.base_url, timeout=110, trust_env=False) as client:
                response = client.request(method, path, json=payload)
            if response.is_error:
                try:
                    detail = response.json()["error"]
                    message = f"{detail['message']} ({detail['code']})"
                    if detail.get("fields"):
                        message += " — " + ", ".join(item["field"] for item in detail["fields"])
                except (ValueError, KeyError, TypeError):
                    message = f"Backend returned HTTP {response.status_code}."
                raise APIError(message)
            return response.content if binary else response.json()
        except httpx.TimeoutException:
            raise APIError(
                "Backend request timed out. Retry or restore the submitted run from history."
            ) from None
        except httpx.HTTPError:
            raise APIError("Cannot connect to the backend. Start FastAPI and check BACKEND_URL.") from None
        except ValueError:
            raise APIError("The backend returned unreadable data. Check BACKEND_URL.") from None
