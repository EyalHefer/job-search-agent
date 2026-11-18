"""One run of the agent:

1. read job posts from the WhatsApp exports, and from the Telegram inbox when one is set
2. skip jobs already handled in earlier runs
3. drop clearly irrelevant jobs with keyword rules (no LLM)
4. score the rest against the profile, from the post text
5. open the pages of the best ones, drop closed jobs, keep the full description
6. score those again from the full description and keep the best for the digest

The agent only reads. It never applies, logs in or sends anything in the user's name.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Config
from .memory import Memory
from .skills import SkillError, SkillRunner

FULL_TEXT_CHARS = 4000
DUPLICATE_SIMILARITY = 0.8
UNVERIFIED_MAX = 80


@dataclass
class RunResult:
    digest: list[dict]
    stats: dict = field(default_factory=dict)


def run(config: Config, skills: SkillRunner, memory: Memory, now: datetime,
        since: datetime | None = None, log=print) -> RunResult:
    since = since or now - timedelta(days=config.max_age_days)
    stats = {}

    log(f"Reading job posts since {since:%d.%m.%Y}")
    jobs = skills("whatsapp-export-reader", [config.exports_dir, "--since", since.isoformat()])
    if config.inbox_dir:
        jobs = merge_by_key(jobs, read_inbox(config, skills, since, log))
    decided = memory.decided_jobs()
    new = [job for job in jobs if job["key"] not in memory.seen
           and job["key"] not in decided and job["url"] not in decided]
    stats.update(posts=len(jobs), new=len(new))
    log(f"{len(jobs)} posts, {len(new)} new")
    if not new:
        return RunResult([], stats)

    filtered = skills("rule-filter", [config.preferences], new)
    stats["rejected_by_rules"] = len(filtered["rejected"])
    log(f"{len(filtered['rejected'])} dropped by rules, scoring {len(filtered['kept'])}")

    context = _context_args(config, memory)
    first = skills("profile-matcher", ["--instructions", config.scoring, *context], filtered["kept"])
    for s in first["scored"]:
        memory.remember(s["item"]["key"], seen=now.isoformat(), first_score=s["score"])
    stats["unscored"] = len(first["unscored"])  # not remembered, so they are retried next run

    candidates = [s for s in first["scored"] if s["score"] >= config.read_min_score][:config.read_limit]
    if not candidates:
        memory.save()
        return RunResult([], stats)

    log(f"Opening {len(candidates)} job pages")
    pages = skills("job-page-reader", ["--limit", len(candidates)], candidates)
    for entry in pages["closed"]:
        memory.remember(entry["item"]["key"], seen=now.isoformat(), first_score=entry["score"], closed=True)
    stats.update(closed=len(pages["closed"]), not_checked=len(pages["unknown"]))

    to_rescore = drop_duplicates([with_page_text(e) for e in pages["open"] + pages["unknown"]])
    stats["duplicates"] = len(pages["open"]) + len(pages["unknown"]) - len(to_rescore)

    log(f"Scoring {len(to_rescore)} jobs again from their full description")
    second = skills("profile-matcher", ["--instructions", config.scoring, *context,
                                        "--max-chars", FULL_TEXT_CHARS], to_rescore)
    # If the second round fails for a job, fall back to its first-round score.
    rescored = second["scored"] + [_first_round(first, item) for item in second["unscored"]]
    rescored = [cap_unverified(s) for s in rescored]
    rescored.sort(key=lambda s: -s["score"])
    for s in rescored:
        memory.remember(s["item"]["key"], seen=now.isoformat(), first_score=s["item"]["first_score"],
                        score=s["score"])

    digest = [s for s in rescored if s["score"] >= config.digest_min_score][:config.digest_size]
    memory.save()
    memory.save_run_details({"pages": pages, "rescored": rescored})
    return RunResult(digest, stats)


def read_inbox(config: Config, skills: SkillRunner, since: datetime, log=print) -> list[dict]:
    """Jobs shared by hand to the Telegram bot. Collecting new ones needs the Worker; if it cannot be
    reached, the run goes on with what was collected before, and the new ones wait there for next time."""
    try:
        pulled = skills("telegram-inbox", ["pull", config.inbox_dir])
        log(f"{pulled['saved']} new messages from the Telegram inbox")
    except SkillError as e:
        log(f"Could not collect from the Telegram inbox ({e}); using what was collected before")
    return skills("telegram-inbox", ["read", config.inbox_dir, "--since", since.isoformat()])


def merge_by_key(first: list[dict], second: list[dict]) -> list[dict]:
    """A job shared to the bot may also be in an export. Keeps the first copy of each key."""
    keys = {job["key"] for job in first}
    return first + [job for job in second if job["key"] not in keys]


def cap_unverified(scored: dict) -> dict:
    """A job judged from a thin post, whose page could not be read, cannot earn a top score.
    The scoring instructions say so too, but the model does not always follow them."""
    item = scored["item"]
    if item.get("page_status") != "open" and scored["confidence"] == "low" and scored["score"] > UNVERIFIED_MAX:
        return {**scored, "score": UNVERIFIED_MAX}
    return scored


def with_page_text(entry: dict) -> dict:
    """The job as the second round sees it: the page text when the page was read, else the post."""
    page = entry["page"]
    item = dict(entry["item"])
    if page["status"] == "open" and page["text"]:
        item["text"] = page["text"]
    item["page_status"] = page["status"]
    item["first_score"] = entry["score"]
    return item


def drop_duplicates(items: list[dict]) -> list[dict]:
    """The same job is often posted again under a new link. Keeps the first of each."""
    kept = []
    for item in items:
        if not any(_same_job(item, other) for other in kept):
            kept.append(item)
    return kept


def _same_job(a: dict, b: dict) -> bool:
    if _norm(a["title"]) != _norm(b["title"]):
        return False
    if a.get("company") and b.get("company"):
        return _norm(a["company"]) == _norm(b["company"])
    words_a, words_b = set(_norm(a["text"]).split()), set(_norm(b["text"]).split())
    union = words_a | words_b
    return bool(union) and len(words_a & words_b) / len(union) >= DUPLICATE_SIMILARITY


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").lower()).strip()


def _context_args(config: Config, memory: Memory) -> list:
    files = [config.profile, config.preferences, memory.feedback_context()]
    return [arg for path in files if path for arg in ("--context", path)]


def _first_round(first: dict, item: dict) -> dict:
    match = next(s for s in first["scored"] if s["item"]["key"] == item["key"])
    return {**match, "item": item}
