#!/usr/bin/env python3
"""Gate freshly fetched price files against the versions committed at HEAD.

Exit code 0 = clean or soft flags only, 1 = at least one hard failure.
A hard failure means no PR is opened; the workflow files an issue instead.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PRICES_DIR = Path(__file__).parent.parent / "prices"

MAX_COUNT_DROP_PCT = 20
PRICE_JUMP_FACTOR = 1.5
PRICE_DROP_FACTOR = 0.5
MAX_MEDIAN_MOVE_PCT = 25

#: Unconditional ceiling on the rendered report (GitHub's issue/PR body limit is 65,536 chars).
MAX_REPORT_CHARS = 60_000
#: Slack reserved so the truncation note itself never pushes the report over MAX_REPORT_CHARS.
_TRUNCATION_NOTE_RESERVE = 400

#: Per-provider threshold overrides for legitimately volatile providers.
OVERRIDES: dict[str, dict] = {}

#: Above this many unknown-plan-code findings for one provider, collapse them
#: into a single summary finding instead of one row per code.
UNKNOWN_PLAN_CODE_SUMMARY_THRESHOLD = 5


@dataclass
class Finding:
    provider: str
    level: str   # "hard" | "soft"
    code: str
    message: str


def _threshold(provider: str, name: str, default):
    return OVERRIDES.get(provider, {}).get(name, default)


def _usd_rate(payload: dict) -> float | None:
    return (payload.get("fx") or {}).get("rates", {}).get("USD")


def _normalise(price: float, rate: float | None) -> float:
    """Express an EUR price in source-currency terms when a rate is known."""
    # rate is USD-per-EUR, so recovering the source price is a multiply, not a divide.
    return price * rate if rate else price


def _usable_price(inst: dict, field: str, rate: float | None) -> float | None:
    """Return a normalised positive numeric price for ``field``, or None.

    Bools are rejected despite ``isinstance(True, int)`` being true in Python.
    """
    price = inst.get(field)
    if price is None or isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
        return None
    return _normalise(price, rate)


def is_fetched_at_only(old: dict, new: dict) -> bool:
    return {k: v for k, v in old.items() if k != "fetched_at"} == \
           {k: v for k, v in new.items() if k != "fetched_at"}


def compare(name: str, old: dict, new: dict) -> list[Finding]:
    findings: list[Finding] = []
    old_instances = {i["id"]: i for i in old.get("instances", [])}
    new_instances = {i["id"]: i for i in new.get("instances", [])}

    if not new_instances:
        findings.append(Finding(name, "hard", "empty", "new file has zero instances"))
        return findings

    # instance count
    if old_instances:
        drop_pct = (len(old_instances) - len(new_instances)) / len(old_instances) * 100
        limit = _threshold(name, "MAX_COUNT_DROP_PCT", MAX_COUNT_DROP_PCT)
        if drop_pct > limit:
            findings.append(Finding(
                name, "hard", "count_drop",
                f"instance count fell {drop_pct:.1f}% ({len(old_instances)} -> {len(new_instances)}), limit {limit}%",
            ))

    # price sanity: price_monthly is mandatory; price_hourly is checked the
    # same way but may be absent.
    for iid, inst in sorted(new_instances.items()):
        for field in ("price_monthly", "price_hourly"):
            price = inst.get(field)
            if field == "price_hourly" and price is None:
                continue
            if (
                price is None
                or isinstance(price, bool)
                or not isinstance(price, (int, float))
                or price <= 0
            ):
                findings.append(Finding(name, "hard", "bad_price", f"{iid}: {field} is {price!r}"))

    # currency
    old_currencies = {i.get("currency") for i in old_instances.values() if i.get("currency")}
    new_currencies = {i.get("currency") for i in new_instances.values() if i.get("currency")}
    if old_currencies and new_currencies and old_currencies != new_currencies:
        findings.append(Finding(
            name, "hard", "currency_change",
            f"currency changed {sorted(old_currencies)} -> {sorted(new_currencies)}",
        ))

    # per-instance price movement, measured net of FX
    old_rate, new_rate = _usd_rate(old), _usd_rate(new)
    fx_aware = old_rate is not None and new_rate is not None
    jump = _threshold(name, "PRICE_JUMP_FACTOR", PRICE_JUMP_FACTOR)
    drop = _threshold(name, "PRICE_DROP_FACTOR", PRICE_DROP_FACTOR)

    def _direction(ratio: float) -> str:
        return "rise" if ratio >= 1 else "drop"

    # ids whose price_hourly was usable at HEAD and is now absent; collapsed
    # into one summary finding after the loop instead of one per id.
    hourly_dropped: list[str] = []

    for iid in sorted(set(old_instances) & set(new_instances)):
        triggered: list[tuple[str, float, float, float]] = []
        for field in ("price_monthly", "price_hourly"):
            old_price = old_instances[iid].get(field)
            new_price = new_instances[iid].get(field)

            if (
                field == "price_hourly"
                and new_price is None
                and _usable_price(old_instances[iid], "price_hourly", None) is not None
            ):
                # only flag a field that was genuinely usable at HEAD and is now gone
                hourly_dropped.append(iid)

            if old_price is None or new_price is None:
                continue  # absent on either side - nothing usable to compare
            if isinstance(old_price, bool) or isinstance(new_price, bool):
                continue
            if not isinstance(old_price, (int, float)) or not isinstance(new_price, (int, float)):
                continue
            if old_price <= 0 or new_price <= 0:
                continue

            a = _normalise(old_price, old_rate if fx_aware else None)
            b = _normalise(new_price, new_rate if fx_aware else None)
            if b >= a * jump or b <= a * drop:
                triggered.append((field, old_price, new_price, b / a))

        if triggered:
            # One finding per instance, not per field: price_monthly and
            # price_hourly are derived from the same source number, so a
            # real jump trips both at once. Only the widest-moving field
            # gets full old -> new values; others are named with direction only.
            triggered.sort(key=lambda t: max(t[3], 1 / t[3]), reverse=True)
            suffix = "" if fx_aware else " (compared without FX netting)"
            widest_field, widest_old, widest_new, widest_ratio = triggered[0]
            if len(triggered) == 1:
                detail = (
                    f"{widest_field} {widest_old} -> {widest_new} "
                    f"({_direction(widest_ratio)}, x{widest_ratio:.2f})"
                )
            else:
                # ratio is FX-netted when fx_aware, so it can disagree with the
                # raw old -> new figures shown alongside it; label it explicitly.
                ratio_note = "FX-netted" if fx_aware else "raw"
                also = ", ".join(f"{f} ({_direction(r)})" for f, _, _, r in triggered[1:])
                detail = (
                    f"{widest_field} {widest_old} -> {widest_new} "
                    f"({_direction(widest_ratio)}, x{widest_ratio:.2f} {ratio_note}; also {also})"
                )
            findings.append(Finding(name, "hard", "price_jump", f"{iid}: {detail}{suffix}"))

    if hourly_dropped:
        shown = ", ".join(hourly_dropped[:10])
        more = f", and {len(hourly_dropped) - 10} more" if len(hourly_dropped) > 10 else ""
        findings.append(Finding(
            name, "soft", "price_hourly_dropped",
            f"{len(hourly_dropped)} instance(s) lost price_hourly (was a usable price at HEAD, now absent): "
            f"{shown}{more}",
        ))

    # whole-provider median move: pairs are built from one pass over ids
    # usable on both sides, not two independently filtered lists - filtering
    # separately can pair unrelated instances into a fabricated median move.
    shared = sorted(set(old_instances) & set(new_instances))
    median_pairs = []
    for i in shared:
        ov = _usable_price(old_instances[i], "price_monthly", old_rate if fx_aware else None)
        nv = _usable_price(new_instances[i], "price_monthly", new_rate if fx_aware else None)
        if ov is not None and nv is not None:
            median_pairs.append((ov, nv))
    if len(median_pairs) >= 3:
        old_values = [p[0] for p in median_pairs]
        new_values = [p[1] for p in median_pairs]
        old_median = statistics.median(old_values)
        new_median = statistics.median(new_values)
        if old_median > 0:
            move = abs(new_median - old_median) / old_median * 100
            if move > _threshold(name, "MAX_MEDIAN_MOVE_PCT", MAX_MEDIAN_MOVE_PCT):
                findings.append(Finding(
                    name, "soft", "median_move",
                    f"median price moved {move:.1f}% ({old_median:.2f} -> {new_median:.2f})",
                ))

    # spec drift on an existing id
    for iid in shared:
        for field in ("vcpu", "ram_gb"):
            before, after = old_instances[iid].get(field), new_instances[iid].get(field)
            if before is not None and after is not None and before != after:
                findings.append(Finding(
                    name, "soft", "spec_drift",
                    f"{iid}: {field} changed {before} -> {after} (possible id reuse)",
                ))

    # id churn
    added = sorted(set(new_instances) - set(old_instances))
    removed = sorted(set(old_instances) - set(new_instances))
    if added or removed:
        findings.append(Finding(
            name, "soft", "ids_changed",
            f"+{len(added)} / -{len(removed)}"
            + (f" added: {', '.join(added[:10])}" if added else "")
            + (f" removed: {', '.join(removed[:10])}" if removed else ""),
        ))

    # unknown OVH plan codes: below threshold, one finding per code; at or
    # above it, collapse into one summary finding so the report stays scannable.
    codes = new.get("unknown_plan_codes", [])
    if len(codes) < UNKNOWN_PLAN_CODE_SUMMARY_THRESHOLD:
        for code in codes:
            findings.append(Finding(name, "soft", "unknown_plan_code", f"no spec entry for {code}"))
    elif codes:
        shown = ", ".join(codes[:10])
        more = f", and {len(codes) - 10} more" if len(codes) > 10 else ""
        findings.append(Finding(
            name, "soft", "unknown_plan_code",
            f"{len(codes)} plan code(s) with no spec entry: {shown}{more} "
            "(full list in the file's unknown_plan_codes)",
        ))

    return findings


def render_report(findings: list[Finding], reverted: list[str], fx_notes: list[str]) -> str:
    hard = [f for f in findings if f.level == "hard"]
    soft = [f for f in findings if f.level == "soft"]

    header = ["## Plausibility check", "", f"**{len(hard)} hard failure(s), {len(soft)} soft flag(s).**", ""]

    # Per-provider counts render unconditionally, never subject to truncation below.
    providers = sorted({f.provider for f in findings})
    if providers:
        for p in providers:
            p_hard = sum(1 for f in hard if f.provider == p)
            p_soft = sum(1 for f in soft if f.provider == p)
            counts = ", ".join(
                part for part in (f"{p_hard} hard" if p_hard else "", f"{p_soft} soft" if p_soft else "") if part
            )
            header.append(f"- `{p}`: {counts}")
        header.append("")

    for note in fx_notes:
        header.append(f"- {note}")
    if fx_notes:
        header.append("")

    if reverted:
        header.append(f"Reverted (only `fetched_at` changed): {', '.join(reverted)}")
        header.append("")

    # MAX_REPORT_CHARS is unconditional below this point: soft findings
    # truncate first, then hard. Counts above are already rendered and unaffected.
    used = len("\n".join(header))
    remaining = MAX_REPORT_CHARS - used - _TRUNCATION_NOTE_RESERVE

    hard_block: list[str] = []
    if hard:
        hard_block = ["### Hard failures — no PR opened", "", "| Provider | Check | Detail |", "|---|---|---|"]
        included = 0
        for f in hard:
            row = f"| {f.provider} | `{f.code}` | {f.message} |"
            if len(row) + 1 > remaining:
                break
            hard_block.append(row)
            remaining -= len(row) + 1
            included += 1
        omitted = len(hard) - included
        if omitted:
            hard_block.append(
                f"⚠️ {omitted:,} of {len(hard):,} hard findings omitted — report truncated at "
                f"{MAX_REPORT_CHARS:,} chars."
            )
            hard_block.append(
                "The full set is in the file diff. This run is BLOCKED; do not merge without reviewing it."
            )
        hard_block.append("")

    soft_block: list[str] = []
    if soft:
        soft_block = ["### Soft flags — review before merging", "", "| Provider | Check | Detail |", "|---|---|---|"]
        included = 0
        for f in soft:
            row = f"| {f.provider} | `{f.code}` | {f.message} |"
            if len(row) + 1 > remaining:
                break
            soft_block.append(row)
            remaining -= len(row) + 1
            included += 1
        omitted = len(soft) - included
        if omitted:
            plural = "s" if omitted != 1 else ""
            soft_block.append(
                f"… {omitted} further finding{plural} omitted "
                f"(report truncated at {MAX_REPORT_CHARS:,} chars); see the file diff for the full set"
            )
        soft_block.append("")

    lines = header + hard_block + soft_block
    if not hard and not soft:
        lines.append("No anomalies detected.")

    return "\n".join(lines) + "\n"


def _head_version(rel: str, root: Path) -> dict:
    """Read ``rel`` (repo-root-relative, as emitted by `git diff`) at HEAD."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return {}
    return json.loads(blob) if blob.strip() else {}


