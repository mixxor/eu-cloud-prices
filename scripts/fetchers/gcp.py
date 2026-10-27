"""GCP Compute Engine on-demand pricing (europe-west1).

Source: Google's public pricing pages on cloud.google.com (no API key). The
old JSON feed (www.gstatic.com/.../gcp-compute.json) was withdrawn in Aug
2026 (issue #36) and the Cloud Billing Catalog API requires a key.

The pricing pages are server-rendered with the tables for *every* region
embedded in the HTML as nested JSON arrays; the region dropdown only switches
between them client-side. Each table is followed by its region label and a
marker, e.g. ``..."Belgium (europe-west1)",[3]]``. So the text between two
labels is the table for the second label. Rows look like::

    [[[1,1,null,[null,[null,"\\u003cp\\u003ec4-standard-2\\u003c/p\\u003e"]]],
      [1,1,null,[null,[null,"\\u003cp\\u003e2\\u003c/p\\u003e"]]],
      [1,1,null,[null,[null,"\\u003cp\\u003e7 GiB\\u003c/p\\u003e"]]],
      [1,1,null,[null,[null,"$0.106659164 / 1 hour"]],1, ...CUD columns...

The 4th column is the on-demand price; tables exist once per hour and once
per month, only the hourly ones are used. Prices are USD and converted with
the ECB rate like aws.py.
"""

from __future__ import annotations

import re

from . import common
from .common import HOURS_PER_MONTH

REGION = "europe-west1"
PAGE_URL = "https://cloud.google.com/products/compute/pricing/{page}?hl=en"
PAGES = ("general-purpose", "compute-optimized", "memory-optimized")

#: A structure change on Google's side should fail loudly, not publish a
#: handful of SKUs. europe-west1 had 363 machine types on 2026-10-27.
MIN_INSTANCES = 100

_LABEL_RE = re.compile(r'"([^"]{2,60}\(([a-z]+-[a-z]+\d)\))",\[(\d+)\]')
_P = r"(?:\\u003cp\\u003e)?"
_PE = r"(?:\\u003c/p\\u003e)?"
_CELL = r"\[1,1,null,\[null,\[null,\""
_ROW_RE = re.compile(
    r"\[\[" + _CELL + _P + r"([a-z0-9]+-[a-z0-9-]+?)" + _PE + r"\"\]\]\],"
    + _CELL + _P + r"([0-9.]+)" + _PE + r"\"\]\]\],"
    + _CELL + _P + r"([0-9.]+) GiB" + _PE + r"\"\]\]\],"
    + _CELL + r"\$([0-9.]+) / 1 hour\""
)

_GCP_CATEGORY: dict[str, str] = {
    "n1": "general", "n2": "general", "n2d": "general", "n4": "general",
    "n4d": "general", "n4a": "general", "e2": "general",
    "c2": "compute", "c2d": "compute", "c3": "compute", "c3d": "compute",
    "c4": "compute", "c4a": "compute", "c4d": "compute", "h3": "compute",
    "m1": "memory", "m2": "memory", "m3": "memory",
    "t2a": "general", "t2d": "general",
    "z3": "storage",
    "f1": "burstable", "g1": "burstable",
}

_GCP_ARM = {"t2a", "c4a", "n4a"}
_GCP_SKIP_FAMILIES = {"a2", "a3", "a4", "g2", "g4"}  # GPU-only
_SHARED_CORE = {"e2-micro", "e2-small", "e2-medium"}


def _category(name: str, family: str) -> str:
    if name in _SHARED_CORE:
        return "burstable"
    if name.endswith("-metal"):
        return "metal"
    if "-highcpu-" in name:
        return "compute"
    if any(f"-{k}" in name for k in ("highmem", "megamem", "ultramem", "hypermem")):
        return "memory"
    return _GCP_CATEGORY.get(family, "general")


def _num(text: str) -> int | float:
    value = float(text)
    return int(value) if value.is_integer() else value


