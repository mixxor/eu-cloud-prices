"""GCP Compute Engine on-demand pricing (europe-west1)."""

from __future__ import annotations

from . import common
from .common import HOURS_PER_MONTH

_GCP_CATEGORY: dict[str, str] = {
    "n1": "general", "n2": "general", "n2d": "general", "n4": "general",
    "e2": "general",
    "c2": "compute", "c3": "compute", "c3d": "compute", "c4": "compute", "c4a": "compute",
    "m1": "memory", "m2": "memory", "m3": "memory",
    "t2a": "general", "t2d": "general",
    "h3": "hpc",
    "z3": "storage",
}

_GCP_ARM = {"t2a", "c4a"}
_GCP_SKIP_FAMILIES = {"a2", "a3", "g2", "a4"}  # GPU-only


def _scrape_gcp(ctx: common.Context, rate: float) -> dict:
    url = "https://www.gstatic.com/cloud-site-ux/pricing/data/gcp-compute.json"
    region = "europe-west1"
    print(f"  Fetching GCP Compute ({region}) …")
    data = ctx.http_get_json(url)

    price_list = data.get("gcp_price_list", {})

    instances = []
    for key, info in price_list.items():
        if not key.startswith("CP-COMPUTEENGINE-VMIMAGE-"):
            continue
        if region not in info:
            continue

        price_usd = float(info[region])
        if price_usd <= 0:
            continue

        cores_raw = info.get("cores", "0")
        mem_raw = info.get("memory", "0")
        try:
            vcpu = int(cores_raw) if cores_raw != "shared" else 1
        except (ValueError, TypeError):
            vcpu = 0
        try:
            ram_gb = float(mem_raw)
        except (ValueError, TypeError):
            ram_gb = 0.0

        # CP-COMPUTEENGINE-VMIMAGE-C2-STANDARD-4 → c2-standard-4
        name = key.replace("CP-COMPUTEENGINE-VMIMAGE-", "").lower()
        family = name.split("-")[0]

        if family in _GCP_SKIP_FAMILIES:
            continue

        instances.append({
            "id": name,
            "name": name,
            "vcpu": vcpu,
            "ram_gb": ram_gb,
            "disk_gb": 0,
            "disk_type": "pd-ssd",
            "price_hourly": round(price_usd * rate, 4),
            "price_monthly": round(price_usd * rate * HOURS_PER_MONTH, 2),
            "price_usd_hourly": round(price_usd, 4),
            "currency": "EUR",
            "architecture": "arm64" if family in _GCP_ARM else "x86",
            "location": region,
            "category": _GCP_CATEGORY.get(family, "general"),
            "family": family,
        })

    instances.sort(key=lambda x: (x["family"], x["vcpu"], x["ram_gb"]))
    print(f"  {len(instances)} instances.")

    gke_usd = 73.0  # GKE Standard: $0.10/hr × 730h

    return {
        "provider": "gcp",
        "source_url": url,
        "source_region": region,
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
