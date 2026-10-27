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

# The fake gh: `gh run list --commit` answers the next line of list.jsonl (the last one repeats), `gh run list
# --status success` (the same-tree scan) answers recent.json, `gh run view <id>` answers view-<id>.json or view.json,
# `gh api .../commits/<sha>` answers trees/<sha> (no file: an API error); every call is logged.
FAKE = r"""#!/bin/sh
d="$(dirname "$0")"
echo "$*" >> "$d/calls.log"
[ -f "$d/fail" ] && exit 1
case "$1 $2" in
"run list") case "$*" in
      *--commit*) n=$(cat "$d/n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$d/n"
          total=$(wc -l < "$d/list.jsonl"); [ "$n" -le "$total" ] || n=$total
          sed -n "${n}p" "$d/list.jsonl" ;;
      *) cat "$d/recent.json" ;;
      esac ;;
"run view") if [ -f "$d/view-$3.json" ]; then cat "$d/view-$3.json"; else cat "$d/view.json"; fi ;;
api*) f="$d/trees/${2##*/}"; [ -f "$f" ] || exit 1; cat "$f" ;;
esac
"""


def run_gate(tmp_path, lists, jobs=GREEN, fail=False, wait_s=60, recent=(), trees=None, views=None):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "recent.json").write_text(json.dumps(list(recent)))
    (b / "trees").mkdir()
    for sha, tree in (trees or {}).items():
        (b / "trees" / sha).write_text(tree + "\n")
    for rid, vj in (views or {}).items():
        (b / f"view-{rid}.json").write_text(json.dumps({"jobs": vj}))
    (b / "gh").write_text(FAKE)
    (b / "gh").chmod(0o755)
    (b / "list.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lists))
    (b / "view.json").write_text(json.dumps({"jobs": jobs}))
    if fail:
        (b / "fail").write_text("")
    out = tmp_path / "github_output"
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out), GITHUB_SHA=SHA,
               CI_GATE_POLL_S="0", CI_GATE_WAIT_S=str(wait_s))
    # cwd outside any git repo: the fake shas are not local objects, so the gate asks the (fake) API for the trees
    p = subprocess.run(["sh", GATE], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr          # the gate never fails the release itself
    calls = (b / "calls.log").read_text().splitlines()
    assert all(f"--commit {SHA}" in c or ("--status success" in c and "--workflow ci.yml" in c)
               for c in calls if c.startswith("run list"))
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


TREE = "7" * 40
PR = "a" * 40           # the PR head: GitHub's merge commit SHA has the same tree


def test_green_run_on_the_same_tree_is_reused(tmp_path):
    recent = [row(21, "completed", "success", sha="b" * 40), row(20, "completed", "success", sha=PR)]
    o, msg, calls = run_gate(tmp_path, [[row(30, "in_progress")]], recent=recent,
                             trees={SHA: TREE, PR: TREE, "b" * 40: "8" * 40})
    assert o == "passed=true", msg
    assert f"ci run 20 passed on {PR}, whose git tree {TREE} is identical to {SHA}'s" in msg and "reused" in msg
    assert "run view 20 --json jobs" in calls and not any("--commit" in c for c in calls)   # no wait on SHA's run


def test_a_different_tree_is_not_reused(tmp_path):
    # the PR's tree differs (main moved): wait for ci on exactly this commit, as before
    lists = [[row(30, "in_progress")], [row(30, "completed", "success")]]
    o, msg, calls = run_gate(tmp_path, lists, recent=[row(20, "completed", "success", sha=PR)],
                             trees={SHA: TREE, PR: "9" * 40})
    assert o == "passed=true" and f"ci run 30 passed on {SHA}" in msg and "still running" in msg
    assert not any(c.startswith("run view 20") for c in calls)


def test_same_tree_needs_success_every_job_and_a_known_tree(tmp_path):
    cases = [  # (recent rows, trees, views): each one falls through to "no ci run on SHA"
        ([row(20, "completed", "failure", sha=PR)], {SHA: TREE, PR: TREE}, None),       # not success
        ([row(20, "in_progress", sha=PR)], {SHA: TREE, PR: TREE}, None),                # not finished
        ([row(20, "completed", "success", sha=PR)], {SHA: TREE}, None),                 # PR's tree unknown
        ([row(20, "completed", "success", sha=PR)], {PR: TREE}, None),                  # SHA's tree unknown
        ([row(20, "completed", "success", sha=PR)], {SHA: TREE, PR: TREE},              # a job skipped
         {20: [GREEN[0], {"name": "generators-macos", "conclusion": "skipped"}]}),
    ]
    for i, (recent, trees, views) in enumerate(cases):
        d = tmp_path / str(i)
        d.mkdir()
        o, msg, calls = run_gate(d, [[]], recent=recent, trees=trees, views=views)
        assert o == "passed=false" and "no ci run" in msg, (i, msg)


def test_release_workflow_wiring():
    yaml = __import__("pytest").importorskip("yaml")
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))
    j = wf["jobs"]
    assert wf["permissions"] == {"contents": "write", "actions": "read", "pages": "write"}
    assert "tools/ci_gate.sh" in j["ci-gate"]["steps"][-1]["run"]
    assert j["ci-gate"]["outputs"]["passed"] == "${{ steps.gate.outputs.passed }}"
    assert j["generators-macos"]["if"] == "needs.ci-gate.outputs.passed != 'true'"
    cond = " ".join(j["release"]["if"].split())
    assert cond == ("!cancelled() && needs.ci-gate.result == 'success' && needs.web.result == 'success' && "
                    "(needs.ci-gate.outputs.passed == 'true' "
                    "|| (needs.generators-macos.result == 'success' && needs.host.result == 'success'))")
    assert j["release"]["needs"] == ["ci-gate", "generators-macos", "host", "web"]
    # the fallback host tests: their own job on ci.yml's arm64 runner; the build stays on x86_64 (the JieLi toolchain)
    assert j["host"]["if"] == "needs.ci-gate.outputs.passed != 'true'" and j["host"]["needs"] == "ci-gate"
    assert j["host"]["runs-on"] == "ubuntu-24.04-arm" and j["release"]["runs-on"] == "ubuntu-latest"
    tests = [s for s in j["host"]["steps"] if "make test" in s.get("run", "")]
    assert len(tests) == 1 and "make pytest" in tests[0]["run"]
    assert not [s for s in j["release"]["steps"] if "make test" in s.get("run", "") or "pytest" in s.get("run", "")]
    names = [s.get("name") for s in j["release"]["steps"]]
    assert names.index("draft release") > names.index("build")
    # the package twice: versioned, and under the stable name the docs link (releases/latest/download/frodhes.fwsc)
    pub = " ".join(j["release"]["steps"][names.index("draft release")]["run"].split())
    assert 'cp "build/frodhes-$V.fwsc" build/publish/frodhes.fwsc' in pub
    assert 'set -- "build/frodhes-$V.fwsc" build/publish/frodhes.fwsc \\ ' in pub
    assert 'gh release create "$GITHUB_REF_NAME" "$@"' in pub and "--draft" in pub and "--latest" not in pub
    # the SMK-37 packages (experimental): built after the FM-1's, each board / model, the release version
    builds = [s.get("run", "") for s in j["release"]["steps"]]
    smk = " ".join(j["release"]["steps"][names.index("build the SMK-37 packages (experimental)")]["run"].split())
    assert names.index("build") < names.index("build the SMK-37 packages (experimental)") < names.index("draft release")
    for m in ("", " --model elite", " --model mke"):
        assert f'./build.sh --board smk37{m} --release "$V"' in smk
    assert sum(b.count("./build.sh") for b in builds) == 4        # the FM-1 once, each SMK-37 model once
    assert './build.sh --release "$V"' in " ".join(j["release"]["steps"][names.index("build")]["run"].split())
    ci = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "ci.yml")))["jobs"]
    assert set(ci) >= {"host", "generators-macos"}                                     # ci_gate.sh's jobs
    # the host tests on arm64 in 5 jobs (the scripts, 4 pytest shards); `host` green only when all are
    assert ci["host-scripts"]["runs-on"] == ci["host-pytest"]["runs-on"] == "ubuntu-24.04-arm"
    assert ci["host-pytest"]["strategy"]["matrix"]["shard"] == [1, 2, 3, 4]
    assert ci["host"]["needs"] == ["host-scripts", "host-pytest"] and ci["host"]["if"] == "always()"
    agg = " ".join(s.get("run", "") for s in ci["host"]["steps"])
    assert 'test "${{ needs.host-scripts.result }}" = success' in agg
    assert 'test "${{ needs.host-pytest.result }}" = success' in agg
    shard = " ".join(s.get("run", "") for s in ci["host-pytest"]["steps"])
    assert "make pytest PY=python3 SHARD=${{ matrix.shard }}/4" in shard and "pytest_shard.py --check 4" in shard
    scripts = " ".join(s.get("run", "") for s in ci["host-scripts"]["steps"])
    assert "make test" in scripts and "make host" in scripts


