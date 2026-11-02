"""Runs the whole pipeline with fake skills and invented jobs: no network, no LLM, no browser."""

from datetime import datetime
from pathlib import Path

import pytest

from job_agent.config import Config
from job_agent.digest import write_digest
from job_agent.memory import Memory
from job_agent.pipeline import cap_unverified, drop_duplicates, run

NOW = datetime(2030, 1, 15, 9, 0)


def job(key, title, company="", text="post text"):
    return {"key": key, "url": f"https://jobs.example.com/{key}", "title": title, "company": company,
            "text": text, "group": "Jobs group", "sent_at": "2030-01-14T10:00:00"}


class FakeSkills:
    """Plays each skill's part, and records what it was given."""

    def __init__(self, posts, first_scores, page_status, second_scores, rejected=()):
        self.posts, self.first_scores, self.page_status = posts, first_scores, page_status
        self.second_scores, self.rejected = second_scores, set(rejected)
        self.calls = []
        self.matcher_round = 0

    def __call__(self, name, args, data=None):
        self.calls.append((name, [str(a) for a in args], data))
        if name == "whatsapp-export-reader":
            return self.posts
        if name == "rule-filter":
            return {"kept": [j for j in data if j["key"] not in self.rejected],
                    "rejected": [{"item": j, "rule": "field", "matched": "x"} for j in data if j["key"] in self.rejected]}
        if name == "profile-matcher":
            self.matcher_round += 1
            scores = self.first_scores if self.matcher_round == 1 else self.second_scores
            scored = [{"item": j, "score": scores[j["key"]], "confidence": "high", "why": "סיבה", "gaps": ""}
                      for j in data if j["key"] in scores]
            return {"scored": sorted(scored, key=lambda s: -s["score"]),
                    "unscored": [j for j in data if j["key"] not in scores]}
        if name == "job-page-reader":
            out = {"open": [], "closed": [], "unknown": []}
            for entry in data:
                status = self.page_status[entry["item"]["key"]]
                text = f"full description of {entry['item']['key']}" if status == "open" else ""
                out[status].append({**entry, "page": {"status": status, "reason": "", "final_url": None, "text": text}})
            return out
        raise AssertionError(name)


@pytest.fixture
def config(tmp_path):
    return Config(skills_dir=tmp_path, exports_dir=tmp_path, profile=tmp_path / "profile.md",
                  preferences=tmp_path / "prefs.yaml", scoring=tmp_path / "scoring.md", data_dir=tmp_path / "data",
                  read_min_score=65, digest_min_score=70)


def test_full_run_keeps_open_well_scored_jobs_in_order(config):
    skills = FakeSkills(
        posts=[job("a", "Automation"), job("b", "Backend"), job("c", "QA"), job("d", "Sales"), job("e", "Old")],
        first_scores={"a": 90, "b": 80, "c": 85, "e": 40},
        page_status={"a": "open", "b": "open", "c": "closed"},
        second_scores={"a": 75, "b": 88},
        rejected={"d"},
    )
    result = run(config, skills, Memory(config.data_dir), NOW, log=lambda m: None)

    assert [j["item"]["key"] for j in result.digest] == ["b", "a"]
    assert result.digest[0]["item"]["text"] == "full description of b"
    assert result.stats == {"posts": 5, "new": 5, "rejected_by_rules": 1, "unscored": 0,
                            "closed": 1, "not_checked": 0, "duplicates": 0}
    page_reader_input = next(data for name, _, data in skills.calls if name == "job-page-reader")
    assert [e["item"]["key"] for e in page_reader_input] == ["a", "c", "b"]  # low first-round score not opened


def test_second_run_skips_jobs_already_handled(config):
    posts = [job("a", "Automation")]
    memory = Memory(config.data_dir)
    run(config, FakeSkills(posts, {"a": 90}, {"a": "open"}, {"a": 90}), memory, NOW, log=lambda m: None)

    skills = FakeSkills(posts, {}, {}, {})
    result = run(config, skills, Memory(config.data_dir), NOW, log=lambda m: None)
    assert result.digest == []
    assert [name for name, _, _ in skills.calls] == ["whatsapp-export-reader"]


