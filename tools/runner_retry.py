#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""runner-retry's decision (.github/workflows/runner-retry.yml): rerun a failed run's jobs only when GitHub never
started them.

    tools/runner_retry.py JOBS.json      (the run attempt's jobs, gh api .../attempts/N/jobs) -> exit 0 and "rerun"
                                         when the failed jobs should run again, exit 0 and "no rerun: <why>" otherwise

GitHub sometimes never starts a job ("The job was not acquired by Runner of type hosted even after multiple attempts":
macOS arm64 capacity, 2026-11-12). Such a job fails with no step run. Since 2026-11-15 a real failure cancels the
whole run (ci.yml: the last step of every job), so a run's failed and cancelled jobs mix the real failure (steps run)
with siblings cancelled mid-way (steps run) and siblings cancelled before they started (no step).

The rule: at least one job concluded `failure`, and no failed or cancelled job ran a step. A job that ran a step and
failed, or was cancelled after it started, means a real failure somewhere in the run: no rerun. A run with no failed
job (cancelled by hand, or by a newer push) is not rerun either."""
import json
import sys


def decide(jobs):
    """(rerun?, why) for a list of job dicts {name, conclusion, steps}"""
    bad = [j for j in jobs if j.get("conclusion") in ("failure", "cancelled")]
    if not any(j.get("conclusion") == "failure" for j in bad):
        return False, "no failed job (a cancelled run is never rerun)"
    ran = [j["name"] for j in bad if j.get("steps")]
    if ran:
        return False, "a job ran and failed or was cancelled mid-way (a real failure): " + ", ".join(ran)
    return True, "every failed job ran no step (no runner): " + ", ".join(j["name"] for j in bad)


def main(argv):
    doc = json.load(open(argv[1]))
    rerun, why = decide(doc.get("jobs", doc) if isinstance(doc, dict) else doc)
    print(("rerun: " if rerun else "no rerun: ") + why)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