def test_release_publish_step_uploads_both_names(tmp_path):
    """release.yml's publish step, run with a fake gh: frodhes-X.Y.Z.fwsc and frodhes.fwsc (the same bytes) attached,
    then the three SMK-37 packages under their experimental names (tools/release.py SMK_ASSETS, checked by step 9)"""
    yaml = __import__("pytest").importorskip("yaml")
    steps = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"]
    run = [s for s in steps if s.get("name") == "draft release"][0]["run"]
    (tmp_path / "bin").mkdir()
    # `gh release view` fails (no release yet: the first run); every other call's arguments are logged
    (tmp_path / "bin" / "gh").write_text('#!/bin/sh\n[ "$2" = view ] && exit 1\n'
                                         'for a in "$@"; do echo "$a"; done > "$GH_LOG"\n')
    (tmp_path / "bin" / "gh").chmod(0o755)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "frodhes-0.9.9.fwsc").write_bytes(b"package")
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import release as rel
    for pub, built in rel.SMK_ASSETS.items():
        (tmp_path / "build" / built.format(v="0.9.9")).write_bytes(pub.encode())
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "releases" / "v0.9.9.md").write_text("notes\n")
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", GITHUB_REF_NAME="v0.9.9",
               GH_LOG=str(tmp_path / "gh.log"))
    p = subprocess.run(["sh", "-e", "-c", run], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    args = (tmp_path / "gh.log").read_text().splitlines()
    assert args[:5] == ["release", "create", "v0.9.9", "build/frodhes-0.9.9.fwsc", "build/publish/frodhes.fwsc"]
    assert args[5:8] == [f"build/publish/{n}" for n in rel.SMK_ASSETS]
    assert args[8].split("#")[0] == "LICENSES/Apache-2.0.txt"   # the SDK files' licence text (Apache-2.0 s. 4)
    assert args[9] == "--title"                   # nothing else attached
    for n in rel.SMK_ASSETS:                       # each the build of its own model
        assert (tmp_path / "build" / "publish" / n).read_bytes() == n.encode()
    assert args[-1] == "--draft" and "--latest" not in args      # published (and made latest) after the emulator
    assert args[args.index("--notes-file") + 1] == "docs/releases/v0.9.9.md"
    assert (tmp_path / "build" / "publish" / "frodhes.fwsc").read_bytes() == b"package"


def test_release_publish_step_rerun_replaces_the_assets(tmp_path):
    """a rerun (the release exists: a draft left by a failed run): the assets uploaded again with --clobber and the
    notes updated, never a second `release create`"""
    yaml = __import__("pytest").importorskip("yaml")
    steps = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))["jobs"]["release"]["steps"]
    run = [s for s in steps if s.get("name") == "draft release"][0]["run"]
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gh").write_text('#!/bin/sh\necho "$*" >> "$GH_LOG"\n')
    (tmp_path / "bin" / "gh").chmod(0o755)
    (tmp_path / "build").mkdir()
    for n in ("frodhes-0.9.9.fwsc", "frodhes-0.9.9-smk37.fwsc", "frodhes-0.9.9-smk37-elite.fwsc",
              "frodhes-0.9.9-smk37-mke.fwsc"):
        (tmp_path / "build" / n).write_bytes(b"x")
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", GITHUB_REF_NAME="v0.9.9",
               GH_LOG=str(tmp_path / "gh.log"))
    p = subprocess.run(["sh", "-e", "-c", run], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    calls = (tmp_path / "gh.log").read_text().splitlines()
    assert [c.split()[1] for c in calls] == ["view", "upload", "edit"]
    assert calls[1].endswith("--clobber") and "--draft" not in calls[2] and "Frodhes 0.9.9" in calls[2]


def test_the_emulator_is_live_when_the_release_is():
    """release.yml: the emulator is built from the tag and selftested before anything is published (a failure stops
    the release); the release is a draft until gh-pages is deployed and Pages serves it; only then published + latest"""
    yaml = __import__("pytest").importorskip("yaml")
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "release.yml")))
    assert list(wf[True]) == ["push"] and wf[True]["push"] == {"tags": ["v*"]}     # (yaml reads `on:` as True)
    web = wf["jobs"]["web"]
    runs = [" ".join(s.get("run", "").split()) for s in web["steps"]]
    b = next(i for i, r in enumerate(runs) if r.startswith("sh web/build.sh"))
    t = next(i for i, r in enumerate(runs) if "tools/release.py --web-selftest web/dist" in r)
    u = next(i for i, s in enumerate(web["steps"]) if s.get("uses", "").startswith("actions/upload-artifact"))
    assert runs[b] == 'sh web/build.sh --release "${GITHUB_REF_NAME#v}"'            # the page shows the tag's version
    assert runs[t].endswith('"${GITHUB_REF_NAME#v}"') and "chromium webkit" in " ".join(runs)
    assert next(i for i, r in enumerate(runs) if "tools/get_emsdk.sh" in r) < b < t < u
    assert web["steps"][u]["with"]["name"] == "web-dist" and web["steps"][u]["with"]["path"] == "web/dist"
    assert web["container"] == "mcr.microsoft.com/playwright/python:v1.64.0-noble" and "--with-deps" not in " ".join(runs)
    rel = wf["jobs"]["release"]
    st = rel["steps"]
    names = [s.get("name") or s.get("uses") for s in st]
    order = ["build", "build the SMK-37 packages (experimental)", "draft release", "actions/download-artifact@v4",
             "actions/checkout@v4", "deploy the emulator", "wait for Pages", "publish the release"]
    idx = [len(names) - 1 - names[::-1].index(n) if n == "actions/checkout@v4" else names.index(n) for n in order]
    assert idx == sorted(idx) and idx[-1] == len(st) - 1, names                     # publishing is the very last step
    assert st[idx[3]]["with"] == {"name": "web-dist", "path": "web/dist"}          # the bytes the selftest passed
    assert st[idx[4]]["with"] == {"ref": "gh-pages", "path": "build/pages"}
    dep = " ".join(st[idx[5]]["run"].split())
    assert 'sh tools/deploy_pages.sh web/dist build/pages "${GITHUB_REF_NAME#v}"' in dep
    assert "tools/pages_wait.sh" in st[idx[6]]["run"] and "rev-parse HEAD" in st[idx[6]]["run"]
    assert st[idx[7]]["run"].strip() == 'gh release edit "$GITHUB_REF_NAME" --draft=false --latest'
    every = " ".join(s.get("run", "") for s in st)
    assert every.count("--draft=false") == 1 and every.count("--latest") == 1 and every.count("release create") == 1
    assert rel["concurrency"] == {"group": "gh-pages-deploy", "cancel-in-progress": False}


