"""Connects the Telegram bot to the deployed inbox Worker. Run once, after sending the bot any message.

    python connect_bot.py <path to .env>

Reads TELEGRAM_BOT_TOKEN, TELEGRAM_SECRET and INBOX_URL from the .env file, finds your chat id from
the message you sent, saves it there as CHAT_ID, and points the bot's webhook at the Worker.
It never prints the token, the secret or the chat id.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def read_env(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return {k.strip(): v.strip().strip("\"'") for k, v in (l.split("=", 1) for l in lines if "=" in l)}


def call(token: str, method: str, params: dict | None = None, attempts: int = 4) -> dict:
    data = urllib.parse.urlencode(params).encode() if params else None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=20) as r:
                return json.load(r)["result"]
        except urllib.error.HTTPError as e:
            hint = " (check TELEGRAM_BOT_TOKEN)" if e.code in (401, 404) else ""
            sys.exit(f"{method} failed: HTTP {e.code}{hint}")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if attempt == attempts:
                sys.exit(f"{method} failed: {getattr(e, 'reason', e)}")
            print(f"{method}: connection dropped, retrying ({attempt}/{attempts - 1})")
            time.sleep(2 * attempt)


def main() -> None:
    env_path = Path(sys.argv[1])
    env = read_env(env_path)
    token, secret, url = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_SECRET"), env.get("INBOX_URL")
    if not token or ":" not in token:
        sys.exit("TELEGRAM_BOT_TOKEN is missing in .env, or is not a bot token")
    if not secret or not url:
        sys.exit("TELEGRAM_SECRET or INBOX_URL is missing in .env")

    print("bot:", "@" + call(token, "getMe")["username"])

    if "CHAT_ID" not in env:
        call(token, "deleteWebhook")   # getUpdates only works while no webhook is set
        chats = {u["message"]["chat"]["id"] for u in call(token, "getUpdates")
                 if "message" in u and u["message"]["chat"]["type"] == "private"}
        if len(chats) != 1:
            sys.exit(f"expected messages from exactly one private chat, found {len(chats)}. "
                     "Send the bot a message and run again.")
        text = env_path.read_text(encoding="utf-8-sig")
        env_path.write_text(text.rstrip("\n") + f"\nCHAT_ID={chats.pop()}\n", encoding="utf-8")
        print("chat id: saved to .env")

    call(token, "setWebhook", {"url": url.rstrip("/") + "/telegram", "secret_token": secret,
                               "allowed_updates": json.dumps(["message"])})
    info = call(token, "getWebhookInfo")
    print("webhook:", info["url"], "| pending:", info.get("pending_update_count", 0))


if __name__ == "__main__":
    main()
