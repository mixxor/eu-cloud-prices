#!/usr/bin/env python3
import json
import os
import re
import datetime
from dataclasses import asdict, dataclass

import requests

API_URL = "https://prices.azure.com/api/retail/prices?api-version=2021-10-01-preview"
CURRENCY = "EUR"
REGION = "westeurope"
HOURS_PER_MONTH = 730
ONE_YEAR_TERM = "1 Year"
THREE_YEARS_TERM = "3 Years"
ONE_YEAR_HOURS = 8760
THREE_YEARS_HOURS = 26280
SUPPORTED_FAMILIES = {"B", "D", "E", "F"}
DEFAULT_DISK_GB = 0
DEFAULT_DISK_TYPE = "managed-ssd"
PRICES_DIR = "prices"
ONE_YEAR_FILE = "azure-reservations-1year"
THREE_YEARS_FILE = "azure-reservations-3years"

SKU_PATTERN = re.compile(
    r"Standard_"
    r"(?P<family>[A-Z])"
    r"(?P<subfamily>[A-Z]+)?"
    r"(?P<cpu_count>\d+)"
    r"(?P<constrainedcpu_count>-\d+)?"
    r"(?P<attributes>[a-z]+)?"
    r"(?P<accelerator>_H\w+)?"
    r"(?P<version>_v\d)?"
)

FAMILY_CATEGORY_MAP = {
    "B": "burstable",
    "D": "general",
    "E": "memory",
    "F": "compute",
    "H": "performance",
    "L": "storage",
    "M": "ultramemory",
    "N": "gpu",
}


@dataclass
class AzureVMPrice:
    id: str
    name: str
    vcpu: int
    ram_gb: float
    disk_gb: int
    disk_type: str
    price_hourly: float
    price_monthly: float
    currency: str
    architecture: str
    location: str
    category: str
    family: str
    is_amd: bool
    version: str


def calculate_hourly_price(item: dict) -> float:
    reservation_term = item.get("reservationTerm")
    retail_price = item["retailPrice"]

    if reservation_term == ONE_YEAR_TERM:
        return retail_price / ONE_YEAR_HOURS
    if reservation_term == THREE_YEARS_TERM:
        return retail_price / THREE_YEARS_HOURS

    return retail_price


def get_architecture(attributes: str) -> str:
    if "p" in attributes:
        return "arm64"
    return "x86"


def calculate_memory_gb(family: str, attributes: str, version: str, cpu_count: int) -> float:
    series = f"{family}{attributes}{version}"
    memory_ratio = 4

    if family == "E":
        memory_ratio = 8
    if series == "Fsv2":
        memory_ratio = 2
    if "l" in attributes:
        memory_ratio /= 2

    return cpu_count * memory_ratio


def create_vm_price(item: dict, sku_match: re.Match) -> AzureVMPrice:
    family = sku_match.group("family")
    cpu_count = int(sku_match.group("cpu_count"))
    attributes = sku_match.group("attributes") or ""
    version = sku_match.group("version") or ""
    version = version.replace("_", "")

    sku_name = item["armSkuName"].replace("Standard_", "")
    hourly_price = calculate_hourly_price(item)
    architecture = get_architecture(attributes)
    category = FAMILY_CATEGORY_MAP.get(family, "unknown")
    memory_gb = calculate_memory_gb(family, attributes, version, cpu_count)

    return AzureVMPrice(
        id=sku_name,
        name=sku_name,
        vcpu=cpu_count,
        ram_gb=memory_gb,
        disk_gb=DEFAULT_DISK_GB,
        disk_type=DEFAULT_DISK_TYPE,
        price_hourly=hourly_price,
        price_monthly=hourly_price * HOURS_PER_MONTH,
        currency=CURRENCY,
        architecture=architecture,
        location=REGION,
        category=category,
        family=family,
        is_amd="a" in attributes,
        version=f"_{version}",
    )