def test_ci_builds_the_emulator_and_never_deploys():
    yaml = __import__("pytest").importorskip("yaml")
    ci = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "ci.yml")))
    assert "tags" not in ci[True]["push"]
    runs = " ".join(" ".join(s.get("run", "").split()) for s in ci["jobs"]["web"]["steps"])
    assert "sh tools/get_emsdk.sh" in runs and "sh web/build.sh" in runs
    assert "playwright install chromium webkit" in runs and "--with-deps" not in runs     # the image has the libraries
    assert ci["jobs"]["web"]["container"] == "mcr.microsoft.com/playwright/python:v1.64.0-noble"
    assert "playwright==1.64.0" in runs                                             # the image's version
    assert "tools/release.py --web-selftest web/dist dev" in runs                 # a local build says dev
    assert "pytest -q -rs tests/py/test_web_knobs.py > knobs.txt" in runs and "! grep -q SKIPPED knobs.txt" in runs
    text = open(os.path.join(ROOT, ".github", "workflows", "ci.yml")).read()
    body = text.split("\njobs:", 1)[1]
    assert not any(w in body for w in ("deploy_pages", "pages_wait", "git push", "gh release", "contents: write"))


def test_the_emsdk_pin_is_the_one_installed():
    """tools/get_emsdk.sh pins Emscripten (the CI cache key hashes the script); the development machine's
    .local/emsdk, when present, is that version"""
    import re
    pin = re.search(r"^EMSDK_VERSION=(\S+)$", open(os.path.join(ROOT, "tools", "get_emsdk.sh")).read(), re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin)
    local = os.path.join(ROOT, ".local", "emsdk", "upstream", "emscripten", "emscripten-version.txt")
    if os.path.exists(local):
        assert open(local).read().strip().strip('"') == pin


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()


