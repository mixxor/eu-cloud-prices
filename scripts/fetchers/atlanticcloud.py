"""Atlantic.Cloud — public configurator endpoints (no authentication).

The API returns unit prices and the selectable option grids; instances are the
full cartesian product of those options.
"""

from __future__ import annotations

from . import common

VPS_URL = "https://api.atlantic.cloud/offer/vps-config?currency=EUR&trackViewItem=true"
VDS_URL = "https://api.atlantic.cloud/offer/vds-config?currency=EUR&trackViewItem=true"

#: API offer type -> the category name used in prices/atlanticcloud.json
CATEGORIES = (("vps", VPS_URL), ("dedicated", VDS_URL))


def build_grid(config: dict, category: str) -> list[dict]:
    try:
        cpu_price = config["cpuPricePerMonth"]
        ram_price = config["ramPricePerGbPerMonth"]
        disk_price = config["diskPricePerGbPerMonth"]
    except KeyError as exc:
        raise common.FetchError(f"atlantic.cloud {category} config missing key {exc}")

    disk_type = config.get("diskType", "SSD").lower()
    currency = config.get("currency", "EUR")

    instances = []
    try:
        cpu_options = config["cpuOptions"]
        ram_options = config["ramOptions"]
        disk_options = config["diskOptions"]
    except KeyError as exc:
        raise common.FetchError(f"atlantic.cloud {category} config missing key {exc}")

    for vcpu in cpu_options:
        for ram in ram_options:
            for disk in disk_options:
                price = vcpu * cpu_price + ram * ram_price + disk * disk_price
                instances.append({
                    "id": f"custom-{category}-{vcpu}c{ram}g{disk}g",
                    "name": f"{vcpu} vCPU / {ram}GB RAM / {disk}GB {disk_type.upper()} ({category})",
                    "vcpu": vcpu,
                    "ram_gb": ram,
                    "disk_gb": disk,
                    "disk_type": disk_type,
                    "price_monthly": round(price, 2),
                    "currency": currency,
                    "location": "eu",
                    "category": category,
                })
    return instances


def fetch(ctx: common.Context) -> dict:
    instances: list[dict] = []
    for category, url in CATEGORIES:
        config = ctx.http_get_json(url)
        grid = build_grid(config, category)
        # per-category check: a half grid (e.g. vps ok, dedicated empty) must not
        # be published as a successful fetch just because other categories filled in.
        if not grid:
            raise common.FetchError(f"atlantic.cloud {category} config returned no options")
        instances.extend(grid)

    if not instances:
        raise common.FetchError("atlantic.cloud returned no configurable options")

    return {
        "provider": "atlanticcloud",
        "fetched_at": common.now_iso(),
        "source": VPS_URL,
        "manual": False,
        "instances": instances,
    }
