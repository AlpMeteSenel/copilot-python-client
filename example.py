from pathlib import Path
import base64

from copilot_client import CopilotClient


def divider(title: str) -> None:
    print("\n" + "=" * 10 + f" {title} " + "=" * 10)


def get_token() -> str:
    code = CopilotClient.start_device_flow()
    print(f"Open {code.verification_uri} and enter {code.user_code}")
    token = CopilotClient.poll_device_flow(code.device_code, poll_interval=code.interval)
    print(token)
    return token


def main() -> None:
    client = CopilotClient(copilot_access_token=get_token())
    divider("copilot_token")
    print(client.copilot_token)

    divider("user")
    print(client.get_user())

    divider("models")
    models = client.list_models()
    print([[m["id"], (m["policy"]["state"] if "policy" in m else "no policy")] for m in models])

    if models:
        divider("enable_model")
        try:
            print(client.enable_model("gemini-5.2"))
        except Exception as exc:
            print(f"enable_model skipped: {exc}")

    divider("chat")
    reply = client.chat([
        {"role": "user", "content": "Summarize github in 2 bullets"},
    ], system_message="Be concise and technical.")
    print(reply)

    divider("chat_stream")
    for chunk in client.chat_stream([
        {"role": "user", "content": "Name two Python async primitives"},
    ], model="copilot-nes-xtab"):
        if chunk["type"] == "text-delta":
            print(chunk["text"], end="", flush=True)
    print()

    divider("chat_stream_image")
    image_path = Path(__file__).parent / "husky.jpg"
    image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    vision_message = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is on this image?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}", "detail": "high"}},
            ],
        },
    ]

    for chunk in client.chat_stream(vision_message, model="gpt-5-mini", vision=True):
        if chunk["type"] == "text-delta":
            print(chunk["text"], end="", flush=True)
    print()


if __name__ == "__main__":
    main()