WEB = ("index.html", "app.js", "frodhes.js", "frodhes.wasm")


def pages_repo(tmp_path):
    """a bare origin whose gh-pages holds an older emulator and .nojekyll, and a clone of it on gh-pages"""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "gh-pages")
    for f in WEB + (".nojekyll",):
        (seed / f).write_text("old " + f)
    git(seed, "add", "-A")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Emulator 0.9.8")
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(tmp_path / "origin.git")], check=True)
    subprocess.run(["git", "clone", "-q", "-b", "gh-pages", str(tmp_path / "origin.git"), str(tmp_path / "pages")],
                   check=True)
    git(tmp_path / "pages", "config", "user.name", "t")
    git(tmp_path / "pages", "config", "user.email", "t@t")
    dist = tmp_path / "dist"
    dist.mkdir()
    for f in WEB:
        (dist / f).write_text("new " + f)
    (dist / "version.js").write_text('window.FRODHES_VERSION = "0.9.9";\n')
    return dist, tmp_path / "pages", tmp_path / "origin.git"


def deploy(dist, pages, ver="0.9.9"):
    return subprocess.run(["sh", os.path.join(ROOT, "tools", "deploy_pages.sh"), str(dist), str(pages), ver],
                          capture_output=True, text=True, timeout=60)