def test_jobs_with_feedback_are_not_shown_again(config):
    memory = Memory(config.data_dir)
    memory.add_feedback({"url": "https://jobs.example.com/a", "title": "Automation", "applied": True})
    memory.add_feedback({"key": "b", "url": "https://jobs.example.com/b?ref=1", "title": "QA", "applied": False})
    skills = FakeSkills([job("a", "Automation"), job("b", "QA"), job("c", "Backend")], {"c": 90}, {"c": "open"}, {"c": 90})
    result = run(config, skills, memory, NOW, log=lambda m: None)
    assert [j["item"]["key"] for j in result.digest] == ["c"]


def test_unscored_jobs_are_retried_next_run(config):
    run(config, FakeSkills([job("a", "Automation")], {}, {}, {}), Memory(config.data_dir), NOW, log=lambda m: None)
    assert "a" not in Memory(config.data_dir).seen


def test_failed_second_round_falls_back_to_first_score(config):
    skills = FakeSkills([job("a", "Automation")], {"a": 82}, {"a": "unknown"}, second_scores={})
    result = run(config, skills, Memory(config.data_dir), NOW, log=lambda m: None)
    assert [(j["item"]["key"], j["score"]) for j in result.digest] == [("a", 82)]
    assert result.digest[0]["item"]["page_status"] == "unknown"


def test_second_round_gets_full_text_and_past_feedback(config):
    memory = Memory(config.data_dir)
    memory.add_feedback({"title": "QA Automation", "company": "Acme", "applied": True, "reason": "good stack"})
    skills = FakeSkills([job("a", "Automation")], {"a": 90}, {"a": "open"}, {"a": 90})
    run(config, skills, memory, NOW, log=lambda m: None)

    _, args, _ = [c for c in skills.calls if c[0] == "profile-matcher"][1]
    assert args[args.index("--max-chars") + 1] == "4000"
    feedback_file = Path(args[[i for i, a in enumerate(args) if a == "--context"][-1] + 1])
    assert "QA Automation (Acme): applied, reason: good stack" in feedback_file.read_text(encoding="utf-8")


def test_unread_title_only_job_is_capped_but_read_or_detailed_jobs_are_not():
    def scored(status, confidence):
        return {"item": {"page_status": status}, "score": 92, "confidence": confidence, "why": "", "gaps": ""}
    assert cap_unverified(scored("unknown", "low"))["score"] == 80
    assert cap_unverified(scored("unknown", "high"))["score"] == 92  # the post itself had details
    assert cap_unverified(scored("open", "low"))["score"] == 92


def test_same_job_posted_twice_is_kept_once():
    text = "Junior automation engineer, Python and Selenium, hybrid in Tel Aviv"
    items = [job("a", "Automation & Test Engineer", text=text), job("b", "Automation & Test Engineer", text=text),
             job("c", "Automation & Test Engineer", text="A different company building drones in Haifa")]
    assert [i["key"] for i in drop_duplicates(items)] == ["a", "c"]


def test_same_title_at_different_companies_is_not_a_duplicate():
    items = [job("a", "Software Engineer", "Acme"), job("b", "Software Engineer", "Globex")]
    assert len(drop_duplicates(items)) == 2


def test_digest_lists_jobs_with_links_and_warnings():
    jobs = [{"item": {**job("a", "Junior Automation"), "page_status": "unknown"}, "score": 88,
             "why": "מתאים", "gaps": "Java"}]
    text = write_digest(jobs, {"new": 12, "closed": 2}, NOW)
    assert "15.01.2030" in text and "12 משרות חדשות · 2 כבר נסגרו" in text
    assert "1. *Junior Automation* · חברה לא ידועה · 88%" in text
    assert "חסר: Java" in text and "לא הצלחתי לוודא" in text
    assert "https://jobs.example.com/a" in text
