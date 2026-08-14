"""Azure Virtual Machines retail pricing (westeurope).

Azure retail prices are already EUR-denominated at source, so ``fetch``
does no currency conversion (unlike aws.py / gcp.py) and attaches no
``fx`` block.
"""

from __future__ import annotations

import re
import sys
import urllib.parse

from . import common
from .common import HOURS_PER_MONTH

_AZURE_CATEGORY: dict[str, str] = {
    "FX": "compute",
    "B":  "burstable",
    "D":  "general",
    "E":  "memory",
    "F":  "compute",
}

# Only keep these families; everything else (GPU, HPC, confidential, old) excluded
_AZURE_KEEP_FAMILIES = frozenset({"B", "D", "E", "F", "FX"})

# Minimum version per family (as int, 0 = no minimum)
_AZURE_MIN_VERSION = {"D": 3, "E": 3, "F": 2, "B": 0, "FX": 0}


def _azure_family(display_id: str) -> str:
    """'D2s_v3' → 'D', 'NC6' → 'NC', 'FX4mds_v2' → 'FX'"""
    m = re.match(r"^([A-Z]{1,2})", display_id)
    return m.group(1) if m else "unknown"


def _azure_category(family: str) -> str:
    return _AZURE_CATEGORY.get(family, "general")


def _azure_arch(sku_name: str) -> str:
    # ARM (Ampere Altra): Dpsv5, Dplsv5, Epsv5, Dpdsv5 — digit followed by 'p' then [dls]?s?
    return "arm64" if re.search(r"\d+p[dls]?[ls]?\s", sku_name) else "x86"


def _azure_is_amd(sku_name: str) -> bool:
    return bool(re.search(r"\d+a[dls]?s?\s", sku_name))


def _azure_version(sku_name: str) -> str:
    m = re.search(r"\sv(\d+)$", sku_name, re.IGNORECASE)
    return f"_v{m.group(1)}" if m else ""


def _fetch_azure_specs(ctx: common.Context) -> dict[str, dict]:
    """
    Fetch vCPU + RAM from Azure pricing calculator API.
    Offer keys look like 'linux-d2sv3-standard' or 'windows-b8ms-standard'.
    Returns lookup dict keyed by the normalized part: 'linux-{slug}-standard'.
    """
    url = (
        "https://azure.microsoft.com/api/v3/pricing/virtual-machines/calculator/"
        "?cid=&calculatortype=vm&culture=en-US&currency=EUR"
    )
    print("  Fetching Azure VM specs from calculator API …")
    try:
        data = ctx.http_get_json(url, headers={"Accept": "application/json"})
        offers = data.get("offers", {})
        specs: dict[str, dict] = {}
        for offer_key, offer in offers.items():
            cores = int(offer.get("cores", 0) or 0)
            ram   = float(offer.get("ram", offer.get("memoryGB", 0)) or 0)
            if not cores:
                continue
            entry = {"vcpu": cores, "ram_gb": ram}
            # Store under full key and under the slug between 'linux-' / 'windows-' and '-standard'
            specs[offer_key] = entry
            # Extract slug: 'linux-d2sv3-standard' → 'd2sv3'
            m = re.match(r'^(?:linux|windows)-(.+)-standard$', offer_key)
            if m:
                specs[m.group(1)] = entry
        print(f"  Got specs for {len(offers)} VM offers.")
        return specs
    except Exception as e:
        print(f"  Warning: Azure specs fetch failed ({e}). vCPU/RAM will be 0.", file=sys.stderr)
        return {}


def _arm_sku_to_spec_key(arm_sku: str) -> list[str]:
    """
    Candidates for spec lookup from armSkuName like 'Standard_D2s_v3'.
    The calculator API uses keys like 'linux-d2sv3-standard' — slug is the middle part.
    """
    base = arm_sku.replace("Standard_", "").replace("Basic_", "")
    slug = base.lower().replace("_", "")  # d2sv3
    return [
        f"linux-{slug}-standard",   # preferred: matches 'linux-d2sv3-standard'
        slug,                        # fallback: bare slug stored during indexing
    ]


