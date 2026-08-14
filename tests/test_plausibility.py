import json
import subprocess
from pathlib import Path

import pytest

from scripts import plausibility_check as pc


def _file(instances, **extra):
    return {"provider": "p", "fetched_at": "2026-08-13T00:00:00Z", "instances": instances, **extra}


def _hard(findings):
    return [f.code for f in findings if f.level == "hard"]


def _soft(findings):
    return [f.code for f in findings if f.level == "soft"]


def test_empty_instances_is_a_hard_failure():
    findings = pc.compare("p", _file([{"id": "a", "price_monthly": 5.0}]), _file([]))
    assert "empty" in _hard(findings)


def test_count_drop_over_twenty_percent_is_hard():
    old = _file([{"id": str(i), "price_monthly": 5.0} for i in range(100)])
    new = _file([{"id": str(i), "price_monthly": 5.0} for i in range(75)])
    assert "count_drop" in _hard(pc.compare("p", old, new))


def test_count_drop_under_twenty_percent_is_not_hard():
    old = _file([{"id": str(i), "price_monthly": 5.0} for i in range(100)])
    new = _file([{"id": str(i), "price_monthly": 5.0} for i in range(85)])
    assert "count_drop" not in _hard(pc.compare("p", old, new))


@pytest.mark.parametrize("bad", [0, -1.0, None])
def test_non_positive_or_missing_price_is_hard(bad):
    new = _file([{"id": "a", "price_monthly": bad}])
    assert "bad_price" in _hard(pc.compare("p", _file([{"id": "a", "price_monthly": 5.0}]), new))


def test_price_rise_of_fifty_percent_is_hard():
    old = _file([{"id": "a", "price_monthly": 10.0}])
    new = _file([{"id": "a", "price_monthly": 15.0}])
    assert "price_jump" in _hard(pc.compare("p", old, new))


def test_price_drop_of_fifty_percent_is_hard():
    old = _file([{"id": "a", "price_monthly": 10.0}])
    new = _file([{"id": "a", "price_monthly": 5.0}])
    assert "price_jump" in _hard(pc.compare("p", old, new))


def test_price_move_just_inside_the_threshold_is_not_hard():
    old = _file([{"id": "a", "price_monthly": 10.0}])
    assert "price_jump" not in _hard(pc.compare("p", old, _file([{"id": "a", "price_monthly": 14.9}])))
    assert "price_jump" not in _hard(pc.compare("p", old, _file([{"id": "a", "price_monthly": 5.1}])))


def test_price_jump_is_measured_net_of_fx():
    """Same USD price, EUR moved only because the rate moved: not a jump.

    The raw EUR ratio here (40/100 = 0.40) is itself below the 0.5 drop
    bound, so this fixture actively exercises FX netting rather than
    happening to land inside the threshold either way: with the fx blocks
    present, netting must pull the ratio back to 1.0 and suppress the
    finding; without them (see the round-trip check in the fix report),
    the same raw prices trip price_jump.
    """
    old = _file([{"id": "a", "price_monthly": 100.0}],
                fx={"base": "EUR", "rate_date": "2026-01-01", "rates": {"USD": 1.0}})
    new = _file([{"id": "a", "price_monthly": 40.0}],
                fx={"base": "EUR", "rate_date": "2026-08-13", "rates": {"USD": 2.5}})
    # 100 USD on both sides; the raw EUR ratio is 0.40, which WOULD trip the drop bound
    assert "price_jump" not in _hard(pc.compare("p", old, new))


def test_price_jump_without_fx_blocks_falls_back_to_raw_eur():
    old = _file([{"id": "a", "price_monthly": 10.0}])
    new = _file([{"id": "a", "price_monthly": 20.0}])
    findings = pc.compare("p", old, new)
    assert "price_jump" in _hard(findings)


