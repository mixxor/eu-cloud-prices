"""OVHcloud — public order catalog (no authentication).

Prices are in micro-units: 1_040_000 renders as "0.01 EUR". The catalog has no
hardware specs, so they come from data/ovh_flavors.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import common
from .common import HOURS_PER_MONTH

SUBSIDIARY = "DE"
BASE_URL = f"https://api.ovh.com/v1/order/catalog/public/cloud?ovhSubsidiary={SUBSIDIARY}"
MICRO_UNITS = 100_000_000
SPECS_PATH = Path(__file__).parent / "data" / "ovh_flavors.json"

#: Plan-code suffixes to keep. ".consumption" is the plain hourly rate and
#: ".consumption.3AZ" its three-AZ variant. ".monthly.postpaid" is a different,
#: committed product and exists for only some flavors, so it is excluded.
#: Local-zone variants (.LZ.AF, .LZ.EU, .LZ.EUROZONE) price other regions.
KEEP_SUFFIXES = frozenset({"consumption", "consumption.3AZ"})


def load_specs(path: Path = SPECS_PATH) -> dict:
    return json.loads(path.read_text())


def parse_addons(addons: list[dict], specs: dict) -> tuple[list[dict], list[str]]:
    by_flavor: dict[str, float] = {}
    by_suffix: dict[str, str] = {}  # Track which suffix was used for deduplication
    unknown: set[str] = set()

    for addon in addons:
        if addon.get("product") != "publiccloud-instance":
            continue

        plan_code = addon.get("planCode", "")

        # Matched from the end, not by splitting on the first dot: flavor ids
        # can themselves contain dots (e.g. "metal.eg-256").
        flavor = None
        suffix = None

        for known_suffix in ("consumption.3AZ", "monthly.postpaid",
                             "consumption.LZ.AF", "consumption.LZ.EU", "consumption.LZ.EUROZONE",
                             "consumption"):
            if plan_code.endswith("." + known_suffix):
                flavor = plan_code[: -(len(known_suffix) + 1)]
                suffix = known_suffix
                break

        if suffix not in KEEP_SUFFIXES:
            # only an unrecognised suffix (not a recognised-but-excluded one) counts as unknown
            if suffix is None:
                unknown.add(plan_code)
            continue

        pricings = [p for p in addon.get("pricings", []) if p.get("intervalUnit") == "hour"]
        if not pricings:
            continue
        hourly = pricings[0]["price"] / MICRO_UNITS

        if flavor not in specs:
            unknown.add(flavor)
            continue

        # Prefer the plain ".consumption" rate when both variants are present.
        if flavor not in by_flavor or suffix == "consumption":
            by_flavor[flavor] = hourly
            by_suffix[flavor] = suffix

    instances = []
    for flavor, hourly in sorted(by_flavor.items()):
        spec = specs[flavor]
        instance = {
            "id": flavor,
            "name": spec["name"],
            "vcpu": spec["vcpu"],
            "ram_gb": spec["ram_gb"],
            "disk_gb": spec["disk_gb"],
            "price_monthly": round(hourly * HOURS_PER_MONTH, 2),
            "category": spec["category"],
            "disk_type": spec["disk_type"],
            "price_hourly": round(hourly, 4),
            "currency": "EUR",
            "architecture": spec["architecture"],
            "location": "eu",
        }
        if "gpu" in spec:
            instance["gpu"] = spec["gpu"]
        if "gpu_model" in spec:
            instance["gpu_model"] = spec["gpu_model"]
        instances.append(instance)

    return instances, sorted(unknown)


def fetch(ctx: common.Context) -> dict:
    data = ctx.http_get_json(BASE_URL)
    instances, unknown = parse_addons(data.get("addons", []), load_specs())

    if not instances:
        raise common.FetchError("no publiccloud-instance addons matched")

    payload = {
        "provider": "ovh",
        "fetched_at": common.now_iso(),
        "source": BASE_URL,
        "manual": False,
        "instances": instances,
        # Always set, even when empty: merge_price_file's shallow overlay
        # would otherwise never clear a stale list from a prior run.
        "unknown_plan_codes": unknown,
    }
    return payload