def _git_diff_paths(prices_dir: Path, root: Path, diff_filter: str) -> list[str]:
    """Repo-root-relative paths under ``prices_dir`` changed since HEAD.

    Diffs against HEAD (not just the working tree) to catch staged changes
    too; NUL-delimited to survive filenames with spaces; anchored to
    ``root`` via ``cwd=`` since git always emits repo-root-relative paths.
    """
    raw = subprocess.run(
        ["git", "diff", "--name-only", "-z", f"--diff-filter={diff_filter}", "HEAD", "--", str(prices_dir)],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return [c for c in raw.split("\0") if c]


def _git_renames(prices_dir: Path, root: Path) -> list[tuple[str, str]]:
    """(old_path, new_path) pairs for files renamed under prices_dir since HEAD.

    Uses explicit ``-M`` since `git diff` doesn't detect renames by default
    (unlike `git status`); rename-involved paths are excluded from the
    other diff queries in ``main`` to avoid a duplicate ``deleted`` finding.
    """
    raw = subprocess.run(
        ["git", "diff", "--name-status", "-z", "-M", "--diff-filter=R", "HEAD", "--", str(prices_dir)],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    parts = [p for p in raw.split("\0") if p]
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if status.startswith("R") and i + 2 < len(parts):
            pairs.append((parts[i + 1], parts[i + 2]))
            i += 3
        else:  # defensive; --diff-filter=R should make this unreachable
            i += 1
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices-dir", default=str(PRICES_DIR))
    parser.add_argument("--report", help="also write the markdown report to this path")
    args = parser.parse_args(argv)

    prices_dir = Path(args.prices_dir).resolve()
    findings: list[Finding] = []
    reverted: list[str] = []
    fx_notes: list[str] = []

    root = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=prices_dir, capture_output=True, text=True, check=True,
    ).stdout.strip())

    # "d" (lowercase) excludes deletions; those are handled explicitly below
    # as their own hard finding rather than silently vanishing.
    changed = _git_diff_paths(prices_dir, root, "d")
    deleted = _git_diff_paths(prices_dir, root, "D")
    renamed = _git_renames(prices_dir, root)
    # Renames are reported through their own loop below; exclude their two
    # halves from the plain deleted/changed queries to avoid a duplicate finding.
    renamed_old_paths = {old for old, _new in renamed}
    renamed_new_paths = {new for _old, new in renamed}

    for old_rel, new_rel in renamed:
        path = root / old_rel
        if path.name == "schema.json":
            continue
        name = path.stem
        old = _head_version(old_rel, root)
        if not old:
            continue
        findings.append(Finding(
            name, "hard", "renamed",
            f"{old_rel} -> {new_rel} (price file renamed; HEAD had "
            f"{len(old.get('instances', []))} instances at the old path - verify by hand)",
        ))

    for rel in sorted(deleted):
        if rel in renamed_old_paths:
            continue
        path = root / rel
        if path.name == "schema.json":
            continue
        name = path.stem
        old = _head_version(rel, root)
        if not old:
            continue
        findings.append(Finding(
            name, "hard", "deleted",
            f"{rel} was deleted (HEAD had {len(old.get('instances', []))} instances)",
        ))

    for rel in sorted(changed):
        if rel in renamed_new_paths:
            continue
        path = root / rel
        if path.name == "schema.json":
            continue
        name = path.stem
        if not path.exists():
            # Listed as changed (not a deletion) but missing on disk, e.g. a
            # dangling symlink; still needs a hard finding, not a silent skip.
            findings.append(Finding(
                name, "hard", "missing",
                f"{rel} is listed as changed by git but is missing on disk "
                "(e.g. a dangling symlink) and is not a tracked deletion",
            ))
            continue
        try:
            new = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            # Unguarded, this would propagate out of main() and suppress the
            # report for every other provider in this run, not just this one.
            findings.append(Finding(name, "hard", "unreadable", f"{rel}: failed to parse as JSON: {exc}"))
            continue

        # No single file may take down the whole run's report; everything
        # from here to the end of this file's processing is wrapped once.
        try:
            old = _head_version(rel, root)
            if not old:
                continue

            if is_fetched_at_only(old, new):
                # "HEAD --", not bare "--": a bare checkout restores from
                # the index, which is a no-op if the change is staged.
                subprocess.run(["git", "checkout", "HEAD", "--", rel], cwd=root, check=True)
                reverted.append(name)
                continue

            old_rate, new_rate = _usd_rate(old), _usd_rate(new)
            if old_rate and new_rate and old_rate != new_rate:
                move = (new_rate - old_rate) / old_rate * 100
                fx_notes.append(
                    f"`{name}`: USD rate {old_rate} -> {new_rate} ({move:+.2f}%), "
                    f"{len(new.get('instances', []))} instances repriced"
                )
            elif not (old_rate and new_rate):
                # State the comparison mode explicitly so a reviewer isn't
                # misled into thinking FX was netted out.
                if old_rate is None and new_rate is None:
                    where = "either side"
                elif old_rate is None:
                    where = "the old side"
                else:
                    where = "the new side"
                fx_notes.append(f"`{name}`: no fx block on {where}; price checks ran on raw EUR")

            findings.extend(compare(name, old, new))
        except Exception as exc:  # noqa: BLE001 - one bad file must never suppress the whole run's report
            findings.append(Finding(name, "hard", "error", f"{rel}: {type(exc).__name__}: {exc}"))

    report = render_report(findings, reverted, fx_notes)
    print(report)
    if args.report:
        Path(args.report).write_text(report)

    return 1 if any(f.level == "hard" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