def test_currency_change_is_hard():
    old = _file([{"id": "a", "price_monthly": 5.0, "currency": "USD"}])
    new = _file([{"id": "a", "price_monthly": 5.0, "currency": "EUR"}])
    assert "currency_change" in _hard(pc.compare("p", old, new))


def test_spec_drift_on_an_existing_id_is_soft():
    old = _file([{"id": "a", "price_monthly": 5.0, "vcpu": 2, "ram_gb": 4}])
    new = _file([{"id": "a", "price_monthly": 5.0, "vcpu": 4, "ram_gb": 4}])
    findings = pc.compare("p", old, new)
    assert "spec_drift" in _soft(findings)
    assert _hard(findings) == []


def test_added_and_removed_ids_are_soft():
    old = _file([{"id": "a", "price_monthly": 5.0}, {"id": "b", "price_monthly": 5.0}])
    new = _file([{"id": "a", "price_monthly": 5.0}, {"id": "c", "price_monthly": 5.0}])
    assert "ids_changed" in _soft(pc.compare("p", old, new))


def test_median_move_over_twenty_five_percent_is_soft():
    old = _file([{"id": str(i), "price_monthly": 10.0} for i in range(10)])
    new = _file([{"id": str(i), "price_monthly": 13.0} for i in range(10)])
    assert "median_move" in _soft(pc.compare("p", old, new))


def test_fetched_at_only_difference_is_detected():
    old = _file([{"id": "a", "price_monthly": 5.0}])
    new = {**old, "fetched_at": "2026-09-01T00:00:00Z"}
    assert pc.is_fetched_at_only(old, new) is True


def test_a_real_change_is_not_fetched_at_only():
    old = _file([{"id": "a", "price_monthly": 5.0}])
    new = _file([{"id": "a", "price_monthly": 6.0}], fetched_at="2026-09-01T00:00:00Z")
    assert pc.is_fetched_at_only(old, new) is False


def test_report_lists_offending_ids_with_both_values():
    old = _file([{"id": "big-one", "price_monthly": 10.0}])
    new = _file([{"id": "big-one", "price_monthly": 30.0}])
    report = pc.render_report(pc.compare("p", old, new), reverted=[], fx_notes=[])
    assert "big-one" in report
    assert "10.0" in report and "30.0" in report


# --- fix round 1: C1, the median block must not crash on a bad price -------
#
# statistics.median sorts its input, so subscripting price_monthly straight
# from the instance dict (rather than going through the same isinstance/<=0
# guard the per-instance jump loop already uses) blows up on the first
# null, missing, or non-numeric value once there are 3+ shared ids. Because
# render_report and the --report write both happen after compare() returns,
# an uncaught exception here means no report is produced for ANY provider in
# the run, not just the one with the bad price.


def test_median_block_does_not_crash_on_a_null_price():
    ids = ["a", "b", "c"]
    old = _file([{"id": i, "price_monthly": 10.0} for i in ids])
    new_instances = [{"id": i, "price_monthly": 10.0} for i in ids]
    new_instances[0]["price_monthly"] = None
    findings = pc.compare("p", old, _file(new_instances))  # must not raise
    assert isinstance(findings, list)
    assert "bad_price" in _hard(findings)


def test_median_block_does_not_crash_on_a_missing_price_key():
    ids = ["a", "b", "c"]
    old = _file([{"id": i, "price_monthly": 10.0} for i in ids])
    new = _file([{"id": "a"}, {"id": "b", "price_monthly": 10.0}, {"id": "c", "price_monthly": 10.0}])
    findings = pc.compare("p", old, new)  # must not raise
    assert isinstance(findings, list)
    assert "bad_price" in _hard(findings)


def test_median_block_does_not_crash_on_a_non_numeric_price():
    ids = ["a", "b", "c"]
    old = _file([{"id": i, "price_monthly": 10.0} for i in ids])
    new_instances = [{"id": i, "price_monthly": 10.0} for i in ids]
    new_instances[0]["price_monthly"] = "10.0"
    findings = pc.compare("p", old, _file(new_instances))  # must not raise
    assert isinstance(findings, list)
    assert "bad_price" in _hard(findings)


