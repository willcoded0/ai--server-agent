from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import sys

import yaml
import subprocess


def tail_remote_lines(user: str, host: str, path: str, n: int) -> tuple[list[str], str | None]:
    """Fetch last n lines from remote log via SSH.

    Returns (lines, error_message). If error_message is not None, SSH failed.
    """
    cmd = ["ssh", f"{user}@{host}", f"tail -n {n} {path}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return [], result.stderr.strip() or f"SSH exited with code {result.returncode}"
    return result.stdout.splitlines(), None


@dataclass(frozen=True)
class Pattern:
    name: str
    match: str


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        try:
            return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            print(f"Failed to parse config file: {path}")
            print(f"Error: {e}")
            print("Hint: Overwrite agent/config.yaml with agent/config.example.yaml and retry")
            sys.exit(2)


def tail_last_lines(path: Path, n: int) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines[-n:]
    except FileNotFoundError:
        return []


def find_matches(lines: Iterable[str], patterns: list[Pattern]) -> list[tuple[Pattern, str]]:
    hits: list[tuple[Pattern, str]] = []
    for line in lines:
        for p in patterns:
            if re.search(p.match, line):
                hits.append((p, line.rstrip("\n")))
    return hits


def write_incident(
    postmortems_dir: Path,
    title: str,
    log_path: Path,
    context: list[str],
    hits: list[tuple[Pattern, str]],
) -> Path:
    postmortems_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    fname = f"{ts}_{title.replace(' ', '_')}.md"
    out = postmortems_dir / fname

    hit_lines = "\n".join([f"- **{p.name}**: `{line}`" for p, line in hits]) if hits else "- (none)"

    body: list[str] = []
    body.append(f"# Incident: {title}")
    body.append("")
    body.append(f"- Time: {datetime.now().isoformat(timespec='seconds')}")
    body.append(f"- Log: `{log_path}`")
    body.append("")
    body.append("## Detected signals")
    body.append(hit_lines)
    body.append("")
    body.append("## Context (tail)")
    body.append("```log")
    body.extend([l.rstrip("\n") for l in context])
    body.append("```")
    body.append("")
    body.append("## Next steps")
    body.append("- Add runbook steps you took in `docs/04_Runbook.md`.")
    body.append("- If this repeats, add a new detection pattern in `agent/config.yaml`.")
    body.append("")

    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    return out


def main() -> int:
    cfg_path = Path(os.environ.get("AGENT_CONFIG", "agent/config.yaml"))
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path} (copy agent/config.example.yaml -> agent/config.yaml)")
        return 2

    cfg = load_config(cfg_path)

    # Validate remote section
    remote = cfg.get("remote")
    if not remote:
        print("Config error: missing 'remote' section")
        return 2
    user = remote.get("user")
    host = remote.get("host")
    log_file = remote.get("log_file")
    if not all([user, host, log_file]):
        print("Config error: 'remote' must have 'user', 'host', and 'log_file'")
        return 2

    tail_n = int(cfg.get("tail_lines", 200))
    postmortems_dir = Path(cfg.get("postmortems_dir", "docs/05_Postmortems"))
    patterns = [Pattern(**p) for p in (cfg.get("patterns") or [])]

    # Fetch logs via SSH
    lines, ssh_error = tail_remote_lines(user, host, log_file, tail_n)
    if ssh_error:
        print(f"SSH failed: {ssh_error}")
        return 1
    if not lines:
        print(f"No log lines read from {user}@{host}:{log_file}")
        return 1

    hits = find_matches(lines, patterns)
    if not hits:
        print("No incident patterns matched.")
        return 0

    title = hits[0][0].name
    log_path = Path(log_file)
    out = write_incident(postmortems_dir, title, log_path, lines, hits[:10])
    print(f"Wrote incident note: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

