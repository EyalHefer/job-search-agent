"""Job search agent: reads job posts, ranks them against your profile and writes a short digest.
It only reads. Applying is up to you."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from job_agent.config import load_config
from job_agent.digest import write_digest
from job_agent.memory import Memory
from job_agent.pipeline import run
from job_agent.skills import Skills


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser("run", help="rank new jobs and write a digest")
    run_cmd.add_argument("--since", type=datetime.fromisoformat, help="read posts from this date (default: max_age_days ago)")

    feedback = commands.add_parser("feedback", help="record your decision on a job from the last digest")
    feedback.add_argument("job", help="its number in the last digest, or its link")
    feedback.add_argument("--applied", choices=["yes", "no"], required=True)
    feedback.add_argument("--match", type=int, help="how well it fits you, 0-100")
    feedback.add_argument("--reason", default="")

    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(args.config)
    memory = Memory(config.data_dir)

    if args.command == "run":
        now = datetime.now()
        result = run(config, Skills(config.skills_dir), memory, now, since=args.since,
                     log=lambda m: print(m, file=sys.stderr, flush=True))
        message = write_digest(result.digest, result.stats, now)
        out = config.data_dir / "digests" / f"{now:%Y-%m-%d_%H%M}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(message, encoding="utf-8")
        memory.save_last_digest([{**job["item"], "score": job["score"]} for job in result.digest])
        print(message)
        print(f"\nSaved to {out}", file=sys.stderr)
    else:
        record_feedback(memory, args)


def record_feedback(memory: Memory, args) -> None:
    jobs = memory.last_digest()
    if args.job.isdigit():
        n = int(args.job)
        if not 1 <= n <= len(jobs):
            sys.exit(f"The last digest has {len(jobs)} jobs")
        job = jobs[n - 1]
    else:
        job = next((j for j in jobs if j["url"] == args.job), {"url": args.job, "title": None})
    memory.add_feedback({
        "date": datetime.now().date().isoformat(),
        "key": job.get("key"),
        "url": job["url"],
        "title": job.get("title"),
        "company": job.get("company"),
        "agent_score": job.get("score"),
        "applied": args.applied == "yes",
        "match": args.match,
        "reason": args.reason,
    })
    print(f"Saved: {job.get('title') or job['url']}")


if __name__ == "__main__":
    main()