def test_deploy_pages_commits_once_and_keeps_other_files(tmp_path):
    dist, pages, origin = pages_repo(tmp_path)
    p = deploy(dist, pages)
    assert p.returncode == 0, p.stderr
    assert git(origin, "log", "-1", "--format=%B", "gh-pages") == "Emulator 0.9.9"        # no trailer, no body
    files = git(origin, "ls-tree", "--name-only", "gh-pages").split()
    assert sorted(files) == sorted(WEB + (".nojekyll", "version.js"))                     # .nojekyll kept
    for f in WEB:
        assert git(origin, "show", f"gh-pages:{f}") == "new " + f
    n = git(origin, "rev-list", "--count", "gh-pages")
    p = deploy(dist, pages)                                                                # a rerun: nothing new
    assert p.returncode == 0 and "already up to date" in p.stdout, p.stderr
    assert git(origin, "rev-list", "--count", "gh-pages") == n == "2"


def test_deploy_pages_refuses_a_wrong_build(tmp_path):
    dist, pages, origin = pages_repo(tmp_path)
    head = git(origin, "rev-parse", "gh-pages")
    p = deploy(dist, pages, "0.9.10")                                       # version.js says 0.9.9
    assert p.returncode == 1 and "version.js is not 0.9.10" in p.stderr
    (dist / "frodhes.wasm").unlink()
    p = deploy(dist, pages)
    assert p.returncode == 1 and "frodhes.wasm missing" in p.stderr
    (dist / "frodhes.wasm").write_text("w")
    git(pages, "checkout", "-q", "-b", "other")
    p = deploy(dist, pages)
    assert p.returncode == 1 and "not on gh-pages" in p.stderr
    assert git(origin, "rev-parse", "gh-pages") == head                    # nothing pushed


