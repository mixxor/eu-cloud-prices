"""AWS EC2 on-demand pricing (EU Frankfurt / eu-central-1)."""

from __future__ import annotations

import re

from . import common
from .common import HOURS_PER_MONTH


def _aws_category(family: str) -> str:
    f = family.lower()
    if f[0] == "t":
        return "burstable"
    if f[0] == "c":
        return "compute"
    if f[0] == "m":
        return "general"
    if f[0] in ("r", "x", "u", "z"):
        return "memory"
    if f[0] in ("i", "d", "h") or f.startswith(("im", "is")):
        return "storage"
    if f[0] in ("g", "p") or f.startswith(("vt", "trn", "inf")):
        return "gpu"
    if f.startswith("hpc"):
        return "hpc"
    return "general"


_AWS_ARM_FAMILIES = {
    "t4g",
    "m6g", "m6gd", "m7g", "m8g",
    "c6g", "c6gn", "c6gd", "c7g", "c8g", "c8gd", "c8gn",
    "r6g", "r6gd", "r7g", "r8g", "r8gd",
    "im4gn", "is4gen", "i8g", "i8ge", "x8g",
    "hpc7g",
}

# c/m/r/t families only, current generation, clean variants (AMD/ARM/Intel
# explicit); no local-disk or network-optimized suffixes.
_AWS_KEEP_VARIANTS_CMR = frozenset({"a", "g", "i", "i-flex"})
_AWS_KEEP_VARIANTS_T   = frozenset({"", "a", "g"})
_AWS_MIN_GEN = {"c": 6, "m": 6, "r": 6, "t": 3}


def _aws_parse_family(family: str):
    """'c8i-flex' → ('c', 8, 'i-flex'),  't4g' → ('t', 4, 'g'),  'c6a' → ('c', 6, 'a')"""
    m = re.match(r'^([cmrt])(\d+)([a-z].*)?$', family)
    if not m:
        return None
    return m.group(1), int(m.group(2)), (m.group(3) or "")


def _aws_keep_families(all_families: set[str]) -> set[str]:
    """All gen >= 6 (or >=3 for t) c/m/r/t families with clean variants only.

    Variants kept: a (AMD), g (Graviton/ARM), i (Intel explicit), i-flex.
    No storage-disk ('d'), no network-optimized ('n'), no specialty suffixes.
    Empty variant allowed only for t-family (t3 = Intel burstable, no explicit suffix).
    """
    kept = set()
    for fam in all_families:
        parsed = _aws_parse_family(fam)
        if parsed is None:
            continue
        letter, gen, variant = parsed
        min_gen = _AWS_MIN_GEN.get(letter)
        if min_gen is None or gen < min_gen:
            continue
        allowed = _AWS_KEEP_VARIANTS_T if letter == "t" else _AWS_KEEP_VARIANTS_CMR
        if variant in allowed:
            kept.add(fam)
    return kept


def _scrape_aws(ctx: common.Context, rate: float) -> dict:
    url = (
        "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current"
        "/ec2-ondemand-without-sec-sel/EU%20(Frankfurt)/Linux/index.json"
    )
    print("  Fetching AWS EC2 (EU Frankfurt) …")
    data = ctx.http_get_json(url)

    # Actual shape: {"regions": {"EU (Frankfurt)": {
    #   "m7i 16xlarge EU Frankfurt Linux": {
    #     "price": "3.864", "Instance Type": "m7i.16xlarge",
    #     "vCPU": "64", "Memory": "256 GiB", "Storage": "EBS only", ...
    #   }
    # }}}
    region_data = data.get("regions", {}).get("EU (Frankfurt)", {})

    # First pass: collect all families present
    all_raw_families = set()
    for info in region_data.values():
        if isinstance(info, dict) and "Instance Type" in info:
            itype = info["Instance Type"]
            if "." in itype:
                all_raw_families.add(itype.split(".")[0])

    keep_families = _aws_keep_families(all_raw_families)
    print(f"  Keeping {len(keep_families)} families: {sorted(keep_families)}")

    instances = []
    for _key, info in region_data.items():
        if not isinstance(info, dict) or "price" not in info:
            continue
        price_usd = float(info["price"])
        if price_usd <= 0:
            continue

        itype = info.get("Instance Type", "")
        if not itype or "." not in itype:
            continue

        family = itype.split(".")[0]
        if family not in keep_families:
            continue

        try:
            vcpu = int(info.get("vCPU", 0))
        except (ValueError, TypeError):
            vcpu = 0

        mem_raw = info.get("Memory", "0 GiB")
        try:
            ram_gb = float(str(mem_raw).split()[0].replace(",", ""))
        except (ValueError, AttributeError):
            ram_gb = 0.0

        arch = "arm64" if family in _AWS_ARM_FAMILIES else "x86"

        instances.append({
            "id": itype,
            "name": itype,
            "vcpu": vcpu,
            "ram_gb": ram_gb,
            "disk_gb": 0,
            "disk_type": "ebs",
            "price_hourly": round(price_usd * rate, 4),
            "price_monthly": round(price_usd * rate * HOURS_PER_MONTH, 2),
            "price_usd_hourly": round(price_usd, 4),
            "currency": "EUR",
            "architecture": arch,
            "location": "eu-central-1",
            "category": _aws_category(family),
            "family": family,
        })

    instances.sort(key=lambda x: (x["family"], x["vcpu"], x["ram_gb"]))
    print(f"  {len(instances)} instances.")

    eks_usd = 73.0  # EKS: $0.10/hr × 730h

    return {
        "provider": "aws",
        "source_url": url,
        "source_region": "EU (Frankfurt)",
        "control_plane_cost": round(eks_usd * rate, 2),
        "instances": instances,
        "load_balancer": {
            "price_monthly": round(16.20 * rate, 2),
            "notes": "Plus LCU charges based on traffic",
        },
        "block_storage": {
            "price_per_gb_monthly": round(0.10 * rate, 4),
            "type": "gp3",
        },
        "egress": {
            "free_tb": 0.1,
            "price_per_gb": round(0.09 * rate, 4),
            "notes": "Tiered pricing: $0.09/GB first 10TB, $0.085 next 40TB, $0.07 next 100TB",
        },
        "object_storage": {
            "price_per_gb_monthly": round(0.023 * rate, 5),
            "type": "S3 Standard",
            "notes": "Tiered: $0.023/GB first 50TB, $0.022 next 450TB, $0.021 over 500TB",
        },
    }


def fetch(ctx: common.Context) -> dict:
    # round before use: full precision shifts the last digit of many prices
    rate = round(ctx.fx.eur_per("USD"), 4)
    payload = _scrape_aws(ctx, rate)
    payload["eur_usd_rate"] = round(rate, 4)  # already rounded; kept for consumers
    payload["fx"] = ctx.fx.block(("USD",))
    payload["manual"] = False
    payload["fetched_at"] = common.now_iso()
    return payload