# --- fix round 1: M6, cover the unknown_plan_codes aggregation deviation ---


def test_unknown_plan_codes_under_the_summary_threshold_get_one_finding_each():
    old = _file([{"id": "a", "price_monthly": 5.0}])
    new = _file([{"id": "a", "price_monthly": 5.0}], unknown_plan_codes=["c1", "c2", "c3", "c4"])
    findings = [f for f in pc.compare("p", old, new) if f.code == "unknown_plan_code"]
    assert len(findings) == 4
    assert all(f.level == "soft" for f in findings)


def test_unknown_plan_codes_at_the_summary_threshold_collapse_to_one_finding():
    codes = [f"c{i}" for i in range(12)]
    old = _file([{"id": "a", "price_monthly": 5.0}])
    new = _file([{"id": "a", "price_monthly": 5.0}], unknown_plan_codes=codes)
    findings = [f for f in pc.compare("p", old, new) if f.code == "unknown_plan_code"]
    assert len(findings) == 1
    assert findings[0].level == "soft"
    assert "12" in findings[0].message


# --- fix round 2: NEW-2, price_hourly must not double-count price_jump ----
#
# Every fetcher derives price_monthly from price_hourly (or vice versa) from
# the same source number, so a real jump trips both at once. One row per
# field for the same underlying move doubles report size for every provider
# where "every price moved" - and the doubled report is what can exceed
# GitHub's 65,536-char issue body limit on exactly the run where filing the
# issue matters most.


def test_price_jump_on_both_fields_yields_one_finding_per_instance():
    old = _file([{"id": "a", "price_monthly": 10.0, "price_hourly": 0.0137}])
    new = _file([{"id": "a", "price_monthly": 30.0, "price_hourly": 0.0411}])
    findings = [f for f in pc.compare("p", old, new) if f.code == "price_jump"]
    assert len(findings) == 1


# --- fix round 2: I6 tests, price_hourly was previously entirely untested -


def test_hourly_only_jump_fires_price_jump():
    """price_monthly is unchanged; only price_hourly moves past the bound."""
    old = _file([{"id": "a", "price_monthly": 10.0, "price_hourly": 0.0137}])
    new = _file([{"id": "a", "price_monthly": 10.0, "price_hourly": 0.05}])
    findings = [f for f in pc.compare("p", old, new) if f.code == "price_jump"]
    assert len(findings) == 1
    assert "price_hourly" in findings[0].message


def test_hourly_bad_price_is_hard_and_names_the_field():
    old = _file([{"id": "a", "price_monthly": 10.0, "price_hourly": 0.0137}])
    new = _file([{"id": "a", "price_monthly": 10.0, "price_hourly": 0.0}])
    bad = [f for f in pc.compare("p", old, new) if f.code == "bad_price"]
    assert len(bad) == 1
    assert "price_hourly" in bad[0].message


def test_missing_price_hourly_on_both_sides_is_silently_skipped():
    old = _file([{"id": "a", "price_monthly": 10.0}])
    new = _file([{"id": "a", "price_monthly": 10.0}])
    assert pc.compare("p", old, new) == []


def test_price_hourly_disappearing_is_a_soft_flag():
    """Present on the old side, absent on the new: a fetcher regression,
    not silence."""
    old = _file([{"id": "a", "price_monthly": 10.0, "price_hourly": 0.0137}])
    new = _file([{"id": "a", "price_monthly": 10.0}])
    findings = pc.compare("p", old, new)
    assert "price_hourly_dropped" in _soft(findings)
    assert _hard(findings) == []


# --- fix round 2: NEW-3, median must be computed over one shared population


