"""Scaleway — public product catalog (no authentication)."""

from __future__ import annotations

from . import common
from .common import HOURS_PER_MONTH

BASE_URL = "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products"
PAGE_SIZE = 1000
DEFAULT_ZONE = "fr-par-1"
NANOS = 1_000_000_000
BYTES_PER_GB = 1024 ** 3

# Category mapping by product ID prefix. Order matters: check longer prefixes first.
CATEGORY_MAP = [
    ("STARDUST", "development"),
    ("DEV1", "development"),
    ("PLAY2", "development"),
    ("COMPUTE3", "compute"),
    ("POP2-HC", "compute"),
    ("MEMORY3", "memory"),
    ("POP2-HM", "memory"),
    ("RENDER", "gpu"),
]


def _get_category(product_id: str) -> str:
    """Determine category from product ID using prefix matching."""
    for prefix, category in CATEGORY_MAP:
        if product_id.startswith(prefix):
            return category
    # Check for GPU indicators anywhere in the product ID
    if any(indicator in product_id for indicator in ("GPU", "H100", "L4", "L40S")):
        return "gpu"
    return "general"


def _price_eur(retail: dict) -> float:
    return retail.get("units", 0) + retail.get("nanos", 0) / NANOS


def _architecture(arch: str) -> str:
    return "arm64" if arch.startswith("arm") else "x86"


def parse_catalog(products: list[dict], zone: str) -> list[dict]:
    instances = []
    for product in products:
        # current offers are under /compute/; /instance/server holds only retired ones
        if not product.get("sku", "").startswith("/compute/"):
            continue
        if not product.get("properties", {}).get("hardware"):
            continue
        if product.get("locality", {}).get("zone") != zone:
            continue
        if product.get("unit_of_measure", {}).get("unit") != "hour":
            continue

        product_id = product.get("product")
        price_data = product.get("price", {}).get("retail_price")
        if not product_id or not price_data:
            continue

        hardware = product.get("properties", {}).get("hardware", {})
        cpu = hardware.get("cpu", {})
        ram = hardware.get("ram", {})
        storage = hardware.get("storage", {})
        meta = product.get("properties", {}).get("instance", {})

        hourly = _price_eur(price_data)
        instances.append({
            "id": product_id,
            "name": product_id,
            "vcpu": cpu.get("virtual", {}).get("count", 0),
            "ram_gb": round(ram.get("size", 0) / BYTES_PER_GB),
            "disk_gb": round(storage.get("total", 0) / BYTES_PER_GB),
            "disk_type": "ssd" if storage.get("total") else "block-storage-required",
            "price_hourly": round(hourly, 5),
            "price_monthly": round(hourly * HOURS_PER_MONTH, 2),
            "currency": price_data.get("currency_code", "EUR"),
            "architecture": _architecture(cpu.get("arch", "x64")),
            "category": _get_category(product_id),
            "range": meta.get("range", ""),
            "location": zone,
            "cpu_type": cpu.get("type", ""),
        })

    instances.sort(key=lambda i: i["id"])
    return instances


def fetch(ctx: common.Context, zone: str = DEFAULT_ZONE) -> dict:
    products: list[dict] = []
    page = 1
    while True:
        url = f"{BASE_URL}?page_size={PAGE_SIZE}&page={page}"
        data = ctx.http_get_json(url)
        batch = data.get("products", [])
        products.extend(batch)
        total = data.get("total_count", len(products))
        if not batch or len(products) >= total:
            break
        page += 1

    instances = parse_catalog(products, zone)
    if not instances:
        raise common.FetchError(f"no instance products found for zone {zone}")

    return {
        "provider": "scaleway",
        "fetched_at": common.now_iso(),
        "source_url": BASE_URL,
        "source_zone": zone,
        "manual": False,
        "instances": instances,
    }
