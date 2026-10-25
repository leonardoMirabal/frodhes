# SPDX-License-Identifier: GPL-3.0-only
"""The audio ISR's CPU budget on the host (tools/cpu_budget.py, the FM-1's measured costs in tools/cpu_costs.json):
every release scenario's predicted whole-ISR worst block within the 4500 us target. A change that adds voices, an
effect's work or ISR time updates tools/cpu_costs.json from its device run (tools/hil_cpu.py --worst, 3 runs) and this
test judges it; the model never predicts under what the device measured."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import cpu_budget as cb  # noqa: E402

T = cb.load()
FW = cb.firmware_voices()

# Scenarios known to be over the target, each with what fixes it. An entry that no longer matches the firmware fails
# (test_known_over_is_current): remove it with the fix, so it can never hide a later regression.
KNOWN_OVER = {}   # (the Rhodes at 10 voices, 4551 us predicted from the 9-voice rows plus a voice, went with FM-1 batch 1's
#                   10-voice rows, sonic-d3df27f: 3768 us; the Rhodes at 9, 4604 on 0.9.4's costs, went with 0.9.5's)


def test_the_table():
    for e in cb.ENGINES:
        assert T["worst_app"][e], e
        for n, r in T["worst_app"][e].items():
            assert 0 < min(r["us"]) <= max(r["us"]) < T["block_us"] and r["src"], (e, n)
    assert hi(T["app"]["idle_no_fx"]) < hi(T["app"]["idle_every_fx"])
    assert hi(T["tail_us"]["closed"]) <= hi(T["tail_us"]["44100"]) < hi(T["tail_us"]["48000"])
    assert all(isinstance(T["fx_us"][k], (int, float)) for k in cb.fx_names(T))
    for k, r in T.get("est_savings_us", {}).items():     # information only: never in the gate
        assert k.startswith("_") or (0 <= min(r["us"]) <= max(r["us"]) and r["src"].startswith("[E]")), k
    lo, hi_ = cb.est_savings(T)
    assert 0 <= lo <= hi_
    for k, r in T.get("est_costs_us", {}).items():       # added to every prediction with the reverb on
        assert k.startswith("_") or (0 <= min(r["us"]) <= max(r["us"]) and r["src"].startswith("[E]")), k


def hi(r):
    return max(r["us"])


def test_firmware_voices():
    assert set(FW) == set(cb.ENGINES)
    assert all(1 <= v <= 16 for v in FW.values()), FW
    assert FW["vibes"] == FW["marimba"]                      # the bar engines share BAR_NV


@pytest.mark.parametrize("m", T["whole_isr"], ids=lambda m: f"{m['engine']}{m['voices']}-{m['usb']}-{m['fx']}")
def test_the_model_covers_every_device_measurement(m):
    """an upper bound: never under a whole-ISR maximum the FM-1 reported (CC 116)"""
    p = cb.predict(T, m["engine"], m["voices"], m["usb"], m["fx"], m.get("bend", False))
    assert p["whole"] >= max(m["us"]), (p, m)


def test_the_model_is_monotonic():
    for e in cb.ENGINES:
        a, b = cb.predict(T, e, 8), cb.predict(T, e, 9)
        assert a["whole"] < b["whole"], e
        assert cb.predict(T, e, 9, "44100")["whole"] < b["whole"] < cb.predict(T, e, 9, bend=True)["whole"]
        assert cb.predict(T, e, 9, fx="none")["whole"] < cb.predict(T, e, 9, fx=["reverb"])["whole"] < b["whole"]


@pytest.mark.parametrize("bend", [False, True], ids=["held", "bending"])
@pytest.mark.parametrize("engine", cb.ENGINES)
def test_release_scenario_within_the_target(engine, bend):
    """every effect at its heaviest, the pedal, the engine's voices, the USB stream at 48 kHz; bending only once the
    firmware has the bend (otherwise the prediction is information)"""
    p = cb.predict(T, engine, FW[engine], bend=bend)
    print(f"{engine} {FW[engine]} voices, 48 kHz, every effect{', bending' if bend else ''}: app {p['app']} + tail "
          f"{p['tail']} + bend {p['bend_us']} = {p['whole']} us (target {T['target_us']})")
    if bend and not cb.firmware_has_bend():
        pytest.skip(f"no pitch bend in the firmware (information: {p['whole']} us)")
    if (engine, FW[engine]) in KNOWN_OVER:
        assert p["whole"] > T["target_us"]
        pytest.xfail(KNOWN_OVER[(engine, FW[engine])])
    assert p["whole"] <= T["target_us"], p


def test_known_over_is_current():
    """each KNOWN_OVER entry is the firmware's count and still over: else it is stale (remove it)"""
    for (e, n), why in KNOWN_OVER.items():
        assert FW[e] == n, f"KNOWN_OVER {e} {n}: the firmware has {FW[e]} voices now; remove the entry ({why})"
        assert cb.predict(T, e, n)["whole"] > T["target_us"], f"KNOWN_OVER {e} {n} is within the target: remove it"


def test_predictions():
    """the numbers the release plan quotes (tools/cpu_budget.py prints the whole table)"""
    want = {("rhodes", 8, False): 3373, ("rhodes", 8, True): 3403, ("rhodes", 9, False): 3596,
            ("rhodes", 9, True): 3626, ("rhodes", 10, False): 3819, ("rhodes", 10, True): 3849,
            ("vibes", 9, False): 3991, ("vibes", 9, True): 4021, ("vibes", 10, False): 4258, ("vibes", 10, True): 4288,
            ("marimba", 9, False): 3955, ("marimba", 9, True): 3985, ("marimba", 10, False): 4218,
            ("marimba", 10, True): 4248}
    got = {k: cb.predict(T, k[0], k[1], bend=k[2])["whole"] for k in want}
    # (a device run that updates tools/cpu_costs.json moves these: update them with it; bending: the spread retune;
    # est_costs_us: + 51, the reverb's half-bands, rev-upsample)
    assert got == want, got