def parse_prices(json_data: dict, prices_by_term: dict[str, list[AzureVMPrice]]) -> None:
    for item in json_data["Items"]:
        sku_name = item["armSkuName"]

        if not sku_name.startswith("Standard_"):
            continue

        sku_match = SKU_PATTERN.search(sku_name)
        if sku_match is None:
            continue

        family = sku_match.group("family")
        if family not in SUPPORTED_FAMILIES:
            continue

        reservation_term = item.get("reservationTerm")
        if reservation_term not in prices_by_term:
            continue

        prices_by_term[reservation_term].append(create_vm_price(item, sku_match))


def fetch_price_pages() -> dict[str, list[AzureVMPrice]]:
    query = (
        f"armRegionName eq '{REGION}' "
        "and serviceName eq 'Virtual Machines' "
        "and priceType eq 'Reservation'"
    )
    request_params = {
        "currencyCode": CURRENCY,
        "$filter": query,
    }
    prices_by_term = {
        ONE_YEAR_TERM: [],
        THREE_YEARS_TERM: [],
    }

    response = requests.get(API_URL, params=request_params)
    json_data = json.loads(response.text)
    parse_prices(json_data, prices_by_term)

    next_page_url = json_data["NextPageLink"]
    while next_page_url:
        response = requests.get(next_page_url)
        json_data = json.loads(response.text)
        parse_prices(json_data, prices_by_term)
        next_page_url = json_data["NextPageLink"]

    return prices_by_term


def print_table(name: str, vm_prices: list[AzureVMPrice]) -> None:
    print(f" ---- {name}")

    headers = [
        "Name",
        "vCPU",
        "Memory",
        "Disk",
        "Price Hourly",
        "Price Monthly",
        "Architecture",
        "Category",
        "Family",
        "Amd",
        "Version",
    ]

    rows = [
        [
            vm.name,
            vm.vcpu,
            vm.ram_gb,
            vm.disk_gb,
            vm.price_hourly,
            vm.price_monthly,
            vm.architecture,
            vm.category,
            vm.family,
            vm.is_amd,
            vm.version,
        ]
        for vm in sorted(vm_prices, key=lambda vm: vm.name)
    ]

    col_widths = [
        max(len(str(cell)) for cell in [header, *(row[i] for row in rows)])
        for i, header in enumerate(headers)
    ]

    def format_row(row: list) -> str:
        return " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))

    separator = "-+-".join("-" * width for width in col_widths)

    print(format_row(headers))
    print(separator)
    for row in rows:
        print(format_row(row))


def write_json(filename: str, vm_prices: list[AzureVMPrice]) -> None:
    os.makedirs(PRICES_DIR, exist_ok=True)
    path = os.path.join(PRICES_DIR, filename+".json")

    with open(path, "w") as f:
        json.dump({
            "provider": filename,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_url": "https://prices.azure.com/api/retail/prices",
            "source_region": "westeurope",
            "manual": False,
            "control_plane_cost": 0,
            "instances": [asdict(vm) for vm in vm_prices],
            "load_balancer": {
                "price_monthly": 22.27,
                "notes": "Plus data processing charges"
            },
            "block_storage": {
                "price_per_gb_monthly": 0.15,
                "type": "Premium_SSD"
            },
            "egress": {
                "free_gb": 5,
                "price_per_gb": 0.08,
                "notes": "Tiered pricing: \u20ac0.08/GB first 10TB, \u20ac0.07 next 40TB"
            },
            "object_storage": {
                "price_per_gb_monthly": 0.018,
                "type": "Blob Storage Hot",
                "notes": "Tiered: \u20ac0.018/GB first 50TB, \u20ac0.017 next 450TB, \u20ac0.0166 over 500TB"
            }
        }, f, indent=2)


def main() -> None:
    prices_by_term = fetch_price_pages()

    print_table(ONE_YEAR_TERM, prices_by_term[ONE_YEAR_TERM])
    print_table(THREE_YEARS_TERM, prices_by_term[THREE_YEARS_TERM])

    write_json(ONE_YEAR_FILE, prices_by_term[ONE_YEAR_TERM])
    write_json(THREE_YEARS_FILE, prices_by_term[THREE_YEARS_TERM])


if __name__ == "__main__":
    main()
