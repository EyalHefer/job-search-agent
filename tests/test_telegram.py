"""The digest on Telegram and the feedback that comes back, with a fake Telegram: no network."""

from datetime import date, datetime

import pytest

from job_agent.memory import Memory
from job_agent.telegram import apply_feedback, digest_messages, parse_reply, send_digest

NOW = datetime(2030, 1, 15, 9, 0)


def scored(key, title, score=80, **item):
    return {"item": {"key": key, "url": f"https://jobs.example.com/{key}", "title": title, "company": "Acme",
                     "text": "long post text", **item},
            "score": score, "confidence": "high", "why": "מתאים <מאוד>", "gaps": ""}


class FakeTelegram:
    def __init__(self, fail_after=None):
        self.sent, self.fail_after = [], fail_after

    def send(self, text, buttons=False):
        if self.fail_after is not None and len(self.sent) == self.fail_after:
            from job_agent.telegram import TelegramError
            raise TelegramError("sendMessage: HTTP 500")
        self.sent.append((text, buttons))
        return 100 + len(self.sent)


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path)


def test_digest_is_a_header_then_one_message_per_job():
    messages = digest_messages([scored("a", "QA"), scored("b", "Dev", page_status="unknown")], {"new": 5}, NOW)
    assert len(messages) == 3
    assert messages[0][1] is None and "15.01.2030" in messages[0][0] and "5 משרות חדשות" in messages[0][0]
    assert messages[1][0].startswith("<b>1. QA</b> · Acme · 80%")
    assert "&lt;מאוד&gt;" in messages[1][0]                  # the model's text cannot break the HTML
    assert "לוודא" in messages[2][0] and "לוודא" not in messages[1][0]


def test_empty_digest_says_so():
    [(text, job)] = digest_messages([], {"new": 0}, NOW)
    assert "לא נמצאו" in text and job is None


def test_send_digest_puts_buttons_on_jobs_and_remembers_each_message(memory):
    telegram = FakeTelegram()
    send_digest(telegram, [scored("a", "QA", 85)], {"new": 1}, NOW, memory)
    assert [buttons for _, buttons in telegram.sent] == [False, True]
    assert memory.sent_messages()["jobs"]["102"] == {"key": "a", "url": "https://jobs.example.com/a",
                                                    "title": "QA", "company": "Acme", "score": 85}


def test_messages_sent_before_a_failure_are_remembered(memory):
    with pytest.raises(Exception):
        send_digest(FakeTelegram(fail_after=2), [scored("a", "QA"), scored("b", "Dev")], {}, NOW, memory)
    assert list(memory.sent_messages()["jobs"]) == ["102"]


def sent(memory):
    send_digest(FakeTelegram(), [scored("a", "QA", 85), scored("b", "Dev")], {}, NOW, memory)


def test_button_and_reply_become_one_feedback_entry(memory):
    sent(memory)
    events = [{"update_id": 7, "type": "button", "message_id": 102, "choice": "applied"},
              {"update_id": 8, "type": "reply", "message_id": 102, "text": "90 בדיוק מה שחיפשתי"}]
    assert apply_feedback(events, memory, date(2030, 1, 16)) == 2
    [entry] = memory.feedback()
    assert entry["applied"] is True and entry["match"] == 90 and entry["reason"] == "בדיוק מה שחיפשתי"
    assert entry["key"] == "a" and entry["agent_score"] == 85 and entry["date"] == "2030-01-16"


def test_changing_my_mind_keeps_the_last_press(memory):
    sent(memory)
    events = [{"update_id": 7, "type": "button", "message_id": 103, "choice": "applied"},
              {"update_id": 9, "type": "button", "message_id": 103, "choice": "skipped"}]
    apply_feedback(events, memory, date(2030, 1, 16))
    assert memory.feedback()[0]["applied"] is False


def test_each_event_is_handled_once(memory):
    sent(memory)
    events = [{"update_id": 7, "type": "reply", "message_id": 102, "text": "סיבה"}]
    apply_feedback(events, memory, date(2030, 1, 16))
    assert apply_feedback(events, memory, date(2030, 1, 16)) == 0
    assert len(memory.feedback()) == 1


def test_replies_to_other_bot_messages_are_ignored(memory):
    sent(memory)
    events = [{"update_id": 7, "type": "reply", "message_id": 101, "text": "header reply"},
              {"update_id": 8, "type": "button", "message_id": 999, "choice": "applied"}]
    assert apply_feedback(events, memory, date(2030, 1, 16)) == 0
    assert memory.feedback() == []


def test_feedback_from_the_command_line_merges_with_telegram_feedback(memory):
    sent(memory)
    apply_feedback([{"update_id": 7, "type": "button", "message_id": 102, "choice": "applied"}], memory, date(2030, 1, 16))
    memory.record_decision({"key": "a", "url": "https://jobs.example.com/a"}, date(2030, 1, 17), match=70)
    [entry] = memory.feedback()
    assert entry["applied"] is True and entry["match"] == 70 and entry["title"] == "QA"


@pytest.mark.parametrize("text, expected", [
    ("60 דורש 3 שנות ניסיון", (60, "דורש 3 שנות ניסיון")),
    ("85%", (85, "")),
    ("70 - רחוק מדי", (70, "רחוק מדי")),
    ("רחוק מדי", (None, "רחוק מדי")),
    ("150 שקל לשעה", (None, "150 שקל לשעה")),
])
def test_parse_reply(text, expected):
    assert parse_reply(text) == expected
