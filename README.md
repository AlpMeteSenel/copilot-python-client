# Copilot Python Client
[![PyPI version](https://img.shields.io/pypi/v/copilot-client.svg)](https://pypi.org/project/copilot-client/)
[![Python versions](https://img.shields.io/pypi/pyversions/copilot-client.svg)](https://pypi.org/project/copilot-client/)
[![Downloads](https://static.pepy.tech/badge/copilot-client)](https://pepy.tech/project/copilot-client)
[![Educational Use Only](https://img.shields.io/badge/Status-Educational_Use_Only-orange)](https://github.com/AlpMeteSenel/copilot-python-client#readme)

Synchronous GitHub Copilot client and CLI with device flow auth, token exchange, model management, and streaming chat. This project exists for educational purposes only and is not an official GitHub Copilot product.

## Installation

- Python 3.10+
- From PyPI (recommended):
    ```bash
    pip install copilot-client
    ```
- From GitHub:
    ```bash
    pip install "git+https://github.com/AlpMeteSenel/copilot-python-client.git"
    ```

## CLI (copilot-client)

The CLI is installed as `copilot-client` (same version as the package). Credentials live in `~/.copilot_client/config.json` and are reused across commands.

- Authenticate via device flow or paste a token: `copilot-client auth`
- List available models: `copilot-client models`
- Interactive chat: `copilot-client chat --model gpt-5-mini --system "Be concise"`

## Quickstart (programmatic)

```python
import os
from copilot_client import CopilotClient

# Use an existing Copilot access token or fetch one via device flow (see below)
client = CopilotClient(copilot_access_token=os.environ["COPILOT_TOKEN"])
print(client.get_user())
print(client.chat([{ "role": "user", "content": "Say hi" }]))
```

## Auth options

- **Device flow:**
    ```python
    code = CopilotClient.start_device_flow()
    print(f"Open {code.verification_uri} and enter {code.user_code}")
    github_token = CopilotClient.poll_device_flow(code.device_code, poll_interval=code.interval)
    client = CopilotClient(copilot_access_token=github_token)
    print("Copilot token:", client.copilot_token)
    ```
- **Existing Copilot access token:**
    ```python
    client = CopilotClient(copilot_access_token=os.environ["COPILOT_TOKEN"])
    ```

## End-to-end walkthrough

```python
from pathlib import Path
import base64
from copilot_client import CopilotClient

# Start device flow and exchange for a Copilot token
code = CopilotClient.start_device_flow()
print(f"Open {code.verification_uri} and enter {code.user_code}")
token = CopilotClient.poll_device_flow(code.device_code, poll_interval=code.interval)
client = CopilotClient(copilot_access_token=token)

print("copilot_token", client.copilot_token)
print("user", client.get_user())

models = client.list_models()
print([[m["id"], (m.get("policy", {}).get("state", "no policy"))] for m in models])

if models:
    try:
        print("enable_model", client.enable_model(models[0]["id"]))
    except Exception as exc:
        print("enable_model skipped", exc)

reply = client.chat([{"role": "user", "content": "Summarize GitHub in 2 bullets"}], system_message="Be concise")
print("chat", reply)

print("chat_stream", end=" ")
for chunk in client.chat_stream([{ "role": "user", "content": "Name two Python async primitives"}], model="copilot-nes-xtab"):
    if chunk["type"] == "text-delta":
        print(chunk["text"], end="")
print()

image_path = Path("husky.jpg")  # replace with your image path
image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
vision_message = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "What is on this image?"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}", "detail": "high"}},
    ],
}]

for chunk in client.chat_stream(vision_message, model="gpt-5-mini", vision=True):
    if chunk["type"] == "text-delta":
        print(chunk["text"], end="")
print()
```

## Core usage

- Models: list with `client.list_models()`; enable those needing consent via `client.enable_model(model_id)`.
- Chat (non-streaming): `client.chat([...], model="gpt-5-mini", system_message="Be concise", max_output_tokens=200)`.
- Chat (streaming): iterate over `client.chat_stream([...], model="copilot-nes-xtab")` and print `chunk["text"]` when `chunk["type"] == "text-delta"`.
- User info: `client.get_user()`.

`enable_model` only activates models that require explicit consent; many will already be enabled.

## Errors

All methods raise `CopilotError` on non-2xx responses. Auth issues raise `CopilotAuthError`; rate limits raise `CopilotRateLimitError`.

## Tips

- `chat_stream(..., vision=True)` forwards the Copilot vision header when prompts include images.
- Copilot access tokens are exchanged automatically for Copilot bearer tokens and refreshed every 30 minutes.

## Credits

Built by Alp Mete Senel. Contributions and issues are welcome.