def test_median_is_computed_over_ids_usable_on_both_sides():
    """Regression: filtering old_values and new_values independently could
    pair unrelated instances into a fabricated median_move even when the
    ids valid on both sides didn't move at all. Reproduces the reviewer's
    fixture: 5 cheap + 5 expensive ids, identical on both sides, except the
    old file has null on the 5 cheap ones. Before the fix this reported
    "median price moved 49.5% (1000.00 -> 505.00)" with zero hard findings
    to explain it - a fabricated number.
    """
    cheap_ids = [f"cheap{i}" for i in range(5)]
    expensive_ids = [f"exp{i}" for i in range(5)]
    old = _file(
        [{"id": i, "price_monthly": None} for i in cheap_ids]
        + [{"id": i, "price_monthly": 1000.0} for i in expensive_ids]
    )
    new = _file(
        [{"id": i, "price_monthly": 10.0} for i in cheap_ids]
        + [{"id": i, "price_monthly": 1000.0} for i in expensive_ids]
    )
    findings = pc.compare("p", old, new)
    assert "median_move" not in _soft(findings)


# --- fix round 2: NEW-1 and the checkout-HEAD fix, exercised through main()
#
# Nothing above calls main(): compare/is_fetched_at_only/render_report are
# pure functions, but all the git plumbing (repo-root resolution, the
# staged-vs-unstaged diff, the deletion handling, the fetched_at-only
# revert) lives only in main() and _head_version, and was previously
# covered only by manual smoke tests. These build a real throwaway git repo
# under pytest's tmp_path and call pc.main() directly.


def _init_repo(tmp_path: Path) -> Path:
    prices = tmp_path / "prices"
    prices.mkdir()
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-13T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 10.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "smoke@test.local"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Smoke Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=tmp_path, check=True)
    return prices


def test_main_catches_a_fully_staged_price_jump(tmp_path):
    """I4 (fix round 1): `git add`-ing a bad change must not bypass the gate."""
    prices = _init_repo(tmp_path)
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 100.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    assert pc.main(["--prices-dir", str(prices)]) == 1


def test_main_works_when_the_process_cwd_is_a_different_repo(tmp_path):
    """NEW-1: the ambient process CWD here (wherever pytest was invoked
    from) is a real git repo unrelated to the throwaway one under
    tmp_path - exactly the "wrong CWD" / "absolute --prices-dir" failure
    mode the reviewer reproduced. Before the fix, resolving `git diff`'s
    repo-root-relative paths against the ambient CWD instead of the repo
    actually being gated made every file "not path.exists()" and silently
    skipped, i.e. a 100x staged price rise reported "No anomalies
    detected" and exit 0.
    """
    assert not str(Path.cwd()).startswith(str(tmp_path))  # genuinely a different repo
    prices = _init_repo(tmp_path)
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 100.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    assert pc.main(["--prices-dir", str(prices)]) == 1


def test_main_flags_a_deleted_price_file_as_hard(tmp_path):
    """NEW-1: deleting a whole provider's prices must not sail through clean."""
    prices = _init_repo(tmp_path)
    (prices / "alpha.json").unlink()

    assert pc.main(["--prices-dir", str(prices)]) == 1