# The fake gh for pages_wait.sh: `gh api .../pages/builds/latest` answers the next line of builds.txt (the last one
# repeats); POST is logged. The fake curl serves files from live/; the fake sleep returns at once.
PW_GH = r"""#!/bin/sh
d="$(dirname "$0")"
echo "$*" >> "$d/calls.log"
case "$*" in *"-X POST"*) exit 0 ;; esac
n=$(cat "$d/n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$d/n"
total=$(wc -l < "$d/builds.txt"); [ "$n" -le "$total" ] || n=$total
sed -n "${n}p" "$d/builds.txt"
"""
PW_CURL = r"""#!/bin/sh
d="$(dirname "$0")"
for a in "$@"; do u="$a"; done
f="${u##*/}"; f="${f%%\?*}"
cat "$d/../live/$f"
"""


def pages_wait(tmp_path, builds, live_same=True, wait=900):
    b = tmp_path / "bin"
    b.mkdir()
    for name, body in (("gh", PW_GH), ("curl", PW_CURL), ("sleep", "#!/bin/sh\n")):
        (b / name).write_text(body)
        (b / name).chmod(0o755)
    (b / "builds.txt").write_text("".join(x + "\n" for x in builds))
    dist, live = tmp_path / "dist", tmp_path / "live"
    dist.mkdir()
    live.mkdir()
    for f in WEB:
        (dist / f).write_text("new " + f)
        (live / f).write_text(("new " if live_same else "old ") + f)
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", GH_REPO="o/r", PAGES_POLL_S="15", PAGES_WAIT_S=str(wait))
    p = subprocess.run(["sh", os.path.join(ROOT, "tools", "pages_wait.sh"), SHA, str(dist), "https://o.github.io/r/"],
                       env=env, capture_output=True, text=True, timeout=60)
    calls = (b / "calls.log").read_text().splitlines()
    return p, calls


def test_pages_wait_until_built_and_live(tmp_path):
    p, calls = pages_wait(tmp_path, ["built " + "e" * 40, "building " + SHA, "built " + SHA])
    assert p.returncode == 0, p.stderr
    assert f"Pages: built {SHA}" in p.stdout and "every file of" in p.stdout
    assert all("repos/o/r/pages/builds" in c for c in calls) and not any("POST" in c for c in calls)


def test_pages_wait_asks_for_a_build_once_then_fails_on_errors_and_timeouts(tmp_path):
    (tmp_path / "a").mkdir()
    p, calls = pages_wait(tmp_path / "a", ["built " + "e" * 40] * 12 + ["built " + SHA])   # no build started for SHA
    assert p.returncode == 0 and sum("-X POST" in c for c in calls) == 1, p.stdout
    (tmp_path / "b").mkdir()
    p, _ = pages_wait(tmp_path / "b", ["building " + SHA, "errored " + SHA])
    assert p.returncode == 1 and "errored" in p.stderr
    (tmp_path / "c").mkdir()
    p, _ = pages_wait(tmp_path / "c", ["building " + SHA], wait=60)
    assert p.returncode == 1 and "not built after 60 s" in p.stderr
    (tmp_path / "d").mkdir()
    p, _ = pages_wait(tmp_path / "d", ["built " + SHA], live_same=False, wait=60)          # the CDN never serves it
    assert p.returncode == 1 and "still differs" in p.stderr
