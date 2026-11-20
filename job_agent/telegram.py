"""The digest on my phone: each job is sent to my Telegram chat as its own message, with two buttons.
A press, or a reply to the job's message ("60 דורש 3 שנות ניסיון"), reaches the inbox Worker, and the
next run turns it into feedback. The agent remembers which job it put in each message, so the Worker
never needs to know anything about jobs.

The bot only talks to me, in my own chat. It never writes to anyone else.
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime

from .digest import summary
from .memory import Memory

BUTTONS = {"applied": ("✅ הגשתי", True), "skipped": ("❌ לא הגשתי", False)}
REPLY = re.compile(r"\s*(\d{1,3})\s*%?\s*[-–,:.]?\s*(.*)", re.DOTALL)
ATTEMPTS = 3
RETRY_SECONDS = 2
JOB_FIELDS = ("key", "url", "title", "company", "score")


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    @classmethod
    def from_env(cls) -> "Telegram | None":
        token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("CHAT_ID")
        return cls(token, chat_id) if token and chat_id else None

    def send(self, text: str, buttons: bool = False) -> int:
        """Sends one message and returns its id."""
        params = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": True}}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": choice}
                                                           for choice, (label, _) in BUTTONS.items()]]}
        return self._call("sendMessage", params)["message_id"]

    def _call(self, method: str, params: dict) -> dict:
        request = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                         data=json.dumps(params).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        for attempt in range(1, ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return json.load(response)["result"]
            except urllib.error.HTTPError as e:
                # Telegram explains what was wrong; the URL, which holds the token, is never shown.
                detail = json.loads(e.read() or b"{}").get("description", "")
                if e.code == 429 and attempt < ATTEMPTS:
                    time.sleep(RETRY_SECONDS * attempt)
                    continue
                raise TelegramError(f"{method}: HTTP {e.code} {detail}".strip()) from None
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                if attempt == ATTEMPTS:
                    raise TelegramError(f"{method}: {getattr(e, 'reason', e)}") from None
                time.sleep(RETRY_SECONDS * attempt)


def digest_messages(jobs: list[dict], stats: dict, now: datetime) -> list[tuple[str, dict | None]]:
    """The messages of one digest, in order: a header, then one per job (with the job it is about)."""
    header = [f"<b>משרות מומלצות · {now:%d.%m.%Y}</b>", html.escape(summary(stats)), ""]
    if jobs:
        header.append("לכל משרה: כפתור, או תשובה להודעה עם התאמה והסבר, למשל: 60 דורש 3 שנות ניסיון")
    else:
        header.append("לא נמצאו משרות חדשות שמתאימות מספיק.")
    return [("\n".join(header), None)] + [(_job_message(n, job), job) for n, job in enumerate(jobs, 1)]


def _job_message(n: int, job: dict) -> str:
    item = job["item"]
    company = item.get("company") or "חברה לא ידועה"
    lines = [f"<b>{n}. {html.escape(item['title'])}</b> · {html.escape(company)} · {job['score']}%",
             f"למה: {html.escape(job['why'])}"]
    if job.get("gaps"):
        lines.append(f"חסר: {html.escape(job['gaps'])}")
    if item.get("page_status") == "unknown":
        lines.append("<i>לא הצלחתי לוודא שהמשרה עדיין פתוחה</i>")
    lines.append(html.escape(item["url"]))
    return "\n".join(lines)


def send_digest(telegram: Telegram, jobs: list[dict], stats: dict, now: datetime, memory: Memory) -> None:
    state = memory.sent_messages()
    for text, job in digest_messages(jobs, stats, now):
        message_id = telegram.send(text, buttons=job is not None)
        if job is not None:
            state["jobs"][str(message_id)] = {f: {**job["item"], "score": job["score"]}.get(f) for f in JOB_FIELDS}
            memory.save_sent_messages(state)   # after every message, so a failure halfway loses nothing


def apply_feedback(events: list[dict], memory: Memory, today: date) -> int:
    """Turns new button presses and replies into feedback. Returns how many were about digest jobs."""
    state = memory.sent_messages()
    applied = 0
    for event in sorted(events, key=lambda e: e["update_id"]):
        if event["update_id"] <= state["last_event"]:
            continue    # handled in an earlier run
        state["last_event"] = event["update_id"]
        job = state["jobs"].get(str(event["message_id"]))
        if job is None:
            continue    # a reply to a bot message that is not a digest job
        day = datetime.fromtimestamp(event["date"]).date() if event.get("date") else today
        if event["type"] == "button" and event.get("choice") in BUTTONS:
            memory.record_decision(job, day, applied=BUTTONS[event["choice"]][1])
        elif event["type"] == "reply":
            match, reason = parse_reply(event["text"])
            memory.record_decision(job, day, match=match, reason=reason)
        else:
            continue
        applied += 1
    memory.save_sent_messages(state)
    return applied


def parse_reply(text: str) -> tuple[int | None, str]:
    """"60 דורש 3 שנות ניסיון" -> (60, "דורש 3 שנות ניסיון"). A reply without a leading number is all reason."""
    found = REPLY.fullmatch(text)
    if found and int(found.group(1)) <= 100:
        return int(found.group(1)), found.group(2).strip()
    return None, text.strip()
