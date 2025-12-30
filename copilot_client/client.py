"""Minimal GitHub Copilot client for device flow, tokens, models, and chat."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Generator, Iterable, List, Optional
import contextlib
import json
import time
import uuid
import httpx


COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEFAULT_SCOPE = "read:user"


class CopilotError(Exception):
    pass


class CopilotAuthError(CopilotError):
    pass


class CopilotRateLimitError(CopilotError):
    pass


@dataclass
class DeviceCode:
    verification_uri: str
    user_code: str
    device_code: str
    interval: int
    expires_in: int


class CopilotClient:

    def __init__(
        self,
        *,
        copilot_access_token: str,
        timeout: float = 30.0,
    ):
        if not copilot_access_token:
            raise ValueError("Provide a copilot_access_token")
        if len(copilot_access_token) < 10:
            raise ValueError("copilot_access_token appears invalid")

        self._copilot_access_token = copilot_access_token
        self._copilot_token: Optional[str] = None
        self._copilot_token_acquired_at: Optional[float] = None
        self._timeout_seconds = timeout
        self._client = httpx.Client(timeout=timeout, http2=True)

        self._refresh_copilot_token()

    def __enter__(self) -> "CopilotClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    @staticmethod
    def start_device_flow(*, client_id: str = COPILOT_CLIENT_ID, scope: str = DEFAULT_SCOPE, timeout: float = 15.0) -> DeviceCode:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                "https://github.com/login/device/code",
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "editor-version": "Neovim/0.6.1",
                    "editor-plugin-version": "copilot.vim/1.16.0",
                    "user-agent": "GithubCopilot/1.155.0",
                },
                json={"client_id": client_id, "scope": scope},
            )
        _raise_for_status(resp)
        data = resp.json()
        return DeviceCode(
            verification_uri=data["verification_uri"],
            user_code=data["user_code"],
            device_code=data["device_code"],
            interval=int(data.get("interval", 5)),
            expires_in=int(data.get("expires_in", 900)),
        )

    @staticmethod
    def poll_device_flow(device_code: str, *, client_id: str = COPILOT_CLIENT_ID, poll_interval: float = 5.0, timeout: float = 180.0) -> str:
        start = time.time()
        with httpx.Client(timeout=poll_interval + 5) as client:
            while True:
                resp = client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={
                        "accept": "application/json",
                        "content-type": "application/json",
                        "editor-version": "Neovim/0.6.1",
                        "editor-plugin-version": "copilot.vim/1.16.0",
                        "user-agent": "GithubCopilot/1.155.0",
                    },
                    json={
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
                data = resp.json()

                if "access_token" in data:
                    return data["access_token"]

                err = data.get("error")
                if err == "authorization_pending":
                    pass
                elif err == "slow_down":
                    poll_interval += 2
                elif err == "expired_token":
                    raise CopilotAuthError("Device code expired. Please restart login.")
                elif err == "access_denied":
                    raise CopilotAuthError("User denied access. Please restart login.")
                else:
                    raise CopilotAuthError(f"Device flow failed: {data}")

                if time.time() - start > timeout:
                    raise CopilotAuthError("Device flow polling timed out")
                time.sleep(poll_interval)

    @staticmethod
    def exchange_for_copilot_token(github_token: str, *, timeout: float = 30.0) -> str:
        if not github_token or len(github_token) < 10:
            raise ValueError("github_token appears invalid")
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                "https://api.github.com/copilot_internal/v2/token",
                headers={
                    "authorization": f"token {github_token}",
                    "editor-version": "Neovim/0.6.1",
                    "editor-plugin-version": "copilot.vim/1.16.0",
                    "user-agent": "GithubCopilot/1.155.0",
                },
            )
        _raise_for_status(resp)
        data = resp.json()
        token = data.get("token")
        if not token:
            raise CopilotAuthError("No Copilot token received")
        return token

    def list_models(self) -> List[Dict]:
        resp = self._request(
            "GET",
            "https://api.githubcopilot.com/models",
            intent="model-access",
        )
        payload = resp.json()
        if not isinstance(payload, dict) or "data" not in payload:
            raise CopilotError("Unexpected response structure for models")
        chat_models = [m for m in payload.get("data", [])]
        manual_model = {
            "id": "copilot-nes-xtab",
            "name": "Copilot NES Xtab",
            "vendor": "OpenAI",
            "preview": True,
            "capabilities": {"type": "chat", "supports": {"vision": False, "tool_calls": True, "streaming": True}},
            "model_picker_enabled": True,
        }
        return [*chat_models, manual_model]

    def enable_model(self, model_id: str) -> Dict:
        if not model_id:
            raise ValueError("model_id is required")

        url = f"https://api.individual.githubcopilot.com/models/{model_id}/policy"
        headers = {
            "accept": "*/*",
            "authorization": f"Bearer {self._copilot_access_token}",
            "content-type": "application/json; charset=UTF-8",
            "copilot-integration-id": "copilot-chat",
            "x-github-api-version": "2025-05-01",
            "user-agent": "GitHubCopilotChat/0.27.2",
            "origin": "https://github.com",
            "referer": "https://github.com/",
        }

        resp = self._client.post(url, headers=headers, json={"state": "enabled"})
        if not resp.is_success:
            _ensure_response_read(resp)
            details = resp.text or ""
            raise CopilotError(f"Failed to enable model policy ({resp.status_code}): {details}")

        try:
            return resp.json()
        except Exception:
            return {"error": None}

    def get_user(self) -> Dict:
        resp = self._request(
            "GET",
            "https://api.github.com/copilot_internal/user",
            headers={
                "accept": "*/*",
                "authorization": f"Bearer {self._copilot_access_token}",
                "user-agent": "GitHubCopilotChat/0.27.2",
            },
        )
        return resp.json()

    def chat(self, messages: Iterable[Dict[str, str]], *, model: str = "gpt-5-mini", system_message: Optional[str] = None, vision: bool = False, max_output_tokens: int = 16000) -> str:
        """Send a single chat completion request (non-streaming)."""
        final_messages = _inject_system_message(list(messages), system_message)
        is_proxy_model = model == "copilot-nes-xtab"
        body = {
            "messages": final_messages,
            "model": model,
            "temperature": 0.1,
            "top_p": 1,
            "max_tokens": max_output_tokens,
            "n": 1,
            "stream": False,
            "intent": False,
        }
        api_url = "https://proxy.individual.githubcopilot.com/chat/completions" if is_proxy_model else "https://api.githubcopilot.com/chat/completions"

        def send_request() -> httpx.Response:
            self._ensure_fresh_token()
            return self._client.post(
                api_url,
                headers=self._chat_headers(vision=vision),
                json=body,
            )

        resp = send_request()
        # Retry once on expired token to avoid forcing the caller to handle it.
        if resp.status_code in (401, 403):
            self._refresh_copilot_token()
            resp = send_request()

        _raise_for_status(resp)
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise CopilotError("No choices returned from chat completion")
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, list):
            # OpenAI-style content blocks list; join string parts.
            return "".join([part.get("text", "") for part in content if isinstance(part, dict)])
        if isinstance(content, str):
            return content
        raise CopilotError("Unexpected chat response format")

    def chat_stream(self, messages: Iterable[Dict[str, str]], *, model: str = "gpt-5-mini", system_message: Optional[str] = None, vision: bool = False, max_output_tokens: int = 16000) -> Generator[Dict[str, str], None, None]:
        final_messages = _inject_system_message(list(messages), system_message)
        is_proxy_model = model == "copilot-nes-xtab"
        body = {
            "messages": final_messages,
            "model": model,
            "temperature": 0.1,
            "top_p": 1,
            "max_tokens": max_output_tokens,
            "n": 1,
            "stream": True,
            "intent": False,
        }
        api_url = "https://proxy.individual.githubcopilot.com/chat/completions" if is_proxy_model else "https://api.githubcopilot.com/chat/completions"
        def open_stream() -> httpx.Response:
            self._ensure_fresh_token()
            return self._client.stream(
                "POST",
                api_url,
                headers=self._chat_headers(vision=vision),
                json=body,
            )

        with self._stream_with_refresh(open_stream) as resp:
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    yield {"type": "finish", "finishReason": "stop"}
                    break
                try:
                    data = json.loads(payload)
                except Exception:
                    continue
                content = data.get("choices", [{}])[0].get("delta", {}).get("content")
                if content:
                    yield {"type": "text-delta", "text": content}

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        intent: Optional[str] = None,
        json: Optional[Dict] = None,
        allow_statuses: Optional[List[int]] = None,
    ) -> httpx.Response:
        def build_headers() -> Dict[str, str]:
            if headers is None:
                return self._default_headers(intent=intent)
            final_headers = dict(headers)
            auth = final_headers.get("authorization")
            if auth and auth.startswith("Bearer "):
                final_headers["authorization"] = f"Bearer {self._copilot_access_token}"
            return final_headers

        def send() -> httpx.Response:
            return self._client.request(method, url, headers=build_headers(), json=json)
        allowed = set(allow_statuses or [])

        resp = send()
        if resp.status_code in allowed:
            return resp
        _raise_for_status(resp)
        return resp

    @contextlib.contextmanager
    def _stream_with_refresh(self, open_stream: Callable[[], httpx._client._StreamContextManager]) -> Generator[httpx.Response, None, None]:
        cm = open_stream()
        resp = cm.__enter__()
        try:
            _raise_for_status(resp)
            yield resp
        finally:
            cm.__exit__(None, None, None)

    def _ensure_fresh_token(self) -> None:
        if self._copilot_token and self._copilot_token_acquired_at:
            if time.time() - self._copilot_token_acquired_at < 30 * 60:
                return
        self._refresh_copilot_token()

    def _refresh_copilot_token(self) -> None:
        token = self.exchange_for_copilot_token(self._copilot_access_token, timeout=self._timeout_seconds)
        self._copilot_token_acquired_at = time.time()
        self._copilot_token = token
        self.copilot_token = token

    def _validate_copilot_token(self, token: str) -> None:
        resp = self._client.get(
            "https://api.github.com/copilot_internal/user",
            headers={
                "accept": "*/*",
                "authorization": f"Bearer {token}",
                "user-agent": "GitHubCopilotChat/0.27.2",
            },
        )
        if resp.status_code in (401, 403):
            raise CopilotAuthError(f"Auth failed: {resp.status_code} {resp.text}")
        _raise_for_status(resp)

    def _default_headers(self, intent: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "accept-encoding": "gzip",
            "authorization": f"Bearer {self._copilot_access_token}",
            "connection": "keep-alive",
            "content-type": "application/json",
            "copilot-integration-id": "vscode-chat",
            "editor-plugin-version": "copilot-chat/0.27.2",
            "editor-version": "vscode/1.100.2",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "none",
            "user-agent": "GitHubCopilotChat/0.27.2",
            "x-github-api-version": "2025-05-01",
            "x-initiator": "user",
            "x-vscode-user-agent-library-version": "electron-fetch",
        }
        if intent:
            headers["openai-intent"] = intent
        return headers

    def _chat_headers(self, *, vision: bool) -> Dict[str, str]:
        base = self._default_headers(intent="conversation-panel")
        base["authorization"] = f"Bearer {self.copilot_token}"
        base.update({
            "copilot-vision-request": "true" if vision else "false",
            "vscode-machineid": uuid.uuid4().hex,
            "vscode-sessionid": f"{uuid.uuid4()}{int(time.time() * 1000)}",
            "x-interaction-id": str(uuid.uuid4()),
            "x-interaction-type": "conversation-panel",
            "x-request-id": str(uuid.uuid4()),
        })
        return base


DEFAULT_SYSTEM_MESSAGE = (
    "You are an AI programming assistant.\n"
    "When asked for your name, you must respond with \"GitHub Copilot\".\n"
    "Follow the user's requirements carefully & to the letter.\n"
    "Follow Microsoft content policies.\n"
    "Avoid content that violates copyrights.\n"
    "If you are asked to generate content that is harmful, hateful, racist, sexist, lewd, violent, or completely irrelevant to software engineering, only respond with \"Sorry, I can't assist with that.\"\n"
    "Keep your answers short and impersonal."
)


def _inject_system_message(messages: List[Dict[str, str]], system_message: Optional[str]) -> List[Dict[str, str]]:
    final = list(messages)
    content = system_message or DEFAULT_SYSTEM_MESSAGE
    if not final:
        final.insert(0, {"role": "system", "content": content})
        return final
    if final[0].get("role") != "system":
        final.insert(0, {"role": "system", "content": content})
    else:
        final[0] = {"role": "system", "content": content}
    return final


def _raise_for_status(resp: httpx.Response) -> None:
    if 200 <= resp.status_code < 300:
        return
    _ensure_response_read(resp)
    body = resp.text or ""
    if resp.status_code in (401, 403):
        raise CopilotAuthError(f"Auth failed: {resp.status_code} {body}")
    if resp.status_code == 429:
        raise CopilotRateLimitError(f"Rate limited: {body}")
    raise CopilotError(f"HTTP {resp.status_code}: {body}")


def _ensure_response_read(resp: httpx.Response) -> None:
    if resp.is_stream_consumed or resp.is_closed:
        return
    try:
        resp.read()
    except Exception:
        resp.close()


def _config_path() -> str:
    from pathlib import Path
    cfg_dir = Path.home() / ".copilot_client"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return str(cfg_dir / "config.json")


def load_config() -> Dict:
    path = _config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(data: Dict) -> None:
    path = _config_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _clear_stored_github_token() -> None:
    path = _config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    if "github_token" in data:
        data.pop("github_token", None)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


def _ensure_token_interactive(*, force_prompt: bool = False) -> str:
    cfg = load_config()
    token = cfg.get("github_token")
    if token and not force_prompt:
        return token
    print("No GitHub token configured." if not token else "Re-authentication required.")

    while True:
        print("Choose an option:\n  1) Authenticate via web (device flow)\n  2) Paste a token")
        choice = input("Select 1/2: ").strip()
        if choice == "1":
            if force_prompt:
                _clear_stored_github_token()
            dc = CopilotClient.start_device_flow()
            print("Open this URL in a browser and enter the code:")
            print(f"{dc.verification_uri}  -> code: {dc.user_code}")
            print("Waiting for you to complete authentication...")
            gh_token = CopilotClient.poll_device_flow(dc.device_code, client_id=COPILOT_CLIENT_ID, poll_interval=dc.interval)
            # validate by attempting to exchange for a Copilot token
            try:
                copilot_token = CopilotClient.exchange_for_copilot_token(gh_token)
            except Exception as e:
                print("Authentication failed or token invalid:", e)
                print("Please try again.")
                continue
            # store the GitHub token (not the copilot token)
            cfg["github_token"] = gh_token
            save_config(cfg)
            print("Saved GitHub token to config.")
            return gh_token
        if choice == "2":
            token = input("Paste a GitHub personal access token (scopes: user, repo as needed): ").strip()
            if not token:
                print("No token provided; try again.")
                continue
            try:
                copilot_token = CopilotClient.exchange_for_copilot_token(token)
            except Exception as e:
                print("Provided token appears invalid:", e)
                print("Please try again or use device flow.")
                continue
            if force_prompt:
                _clear_stored_github_token()
            cfg["github_token"] = token
            save_config(cfg)
            print("Saved GitHub token to config.")
            return token
        print("Invalid selection; please enter 1 or 2.")


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="copilot-client", description="Simple Copilot client CLI")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("auth", help="Authenticate via device flow or paste token")
    sub.add_parser("models", help="List available models")

    chat_p = sub.add_parser("chat", help="Start interactive chat")
    chat_p.add_argument("--model", default="gpt-5-mini", help="Model id to use")
    chat_p.add_argument("--system", default=None, help="Optional system prompt override")

    args = parser.parse_args(argv)

    if args.cmd == "auth":
        try:
            _ensure_token_interactive(force_prompt=True)
            print("Authentication complete.")
            return 0
        except Exception as e:
            print("Auth failed:", e)
            return 2

    cfg = load_config()
    gh_token = cfg.get("github_token")
    if not gh_token:
        try:
            gh_token = _ensure_token_interactive()
        except Exception as e:
            print("Authentication required. Run `copilot-client auth`.", e)
            return 2
    else:
        # validate stored GitHub token by exchanging for a Copilot token
        try:
            CopilotClient.exchange_for_copilot_token(gh_token)
        except Exception as e:
            print("Stored token appears invalid:", e)
            print("Run `copilot-client auth` to restart authentication.")
            _clear_stored_github_token()
            return 2

    try:
        client = CopilotClient(copilot_access_token=gh_token)
    except Exception as e:
        print("Failed creating client:", e)
        print("If the token looks invalid, run `copilot-client auth` to reauthenticate.")
        return 3

    if args.cmd == "models":
        try:
            models = client.list_models()
            for m in models:
                _id = m.get("id") or m.get("model") or "<no-id>"
                name = m.get("name") or ""
                preview = " (preview)" if m.get("preview") else ""
                print(f"{_id}\t{name}{preview}")
            return 0
        except Exception as e:
            print("Failed listing models:", e)
            return 4

    # default to chat
    if args.cmd == "chat":
        model = getattr(args, "model", "gpt-5-mini")
        system = getattr(args, "system", None)
        print("Starting chat. Type /exit to quit.")
        try:
            while True:
                prompt = input("You: ")
                if not prompt or prompt.strip() in ("/exit", "/quit"):
                    print("Goodbye")
                    break
                messages = [{"role": "user", "content": prompt}]
                try:
                    resp = client.chat(messages, model=model, system_message=system)
                except Exception as e:
                    print("Chat failed:", e)
                    continue
                print("Copilot:", resp)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
