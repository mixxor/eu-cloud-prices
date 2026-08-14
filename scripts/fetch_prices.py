#!/usr/bin/env python3
"""Fetch provider pricing into prices/*.json.

    python scripts/fetch_prices.py [--provider NAME|all] [--dry-run]

A provider that fails is reported and skipped; its existing file is left
untouched and the other providers still run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Direct execution puts this file's dir, not the repo root, on sys.path[0];
# add the repo root so the scripts.* imports below resolve.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import fetchers
from scripts.fetchers import common, fx

PRICES_DIR = Path(__file__).parent.parent / "prices"
FX_STATE = Path(__file__).parent / "fetchers" / "data" / "fx_rate.json"

#: Providers whose upstream prices are USD and therefore need an ECB rate.
USD_PROVIDERS = frozenset({"aws", "gcp"})


def run(providers: list[str], prices_dir: Path, dry_run: bool) -> dict:
    rates = None
    fx_error = None
    try:
        rates = fx.fetch_rates()
    except common.FetchError as exc:
        fx_error = str(exc)

    result: dict = {"ok": [], "failed": {}}

    for name in providers:
        if name in USD_PROVIDERS and rates is None:
            result["failed"][name] = f"no exchange rate available: {fx_error}"
            print(f"[{name}] SKIPPED - {result['failed'][name]}", file=sys.stderr)
            continue

        ctx = common.Context(prices_dir=prices_dir, fx=rates)
        try:
            payload = fetchers.REGISTRY[name](ctx)
            count = len(payload.get("instances", []))
            if dry_run:
                print(f"[{name}] ok (dry run) - {count} instances")
            else:
                path = common.write_price_file(name, payload, prices_dir)
                print(f"[{name}] ok - {count} instances -> {path}")
        except Exception as exc:  # noqa: BLE001 - any provider failure (fetch or write) is isolated
            result["failed"][name] = str(exc)
            print(f"[{name}] FAILED - {exc}", file=sys.stderr)
            continue
        result["ok"].append(name)

    if rates is not None and not dry_run:
        fx.save_last_rate(rates, FX_STATE)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prices-dir", default=str(PRICES_DIR))
    parser.add_argument(
        "--summary-file",
        help="also write the {ok, failed} JSON summary to this path, "
             "e.g. for a CI step to surface failed providers without "
             "scraping it back out of stdout",
    )
    args = parser.parse_args(argv)

    if args.provider == "all":
        providers = sorted(fetchers.REGISTRY)
    elif args.provider in fetchers.REGISTRY:
        providers = [args.provider]
    else:
        parser.error(f"unknown provider {args.provider!r}; choose from {sorted(fetchers.REGISTRY)} or 'all'")

    result = run(providers, Path(args.prices_dir), args.dry_run)

    summary = json.dumps(result, indent=2)
    print("\n" + summary)
    if args.summary_file:
        Path(args.summary_file).write_text(summary + "\n")
    # Non-zero only when nothing at all succeeded.
    return 1 if not result["ok"] else 0


if __name__ == "__main__":
    sys.exit(main())
