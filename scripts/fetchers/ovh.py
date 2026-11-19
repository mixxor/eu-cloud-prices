"""OVHcloud — public order catalogs (no authentication).

Two catalogs feed one price file: Public Cloud flavors (``/cloud``) and the VPS
range (``/vps``). Prices are in micro-units: 1_040_000 renders as "0.01 EUR".
The cloud catalog has no hardware specs, so they come from
data/ovh_flavors.json; the VPS catalog carries its own.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import common
from .common import HOURS_PER_MONTH

SUBSIDIARY = "DE"
BASE_URL = f"https://api.ovh.com/v1/order/catalog/public/cloud?ovhSubsidiary={SUBSIDIARY}"
VPS_URL = f"https://api.ovh.com/v1/order/catalog/public/vps?ovhSubsidiary={SUBSIDIARY}"
MICRO_UNITS = 100_000_000
SPECS_PATH = Path(__file__).parent / "data" / "ovh_flavors.json"

#: The VPS catalog keeps every generation it ever sold (VPS 2018, Starter,
#: Value, VPS 2026, ...). OVH tags the plans its website currently sells with
#: "website:show"; nothing else is orderable as a new service.
VPS_ON_SALE_TAG = "website:show"

#: Local-zone VPS (".LZ") are a separate, differently priced product on other
#: hardware, like the ".LZ." variants of the cloud flavors.
VPS_LOCAL_ZONE_SUFFIX = ".LZ"

#: The catalog leaves the CPU brand blank. OVH states that its datacentre VPS
#: run on Intel and that AMD EPYC is local-zone only
#: (https://www.ovhcloud.com/en/vps/vps-amd/), so the plans kept here are x86.
VPS_ARCHITECTURE = "x86"

NOTES = (
    "Public Cloud flavors: hourly consumption rate x 730. "
    "VPS (category 'vps'): the range OVH currently sells, from the VPS order catalog, "
    "at the monthly price without commitment; ovhcloud.com advertises the cheaper "
    "12-month commitment price as 'from'. VPS are billed monthly, so they carry no "
    "hourly price; one IPv4 is included. VPS are not Public Cloud instances and "
    "cannot be Managed Kubernetes Service worker nodes. "
    "All prices excl. VAT: both catalogs list price and tax separately."
)

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


def _vps_monthly_price(plan: dict) -> float | None:
    """Monthly renewal price without commitment, or None if it is not unambiguous.

    A plan lists the same service under several modes: "default" (monthly, no
    commitment) and "upfront6" / "upfront12" (cheaper, committed). The dataset
    records the shortest term, so only the "default" row counts.
    """
    rows = [
        p for p in plan.get("pricings", [])
        if p.get("mode") == "default"
        and p.get("commitment") == 0
        and "renew" in p.get("capacities", [])
        and p.get("intervalUnit") == "month"
        and p.get("interval") == 1
    ]
    if len(rows) != 1 or not rows[0].get("price"):
        return None
    return rows[0]["price"] / MICRO_UNITS


def _vps_disk_type(disk: dict) -> str | None:
    if disk.get("interface") == "NVMe":
        return "nvme"
    if disk.get("technology") == "SSD":
        return "ssd"
    return None


def parse_vps_plans(catalog: dict) -> tuple[list[dict], list[str]]:
    """Instances for the VPS plans OVH currently sells, plus the ones it could not read.

    Specs come from the catalog's own product sheet. A plan that is on sale but
    lacks a spec or an unambiguous monthly price is reported, never guessed.
    """
    products = {p.get("name"): p for p in catalog.get("products", [])}
    instances = []
    unknown = []

    for plan in catalog.get("plans", []):
        plan_code = plan.get("planCode", "")
        tags = (plan.get("blobs") or {}).get("tags") or []

        if VPS_ON_SALE_TAG not in tags or plan_code.endswith(VPS_LOCAL_ZONE_SUFFIX):
            continue

        product = products.get(plan.get("product")) or {}
        technical = (product.get("blobs") or {}).get("technical") or {}
        vcpu = (technical.get("cpu") or {}).get("cores")
        ram_gb = (technical.get("memory") or {}).get("size")
        disks = (technical.get("storage") or {}).get("disks") or []
        disk_gb = disks[0].get("capacity") if len(disks) == 1 else None
        monthly = _vps_monthly_price(plan)

        if not all(isinstance(v, (int, float)) and v > 0 for v in (vcpu, ram_gb, disk_gb, monthly)):
            unknown.append(plan_code)
            continue

        instance = {
            "id": plan_code,
            "name": plan.get("invoiceName") or plan_code,
            "vcpu": vcpu,
            "ram_gb": ram_gb,
            "disk_gb": disk_gb,
            "price_monthly": round(monthly, 2),
            "category": "vps",
            "disk_type": _vps_disk_type(disks[0]),
            "price_hourly": None,
            "currency": "EUR",
            "architecture": VPS_ARCHITECTURE,
            "location": "eu",
            "range": "VPS",
        }
        if instance["disk_type"] is None:
            del instance["disk_type"]
        instances.append(instance)

    return sorted(instances, key=lambda i: i["id"]), sorted(unknown)


def fetch(ctx: common.Context) -> dict:
    data = ctx.http_get_json(BASE_URL)
    instances, unknown = parse_addons(data.get("addons", []), load_specs())

    if not instances:
        raise common.FetchError("no publiccloud-instance addons matched")

    vps_instances, vps_unknown = parse_vps_plans(ctx.http_get_json(VPS_URL))

    # A catalog change that hides every VPS plan must fail the run, not
    # quietly publish a file without the VPS range.
    if not vps_instances:
        raise common.FetchError(f"no readable VPS plan tagged {VPS_ON_SALE_TAG!r} in the VPS catalog")

    payload = {
        "provider": "ovh",
        "fetched_at": common.now_iso(),
        "source": BASE_URL,
        "manual": False,
        "vat_included": False,
        "instances": instances + vps_instances,
        # Always set, even when empty: merge_price_file's shallow overlay
        # would otherwise never clear a stale list from a prior run.
        "unknown_plan_codes": sorted(unknown + vps_unknown),
        "notes": NOTES,
    }
    return payload
