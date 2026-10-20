# SPDX-License-Identifier: GPL-3.0-only
"""tools/ci_gate.sh (release.yml's first job) with a fake `gh` on PATH: it skips the release's host tests only when
the ci workflow completed with success on exactly this commit (every job green); it waits for a running ci; ci failed,
never ran, timed out or an API error all mean "run the tests here"."""
import json
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GATE = os.path.join(ROOT, "tools", "ci_gate.sh")
SHA = "0123456789abcdef0123456789abcdef01234567"
GREEN = [{"name": "host", "conclusion": "success"}, {"name": "generators-macos", "conclusion": "success"}]

# The fake gh: `gh run list` answers the next line of list.jsonl (the last one repeats), `gh run view` answers
# view.json; every call is logged.
FAKE = r"""#!/bin/sh
d="$(dirname "$0")"
echo "$*" >> "$d/calls.log"
[ -f "$d/fail" ] && exit 1
case "$2" in
list) n=$(cat "$d/n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$d/n"
      total=$(wc -l < "$d/list.jsonl"); [ "$n" -le "$total" ] || n=$total
      sed -n "${n}p" "$d/list.jsonl" ;;
view) cat "$d/view.json" ;;
esac
"""


def run_gate(tmp_path, lists, jobs=GREEN, fail=False, wait_s=60):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "gh").write_text(FAKE)
    (b / "gh").chmod(0o755)
    (b / "list.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lists))
    (b / "view.json").write_text(json.dumps({"jobs": jobs}))
    if fail:
        (b / "fail").write_text("")
    out = tmp_path / "github_output"
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out), GITHUB_SHA=SHA,
               CI_GATE_POLL_S="0", CI_GATE_WAIT_S=str(wait_s))
    p = subprocess.run(["sh", GATE], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr          # the gate never fails the release itself
    calls = (b / "calls.log").read_text().splitlines()
    assert all(f"--commit {SHA}" in c for c in calls if c.startswith("run list"))
    return out.read_text().strip(), p.stdout, calls


def row(i, status, conclusion="", sha=SHA):
    return {"databaseId": i, "headSha": sha, "status": status, "conclusion": conclusion}


def test_ci_passed_skips_the_tests(tmp_path):
    o, msg, calls = run_gate(tmp_path, [[row(7, "completed", "success")]])
    assert o == "passed=true"
    assert f"host tests: ci run 7 passed on {SHA}" in msg
    assert calls[-1].startswith("run view 7 --json jobs")


def test_ci_running_then_success_waits(tmp_path):
    lists = [[row(8, "queued")], [row(8, "in_progress")], [row(8, "completed", "success")]]
    o, msg, calls = run_gate(tmp_path, lists)
    assert o == "passed=true" and f"ci run 8 passed on {SHA}" in msg
    assert msg.count("still running") == 2 and sum(c.startswith("run list") for c in calls) == 3


def test_ci_running_then_failure_runs_the_tests(tmp_path):
    o, msg, _ = run_gate(tmp_path, [[row(8, "in_progress")], [row(8, "completed", "failure")]])
    assert o == "passed=false" and "concluded failure" in msg


def test_ci_failed_runs_the_tests(tmp_path):
    o, msg, calls = run_gate(tmp_path, [[row(9, "completed", "failure")]])
    assert o == "passed=false" and "concluded failure" in msg and "running them here" in msg
    assert not any(c.startswith("run view") for c in calls)


def test_no_ci_run_runs_the_tests(tmp_path):
    for i, lists in enumerate(([[]], [[row(5, "completed", "success", sha="f" * 40)]])):  # none / another commit's
        (tmp_path / str(i)).mkdir()
        o, msg, _ = run_gate(tmp_path / str(i), lists)
        assert o == "passed=false" and "no ci run" in msg


def test_success_wins_over_a_second_running_run(tmp_path):
    # pushing the tag starts another ci run on the same commit; the finished green run on main is enough
    o, msg, _ = run_gate(tmp_path, [[row(11, "in_progress"), row(10, "completed", "success")]])
    assert o == "passed=true" and "ci run 10 passed" in msg


def test_jobs_not_all_green_runs_the_tests(tmp_path):
    for i, jobs in enumerate(([{"name": "host", "conclusion": "success"}],                      # macOS job missing
                              [GREEN[0], {"name": "generators-macos", "conclusion": "skipped"}],
                              [])):
        d = tmp_path / str(i)
        d.mkdir()
        o, msg, _ = run_gate(d, [[row(7, "completed", "success")]], jobs=jobs)
        assert o == "passed=false" and "not green" in msg, msg


def test_timeout_and_api_error_run_the_tests(tmp_path):
    (tmp_path / "a").mkdir()
    o, msg, _ = run_gate(tmp_path / "a", [[row(8, "in_progress")]], wait_s=0)
    assert o == "passed=false" and "still running after 0 s" in msg
    (tmp_path / "b").mkdir()
    o, msg, _ = run_gate(tmp_path / "b", [[row(7, "completed", "success")]], fail=True)
    assert o == "passed=false" and "gh run list failed" in msg


def test_release_workflow_wiring():
    yaml = __import__("pytest").importorskip("yaml")
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))
    j = wf["jobs"]
    assert wf["permissions"] == {"contents": "write", "actions": "read"}
    assert "tools/ci_gate.sh" in j["ci-gate"]["steps"][-1]["run"]
    assert j["ci-gate"]["outputs"]["passed"] == "${{ steps.gate.outputs.passed }}"
    assert j["generators-macos"]["if"] == "needs.ci-gate.outputs.passed != 'true'"
    cond = " ".join(j["release"]["if"].split())
    assert cond == ("!cancelled() && needs.ci-gate.result == 'success' && (needs.ci-gate.outputs.passed == 'true' "
                    "|| (needs.generators-macos.result == 'success' && needs.host.result == 'success'))")
    assert j["release"]["needs"] == ["ci-gate", "generators-macos", "host"]
    # the fallback host tests: their own job on ci.yml's arm64 runner; the build stays on x86_64 (the JieLi toolchain)
    assert j["host"]["if"] == "needs.ci-gate.outputs.passed != 'true'" and j["host"]["needs"] == "ci-gate"
    assert j["host"]["runs-on"] == "ubuntu-24.04-arm" and j["release"]["runs-on"] == "ubuntu-latest"
    tests = [s for s in j["host"]["steps"] if "make test" in s.get("run", "")]
    assert len(tests) == 1 and "make pytest" in tests[0]["run"]
    assert not [s for s in j["release"]["steps"] if "make test" in s.get("run", "") or "pytest" in s.get("run", "")]
    names = [s.get("name") for s in j["release"]["steps"]]
    assert names.index("publish") > names.index("build")
    # the package twice: versioned, and under the stable name the docs link (releases/latest/download/frodhes.fwsc)
    pub = " ".join(j["release"]["steps"][names.index("publish")]["run"].split())
    assert 'cp "build/frodhes-$V.fwsc" build/publish/frodhes.fwsc' in pub
    assert 'gh release create "$GITHUB_REF_NAME" "build/frodhes-$V.fwsc" build/publish/frodhes.fwsc \\ ' in pub
    assert "--latest" in pub
    ci = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "ci.yml")))["jobs"]
    assert ci["host"]["runs-on"] == "ubuntu-24.04-arm" and set(ci) >= {"host", "generators-macos"}   # ci_gate.sh's jobs


def test_release_publish_step_uploads_both_names(tmp_path):
    """release.yml's publish step, run with a fake gh: frodhes-X.Y.Z.fwsc and frodhes.fwsc (the same bytes) attached"""
    yaml = __import__("pytest").importorskip("yaml")
    steps = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"]
    run = [s for s in steps if s.get("name") == "publish"][0]["run"]
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gh").write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "$GH_LOG"\n')
    (tmp_path / "bin" / "gh").chmod(0o755)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "frodhes-0.9.9.fwsc").write_bytes(b"package")
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "releases" / "v0.9.9.md").write_text("notes\n")
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", GITHUB_REF_NAME="v0.9.9",
               GH_LOG=str(tmp_path / "gh.log"))
    p = subprocess.run(["sh", "-e", "-c", run], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    args = (tmp_path / "gh.log").read_text().splitlines()
    assert args[:5] == ["release", "create", "v0.9.9", "build/frodhes-0.9.9.fwsc", "build/publish/frodhes.fwsc"]
    assert "--latest" in args and args[args.index("--notes-file") + 1] == "docs/releases/v0.9.9.md"
    assert (tmp_path / "build" / "publish" / "frodhes.fwsc").read_bytes() == b"package"
