"""GitHub Copilot Python client authored by Alp Mete Senel."""
from .client import (
    COPILOT_CLIENT_ID,
    DEFAULT_SCOPE,
    CopilotClient,
    CopilotError,
    CopilotAuthError,
    CopilotRateLimitError,
    DeviceCode,
)

__all__ = [
    "COPILOT_CLIENT_ID",
    "DEFAULT_SCOPE",
    "CopilotClient",
    "CopilotError",
    "CopilotAuthError",
    "CopilotRateLimitError",
    "DeviceCode",
]
