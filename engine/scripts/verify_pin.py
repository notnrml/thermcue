#!/usr/bin/env python
"""Check that the committed cache still reproduces the documented results.

The response cache is committed so a clone reproduces the numbers in
docs/headline.md and the README exactly, with no key and no network. That
guarantee is easy to break by accident: any run *without* THERMCUE_OFFLINE=1
refreshes the forecast entries from a live provider, and the forecast for an
event four days out moves daily. It broke three separate times during
development, each time silently, and each time the fix was to notice.

So this checks it instead of trusting it. Run before committing, and in CI:

    .venv/bin/python scripts/verify_pin.py

Exits non-zero and names the drifted figures if the committed cache no longer
produces the committed documentation. The fix is one of two things, and the
script says which applies:

  the cache drifted    -> git checkout -- engine/data/cache/
  the results changed  -> THERMCUE_OFFLINE=1 scripts/headline.py, and commit both
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
DOCS = ENGINE.parent / "docs"
HEADLINE = DOCS / "headline.md"


def figures(markdown: str) -> dict[str, str]:
    """The numbers a reader would actually check, keyed by row label."""
    out: dict[str, str] = {}
    for label, *cells in re.findall(
        r"^\| ([A-Z][^|]+?) \| ([\d,]+(?: min)?) \| ([\d,]+(?: min)?) \|", markdown, re.M
    ):
        out[label.strip()] = f"{cells[0]} -> {cells[1]}"
    for key in ("Peak forecast air temperature", "Analogue day", "Band census across zone-hours"):
        found = re.search(rf"^\| {re.escape(key)} \| (.+?) \|$", markdown, re.M)
        if found:
            out[key] = found.group(1).strip()
    return out


def main() -> int:
    if not HEADLINE.exists():
        print(f"FAIL  {HEADLINE} does not exist; nothing to verify against.")
        return 2

    committed = HEADLINE.read_text()

    env = dict(os.environ)
    env["THERMCUE_OFFLINE"] = "1"
    env["FORTYGUARD_API_KEY"] = ""
    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "headline.py")],
        cwd=ENGINE,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("FAIL  headline.py did not run against the committed cache.")
        print(result.stderr[-1500:])
        return 3

    regenerated = HEADLINE.read_text()
    before, after = figures(committed), figures(regenerated)

    drifted = {k: (v, after.get(k)) for k, v in before.items() if after.get(k) != v}
    # Restore the committed copy either way: this script verifies, it does not
    # decide to change the documentation.
    HEADLINE.write_text(committed)

    if not drifted:
        print(f"ok    committed cache reproduces {HEADLINE.relative_to(ENGINE.parent)}")
        print(f"      {len(before)} figures checked, all identical")
        return 0

    print("FAIL  the committed cache no longer produces the committed results.\n")
    for key, (was, now) in drifted.items():
        print(f"      {key}")
        print(f"        documented: {was}")
        print(f"        produced:   {now}")
    print()
    print("      If the cache drifted (a live run refreshed the forecast):")
    print("        git checkout -- engine/data/cache/")
    print()
    print("      If the results genuinely changed and should be republished:")
    print("        cd engine && THERMCUE_OFFLINE=1 .venv/bin/python scripts/headline.py")
    print("        then commit docs/headline.md and the cache together, and update")
    print("        the tables in README.md from the regenerated file.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
