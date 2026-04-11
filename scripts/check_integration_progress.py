#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import re


CHECKBOX_RE = re.compile(r"^- \[(?P<status>[ xX])\]\s+(?P<item>.+)$")


def _parse_checkboxes(text: str) -> tuple[list[str], list[str]]:
    done: list[str] = []
    pending: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = CHECKBOX_RE.match(line)
        if not match:
            continue
        status = match.group("status").strip().lower()
        item = match.group("item").strip()
        if status == "x":
            done.append(item)
        else:
            pending.append(item)
    return done, pending


def main() -> int:
    parser = argparse.ArgumentParser(description="Check integration progress from report markdown")
    parser.add_argument(
        "--report",
        default="SYSTEM_INTEGRATION_REPORT.md",
        help="Report markdown file path",
    )
    args = parser.parse_args()

    report_path = Path(args.report).resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"report not found: {report_path}")

    text = report_path.read_text(encoding="utf-8")
    done, pending = _parse_checkboxes(text)
    total = len(done) + len(pending)

    print(f"report: {report_path}")
    print(f"total_items: {total}")
    print(f"done_items: {len(done)}")
    print(f"pending_items: {len(pending)}")
    print("")
    print("[Done]")
    for item in done:
        print(f"- {item}")
    print("")
    print("[Pending]")
    for item in pending:
        print(f"- {item}")

    if pending:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