def parse_pricing_html(html: str, region: str = REGION) -> dict[str, dict]:
    """Return {machine_type: {vcpu, ram_gb, usd_hourly}} for ``region``.

    Raises FetchError if the same machine type appears with two different
    on-demand prices in that region, which would mean a column is misread.
    """
    found: dict[str, dict] = {}
    prev_end = 0
    for label in _LABEL_RE.finditer(html):
        block = html[prev_end:label.start()]
        prev_end = label.end()
        if label.group(2) != region:
            continue
        for row in _ROW_RE.finditer(block):
            name, vcpu, ram, usd = row.group(1), row.group(2), row.group(3), float(row.group(4))
            known = found.get(name)
            if known and known["usd_hourly"] != usd:
                raise common.FetchError(
                    f"GCP {name} in {region}: conflicting prices {known['usd_hourly']} vs {usd}"
                )
            found[name] = {"vcpu": _num(vcpu), "ram_gb": _num(ram), "usd_hourly": usd}
    return found


def _scrape_gcp(ctx: common.Context, rate: float) -> dict:
    machines: dict[str, dict] = {}
    for page in PAGES:
        url = PAGE_URL.format(page=page)
        print(f"  Fetching GCP Compute {page} ({REGION}) …")
        machines.update(parse_pricing_html(ctx.http_get_text(url), REGION))

    instances = []
    for name, m in machines.items():
        family = name.split("-")[0]
        if family in _GCP_SKIP_FAMILIES or m["usd_hourly"] <= 0:
            continue
        price_usd = m["usd_hourly"]
        instances.append({
            "id": name,
            "name": name,
            "vcpu": m["vcpu"],
            "ram_gb": m["ram_gb"],
            "disk_gb": 0,
            "disk_type": "pd-ssd",
            "price_hourly": round(price_usd * rate, 4),
            "price_monthly": round(price_usd * rate * HOURS_PER_MONTH, 2),
            "price_usd_hourly": round(price_usd, 6),
            "currency": "EUR",
            "architecture": "arm64" if family in _GCP_ARM else "x86",
            "location": REGION,
            "category": _category(name, family),
            "family": family,
        })

    if len(instances) < MIN_INSTANCES:
        raise common.FetchError(
            f"only {len(instances)} GCP machine types parsed for {REGION} "
            f"(expected >= {MIN_INSTANCES}); page structure changed?"
        )

    instances.sort(key=lambda x: (x["family"], x["vcpu"], x["ram_gb"], x["id"]))
    print(f"  {len(instances)} instances.")

    gke_usd = 73.0  # GKE Standard: $0.10/hr × 730h

    return {
        "provider": "gcp",
        "source_url": PAGE_URL.format(page="general-purpose"),
        "source_region": REGION,
        "control_plane_cost": round(gke_usd * rate, 2),
        "instances": instances,
        "load_balancer": {
            "price_monthly": round(18.0 * rate, 2),
            "notes": "Plus data processing charges",
        },
        "block_storage": {
            "price_per_gb_monthly": round(0.17 * rate, 4),
            "type": "pd-ssd",
        },
        "egress": {
            "free_gb": 0,
            "price_per_gb": round(0.12 * rate, 4),
            "notes": "Tiered pricing based on destination and volume",
        },
        "object_storage": {
            "price_per_gb_monthly": round(0.02 * rate, 4),
            "type": "Cloud Storage Standard",
            "notes": "Regional €0.018/GB, Multi-region (EU) €0.024/GB",
        },
    }


def fetch(ctx: common.Context) -> dict:
    # round before use: full precision shifts the last digit of many prices
    rate = round(ctx.fx.eur_per("USD"), 4)
    payload = _scrape_gcp(ctx, rate)
    payload["eur_usd_rate"] = round(rate, 4)  # already rounded; kept for consumers
    payload["fx"] = ctx.fx.block(("USD",))
    payload["manual"] = False
    payload["fetched_at"] = common.now_iso()
    return payload