def _scrape_azure(ctx: common.Context) -> dict:
    base_url = "https://prices.azure.com/api/retail/prices"
    filter_str = (
        "serviceName eq 'Virtual Machines' "
        "and armRegionName eq 'westeurope' "
        "and priceType eq 'Consumption'"
    )
    url = base_url + "?" + urllib.parse.urlencode({
        "api-version": "2023-01-01-preview",
        "currencyCode": "EUR",
        "$filter": filter_str,
    })

    print("  Fetching Azure pricing (paginated) …")
    all_items: list[dict] = []
    page = 0
    while url:
        page += 1
        chunk = ctx.http_get_json(url)
        all_items.extend(chunk.get("Items", []))
        url = chunk.get("NextPageLink")
        if page % 10 == 0:
            print(f"    … page {page}, {len(all_items)} items")

    print(f"  Fetched {len(all_items)} items ({page} pages).")

    specs = _fetch_azure_specs(ctx)

    # Deduplicate: one entry per armSkuName; skip Windows / Spot / Low Priority / unwanted families
    seen: dict[str, dict] = {}
    for item in all_items:
        sku = item.get("skuName", "")
        arm_sku = item.get("armSkuName", "")
        product = item.get("productName", "")

        if "Windows" in sku or "Windows" in product:
            continue
        if "Spot" in sku or "Low Priority" in sku:
            continue

        price = float(item.get("retailPrice", 0) or 0)
        if price <= 0:
            continue

        # Family + version filter
        display = (arm_sku.replace("Standard_", "").replace("Basic_", "") if arm_sku else sku)
        fam = _azure_family(display)
        if fam not in _AZURE_KEEP_FAMILIES:
            continue
        ver = _azure_version(sku)           # '' or '_v3' etc.
        min_v = _AZURE_MIN_VERSION.get(fam, 0)
        if min_v:
            ver_num = int(ver.replace("_v", "") or "0")
            if ver_num < min_v:
                continue

        # Skip local-disk ('d' in suffix) and network-enhanced ('n' in suffix)
        # e.g. D2ds_v5 (local NVMe), D2ns_v6 (network-only), E4ads_v5 (AMD+local NVMe)
        suffix_m = re.match(r'^[A-Z]{1,2}\d+([a-z]+)(?:_v\d+)?$', display)
        if suffix_m:
            suffix = suffix_m.group(1)
            if 'd' in suffix or 'n' in suffix:
                continue

        key = arm_sku or sku
        if key not in seen:
            seen[key] = item

    instances = []
    for arm_sku, item in seen.items():
        sku = item.get("skuName", "")
        price_eur = float(item.get("retailPrice", 0))

        # Look up vCPU / RAM
        spec_key = next(
            (k for k in _arm_sku_to_spec_key(arm_sku) if k in specs),
            None,
        )
        spec = specs.get(spec_key, {}) if spec_key else {}
        vcpu = spec.get("vcpu", 0)
        ram_gb = spec.get("ram_gb", 0.0)

        display_id = (
            arm_sku.replace("Standard_", "").replace("Basic_", "")
            if arm_sku else sku
        )
        family = _azure_family(display_id)

        instances.append({
            "id": display_id,
            "name": display_id,
            "vcpu": vcpu,
            "ram_gb": ram_gb,
            "disk_gb": 0,
            "disk_type": "managed-ssd",
            "price_hourly": round(price_eur, 4),
            "price_monthly": round(price_eur * HOURS_PER_MONTH, 2),
            "currency": "EUR",
            "architecture": _azure_arch(sku),
            "location": "westeurope",
            "category": _azure_category(family),
            "family": family,
            "is_amd": _azure_is_amd(sku),
            "version": _azure_version(sku),
        })

    before = len(instances)
    instances = [i for i in instances if i["vcpu"] > 0]
    if before != len(instances):
        print(f"  Dropped {before - len(instances)} instances with vcpu=0.")
    instances.sort(key=lambda x: (x["family"], x["vcpu"], x["ram_gb"], x["price_hourly"]))
    print(f"  {len(instances)} instances.")

    # Azure retail prices are already EUR; load-balancer/storage/egress kept at known values
    return {
        "provider": "azure",
        "source_url": base_url,
        "source_region": "westeurope",
        "control_plane_cost": 0,  # AKS is free
        "instances": instances,
        "load_balancer": {
            "price_monthly": 22.27,
            "notes": "Plus data processing charges",
        },
        "block_storage": {
            "price_per_gb_monthly": 0.15,
            "type": "Premium_SSD",
        },
        "egress": {
            "free_gb": 5,
            "price_per_gb": 0.08,
            "notes": "Tiered pricing: €0.08/GB first 10TB, €0.07 next 40TB",
        },
        "object_storage": {
            "price_per_gb_monthly": 0.018,
            "type": "Blob Storage Hot",
            "notes": "Tiered: €0.018/GB first 50TB, €0.017 next 450TB, €0.0166 over 500TB",
        },
    }


def fetch(ctx: common.Context) -> dict:
    payload = _scrape_azure(ctx)
    # ctx.fx is accepted but unused: Azure prices are already EUR, so a
    # refresh must not depend on ECB availability.
    payload["manual"] = False
    payload["fetched_at"] = common.now_iso()
    return payload
