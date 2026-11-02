"""What the agent remembers between runs, kept as plain files in the data folder:
- seen.json       jobs already handled, so they are not scored or shown again
- feedback.jsonl  the user's decisions on jobs, given to the model as context
- last_digest.json the jobs in the latest digest, so feedback can refer to them by number
- last_run.json    the pages read and scores given in the latest run
"""

import json
from pathlib import Path


class Memory:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.seen_file = self.dir / "seen.json"
        self.feedback_file = self.dir / "feedback.jsonl"
        self.last_digest_file = self.dir / "last_digest.json"
        self.seen: dict[str, dict] = self._read_json(self.seen_file, {})

    def remember(self, key: str, **info) -> None:
        self.seen[key] = info

    def save(self) -> None:
        self._write_json(self.seen_file, self.seen)

    def feedback(self) -> list[dict]:
        if not self.feedback_file.exists():
            return []
        lines = self.feedback_file.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def add_feedback(self, entry: dict) -> None:
        with self.feedback_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def decided_jobs(self) -> set[str]:
        """Keys and links of jobs the user already gave feedback on."""
        return {value for f in self.feedback() for value in (f.get("key"), f.get("url")) if value}

    def feedback_context(self) -> Path | None:
        """Writes past decisions as text the model can read. None when there are none."""
        lines = []
        for f in self.feedback():
            decision = "applied" if f.get("applied") else "did not apply" if f.get("applied") is False else "undecided"
            if f.get("status") == "closed":
                continue  # a closed job says nothing about the user's taste
            parts = [f"- {f.get('title')} ({f.get('company') or 'unknown company'}): {decision}"]
            if f.get("match") is not None:
                parts.append(f"their own match {f['match']}%")
            if f.get("reason"):
                parts.append(f"reason: {f['reason']}")
            lines.append(", ".join(parts))
        if not lines:
            return None
        path = self.dir / "feedback_context.md"
        path.write_text("# The candidate's past decisions on jobs\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return path

    def save_run_details(self, details: dict) -> None:
        """What the last run saw and decided, to check its work when a result looks wrong."""
        self._write_json(self.dir / "last_run.json", details)

    def last_digest(self) -> list[dict]:
        return self._read_json(self.last_digest_file, [])

    def save_last_digest(self, jobs: list[dict]) -> None:
        self._write_json(self.last_digest_file, jobs)

    @staticmethod
    def _read_json(path: Path, default):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    @staticmethod
    def _write_json(path: Path, value) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
