"""Runs skills from the ai-agent-skills repo. Each skill is a command-line program that reads JSON on
stdin and writes JSON to stdout; its progress messages go straight to the terminal."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol


class SkillError(Exception):
    pass


class SkillRunner(Protocol):
    def __call__(self, name: str, args: list, data: Any = None) -> Any: ...


class Skills:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir

    def __call__(self, name: str, args: list, data: Any = None) -> Any:
        script = self.skills_dir / name / "scripts" / f"{name.replace('-', '_')}.py"
        if not script.exists():
            raise SkillError(f"skill not found: {script}")
        stdin = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run([sys.executable, str(script), *map(str, args)],
                              input=stdin, stdout=subprocess.PIPE, env=env)
        if proc.returncode != 0:
            raise SkillError(f"{name} failed (exit code {proc.returncode})")
        return json.loads(proc.stdout.decode("utf-8"))