def test_main_reverts_a_staged_fetched_at_only_change(tmp_path):
    """Fix round 1's additional find: `git checkout -- path` (no tree-ish)
    restores from the INDEX, not HEAD, so a fully-staged fetched_at-only
    change was reported "Reverted" while silently staying staged. Confirms
    both the working tree AND the index end up clean.
    """
    prices = _init_repo(tmp_path)
    data = json.loads((prices / "alpha.json").read_text())
    data["fetched_at"] = "2026-09-01T00:00:00Z"
    (prices / "alpha.json").write_text(json.dumps(data))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    assert pc.main(["--prices-dir", str(prices)]) == 0

    unstaged = subprocess.run(
        ["git", "diff", "--", "prices/alpha.json"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout
    staged = subprocess.run(
        ["git", "diff", "--cached", "--", "prices/alpha.json"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout
    assert unstaged == "" and staged == ""


# --- fix round 3: findings 1, the report needs a hard size budget ---------


def test_render_report_stays_under_the_size_cap_and_says_so():
    """Several hundred soft findings, long enough that naive concatenation
    would exceed MAX_REPORT_CHARS - reproduces the scale of "an FX bug hits
    every provider at once", the exact scenario this gate exists for."""
    findings = [
        pc.Finding("provX", "soft", "ids_changed",
                   f"instance-{i:04d}: some fairly long descriptive message about what changed here")
        for i in range(2000)
    ]
    report = pc.render_report(findings, reverted=[], fx_notes=[])
    assert len(report) <= pc.MAX_REPORT_CHARS + 500  # small slack for the note line itself
    assert "omitted" in report
    assert "report truncated" in report


def test_render_report_truncates_hard_findings_when_they_alone_exceed_the_cap():
    """Fix round 4: "hard findings are never truncated" was the wrong
    instruction. An over-limit issue body doesn't file a big issue, it
    files NO issue at all (`gh issue create --body-file` 422s) - total
    silence in exactly the scenario (an incident hitting every provider at
    once) this gate exists to escalate. MAX_REPORT_CHARS must be an
    unconditional ceiling, truncating hard findings too if they alone
    exceed it - but the true counts and the BLOCKED verdict must survive
    in the header regardless of how many individual rows get cut."""
    findings = [
        pc.Finding("provX", "hard", "price_jump", f"instance-{i:04d}: some fairly long descriptive message")
        for i in range(2000)
    ]
    report = pc.render_report(findings, reverted=[], fx_notes=[])
    assert len(report) <= pc.MAX_REPORT_CHARS + 500
    assert "2000 hard failure(s)" in report  # true total, not the rendered subset
    assert "omitted" in report
    assert "BLOCKED" in report


# --- fix round 3: finding 3, price_hourly_dropped must be bounded and -----
# must ignore garbage old values ---------------------------------------


@pytest.mark.parametrize("garbage_old_hourly", [0, -1, False, "0.01"])
def test_price_hourly_dropped_ignores_a_garbage_old_value(garbage_old_hourly):
    old = _file([{"id": "a", "price_monthly": 10.0, "price_hourly": garbage_old_hourly}])
    new = _file([{"id": "a", "price_monthly": 10.0}])
    findings = pc.compare("p", old, new)
    assert "price_hourly_dropped" not in [f.code for f in findings]


def test_price_hourly_dropped_collapses_to_one_finding_per_provider():
    n = 20
    old = _file([{"id": str(i), "price_monthly": 10.0, "price_hourly": 0.01} for i in range(n)])
    new = _file([{"id": str(i), "price_monthly": 10.0} for i in range(n)])
    findings = [f for f in pc.compare("p", old, new) if f.code == "price_hourly_dropped"]
    assert len(findings) == 1
    assert str(n) in findings[0].message


# --- fix round 3: finding 2, a dangling symlink must not crash main() -----


def test_main_flags_a_dangling_symlink_as_hard_instead_of_crashing(tmp_path):
    """git reports a regular-file-to-symlink change as a type change (T),
    included under --diff-filter=d since only D is excluded; Path.exists()
    follows the dangling link and returns False. Must become a hard
    finding, not an uncaught exception that destroys the whole run."""
    prices = _init_repo(tmp_path)
    (prices / "alpha.json").unlink()
    (prices / "alpha.json").symlink_to(tmp_path / "nonexistent-target.json")

    assert pc.main(["--prices-dir", str(prices)]) == 1


# --- fix round 3: finding 4, a pure rename must not vanish -----------------


def test_main_flags_a_renamed_price_file_as_hard(tmp_path):
    """`git mv` must not disappear from every query this script runs: not
    the deletion query (git may not call it a deletion), not the changed
    query (the new path never existed at HEAD, so _head_version returns {}
    and it's silently skipped) - ten instances gone with "No anomalies
    detected", exit 0, unless renames are queried explicitly."""
    prices = _init_repo(tmp_path)
    subprocess.run(["git", "mv", "prices/alpha.json", "prices/alpha-renamed.json"], cwd=tmp_path, check=True)

    assert pc.main(["--prices-dir", str(prices)]) == 1


# --- fix round 3: finding 5, one bad file must never suppress the report --


def test_main_survives_a_bad_instance_and_still_reports_other_providers(tmp_path):
    """beta's instances list contains a non-dict entry, reaching the
    unguarded `i["id"]` inside compare()'s dict comprehension. alpha has an
    unrelated, genuine price jump in the same run. Both must survive:
    beta's crash must not suppress alpha's finding, and the run must still
    produce a report instead of an uncaught exception."""
    prices = _init_repo(tmp_path)
    (prices / "beta.json").write_text(json.dumps({
        "provider": "beta", "fetched_at": "2026-08-13T00:00:00Z",
        "instances": [{"id": "y", "price_monthly": 20.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add beta"], cwd=tmp_path, check=True)

    (prices / "beta.json").write_text(json.dumps({
        "provider": "beta", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": ["not-a-dict"],
    }))
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 999.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    report_path = tmp_path / "report.md"
    exit_code = pc.main(["--prices-dir", str(prices), "--report", str(report_path)])

    assert exit_code == 1
    report = report_path.read_text()
    assert "price_jump" in report and "999.0" in report  # alpha's real finding survived
    assert "error" in report  # beta's crash was caught, not fatal


def test_main_flags_malformed_json_as_hard_instead_of_crashing(tmp_path):
    """Regression guard for the single most likely real input: a truncated
    fetch. Works today via the try/except around json.loads; this locks it
    in at the main() level rather than only via compare()'s unit tests."""
    prices = _init_repo(tmp_path)
    (prices / "alpha.json").write_text('{"provider": "alpha", "instances": [')
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    assert pc.main(["--prices-dir", str(prices)]) == 1


# --- per-provider PR restructure: --provider and --json-out ---------------
#
# The workflow now opens one PR per provider and must be able to gate (and
# get a machine-readable verdict for) a single provider's file without the
# other four providers' findings leaking into its exit code or report, plus
# a JSON verdict file it can loop over instead of re-parsing the markdown.


def _init_two_provider_repo(tmp_path: Path) -> Path:
    """Like ``_init_repo``, but alpha and beta are both committed as the
    baseline before either is dirtied - unlike bolting beta on after alpha
    is already dirtied, which would sweep alpha's uncommitted change into
    the "baseline" commit via ``git add -A`` and hide it from every
    comparison that follows.
    """
    prices = _init_repo(tmp_path)
    (prices / "beta.json").write_text(json.dumps({
        "provider": "beta", "fetched_at": "2026-08-13T00:00:00Z",
        "instances": [{"id": "b", "price_monthly": 5.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add beta"], cwd=tmp_path, check=True)
    return prices


def test_provider_flag_restricts_the_verdict_to_one_file(tmp_path):
    """alpha gets a real (hard) price jump; beta's price is untouched. Gating
    with --provider alpha must fail; gating with --provider beta must pass,
    even though alpha's file is still sitting there broken."""
    prices = _init_two_provider_repo(tmp_path)
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 100.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    assert pc.main(["--prices-dir", str(prices), "--provider", "beta"]) == 0
    assert pc.main(["--prices-dir", str(prices), "--provider", "alpha"]) == 1
    # No --provider at all still sees both, so the beta-only pass above
    # wasn't just alpha being clean.
    assert pc.main(["--prices-dir", str(prices)]) == 1


def test_provider_flag_excludes_other_providers_findings_from_the_report(tmp_path):
    prices = _init_two_provider_repo(tmp_path)
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 100.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    report_path = tmp_path / "report.md"
    pc.main(["--prices-dir", str(prices), "--provider", "beta", "--report", str(report_path)])
    report = report_path.read_text()
    assert "alpha" not in report
    assert "No anomalies detected" in report


def test_json_out_is_written_on_a_clean_run_with_no_changes(tmp_path):
    """'Write it even when the run is clean' - including the degenerate case
    where nothing changed at all, so the verdicts dict is empty."""
    prices = _init_repo(tmp_path)
    json_out = tmp_path / "verdicts.json"

    assert pc.main(["--prices-dir", str(prices), "--json-out", str(json_out)]) == 0
    assert json.loads(json_out.read_text()) == {}


def test_json_out_includes_a_provider_with_zero_findings(tmp_path):
    """A file can genuinely change (not a fetched_at-only no-op) without
    tripping any check - e.g. a field compare() doesn't look at. That
    provider must still show up in --json-out with hard=0, soft=0,
    blocked=false, not be silently absent.
    """
    prices = _init_repo(tmp_path)
    data = json.loads((prices / "alpha.json").read_text())
    data["fetched_at"] = "2026-08-14T00:00:00Z"
    data["instances"][0]["location"] = "us"  # not checked by compare() at all
    (prices / "alpha.json").write_text(json.dumps(data))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    json_out = tmp_path / "verdicts.json"
    assert pc.main(["--prices-dir", str(prices), "--json-out", str(json_out)]) == 0

    verdicts = json.loads(json_out.read_text())
    assert verdicts == {"alpha": {"hard": 0, "soft": 0, "blocked": False}}


def test_json_out_is_written_on_a_blocked_run(tmp_path):
    prices = _init_two_provider_repo(tmp_path)
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 100.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)
    # beta gets a real, non-fetched_at-only change that trips no check, so
    # it's "examined" with zero findings rather than absent or reverted.
    beta_data = json.loads((prices / "beta.json").read_text())
    beta_data["instances"][0]["location"] = "us"
    (prices / "beta.json").write_text(json.dumps(beta_data))
    subprocess.run(["git", "add", "prices/beta.json"], cwd=tmp_path, check=True)

    json_out = tmp_path / "verdicts.json"
    assert pc.main(["--prices-dir", str(prices), "--json-out", str(json_out)]) == 1

    verdicts = json.loads(json_out.read_text())
    assert verdicts["alpha"]["hard"] >= 1
    assert verdicts["alpha"]["blocked"] is True
    assert verdicts["beta"] == {"hard": 0, "soft": 0, "blocked": False}


def test_json_out_and_provider_flag_combine_to_one_entry(tmp_path):
    prices = _init_two_provider_repo(tmp_path)
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 100.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)
    beta_data = json.loads((prices / "beta.json").read_text())
    beta_data["instances"][0]["location"] = "us"
    (prices / "beta.json").write_text(json.dumps(beta_data))
    subprocess.run(["git", "add", "prices/beta.json"], cwd=tmp_path, check=True)

    json_out = tmp_path / "verdicts.json"
    pc.main(["--prices-dir", str(prices), "--provider", "beta", "--json-out", str(json_out)])

    assert json.loads(json_out.read_text()) == {"beta": {"hard": 0, "soft": 0, "blocked": False}}


def test_default_behaviour_without_the_new_flags_is_unchanged(tmp_path):
    """Regression guard: introducing --provider/--json-out must not alter
    the existing no-flags exit code or report for a run that never sets
    them, e.g. by a stray verdicts computation running unconditionally."""
    prices = _init_repo(tmp_path)
    (prices / "alpha.json").write_text(json.dumps({
        "provider": "alpha", "fetched_at": "2026-08-14T00:00:00Z",
        "instances": [{"id": "x", "price_monthly": 100.0, "currency": "EUR"}],
    }))
    subprocess.run(["git", "add", "prices/alpha.json"], cwd=tmp_path, check=True)

    assert pc.main(["--prices-dir", str(prices)]) == 1
