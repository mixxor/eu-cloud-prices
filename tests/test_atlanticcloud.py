import json
from pathlib import Path

from scripts.fetchers import atlanticcloud

CONFIG = json.loads((Path(__file__).parent / "fixtures" / "atlantic_vps.json").read_text())


def test_grid_size_is_the_cartesian_product():
    grid = atlanticcloud.build_grid(CONFIG, "vps")
    expected = len(CONFIG["cpuOptions"]) * len(CONFIG["ramOptions"]) * len(CONFIG["diskOptions"])
    assert len(grid) == expected == 252


def test_price_formula():
    config = {
        "cpuPricePerMonth": 1.3, "ramPricePerGbPerMonth": 0.4,
        "diskPricePerGbPerMonth": 0.03, "diskType": "SSD", "currency": "EUR",
        "cpuOptions": [2], "ramOptions": [4], "diskOptions": [60],
    }

    [inst] = atlanticcloud.build_grid(config, "vps")

    # 2*1.3 + 4*0.4 + 60*0.03 = 2.6 + 1.6 + 1.8 = 6.0
    assert inst["price_monthly"] == 6.0
    assert inst["id"] == "custom-vps-2c4g60g"
    assert inst["name"] == "2 vCPU / 4GB RAM / 60GB SSD (vps)"
    assert inst["disk_type"] == "ssd"
    assert inst["location"] == "eu"
    assert inst["category"] == "vps"


def test_dedicated_category_uses_its_own_unit_prices():
    config = {
        "cpuPricePerMonth": 1.7, "ramPricePerGbPerMonth": 0.55,
        "diskPricePerGbPerMonth": 0.03, "diskType": "SSD", "currency": "EUR",
        "cpuOptions": [2], "ramOptions": [4], "diskOptions": [60],
    }

    [inst] = atlanticcloud.build_grid(config, "dedicated")

    # 2*1.7 + 4*0.55 + 60*0.03 = 3.4 + 2.2 + 1.8 = 7.4
    assert inst["price_monthly"] == 7.4
    assert inst["id"] == "custom-dedicated-2c4g60g"


def test_fetch_combines_both_endpoints():
    vps_config = {
        "type": "VPS",
        "cpuPricePerMonth": 1.3,
        "ramPricePerGbPerMonth": 0.4,
        "diskPricePerGbPerMonth": 0.03,
        "diskType": "SSD",
        "currency": "EUR",
        "cpuOptions": [2, 4],
        "ramOptions": [4, 8],
        "diskOptions": [60, 120],
    }

    vds_config = {
        "type": "VDS",
        "cpuPricePerMonth": 1.7,
        "ramPricePerGbPerMonth": 0.55,
        "diskPricePerGbPerMonth": 0.03,
        "diskType": "SSD",
        "currency": "EUR",
        "cpuOptions": [2, 4],
        "ramOptions": [4, 8],
        "diskOptions": [60, 120],
    }

    def stub(url, *args, **kwargs):
        from scripts.fetchers.atlanticcloud import VPS_URL, VDS_URL
        if url == VPS_URL:
            return vps_config
        elif url == VDS_URL:
            return vds_config
        else:
            raise ValueError(f"Unexpected URL: {url}")

    from pathlib import Path as P
    from scripts.fetchers import common
    payload = atlanticcloud.fetch(common.Context(prices_dir=P("prices"), http_get_json=stub))

    assert payload["provider"] == "atlanticcloud"
    assert payload["manual"] is False
    assert len(payload["instances"]) == 16  # (2 CPU * 2 RAM * 2 disk) * 2 categories
    assert {i["category"] for i in payload["instances"]} == {"vps", "dedicated"}

    # Verify VPS instances use VPS unit prices
    vps_instances = [i for i in payload["instances"] if i["category"] == "vps"]
    assert len(vps_instances) == 8  # 2 * 2 * 2
    # Find 2vCPU/4GB/60GB instance: 2*1.3 + 4*0.4 + 60*0.03 = 2.6 + 1.6 + 1.8 = 6.0
    vps_2c4g60g = [i for i in vps_instances if i["id"] == "custom-vps-2c4g60g"][0]
    assert vps_2c4g60g["price_monthly"] == 6.0

    # Verify dedicated instances use VDS unit prices
    dedicated_instances = [i for i in payload["instances"] if i["category"] == "dedicated"]
    assert len(dedicated_instances) == 8  # 2 * 2 * 2
    # Find 2vCPU/4GB/60GB instance: 2*1.7 + 4*0.55 + 60*0.03 = 3.4 + 2.2 + 1.8 = 7.4
    dedicated_2c4g60g = [i for i in dedicated_instances if i["id"] == "custom-dedicated-2c4g60g"][0]
    assert dedicated_2c4g60g["price_monthly"] == 7.4


def test_fetch_raises_error_on_empty_vps_grid():
    vps_config_empty = {
        "type": "VPS",
        "cpuPricePerMonth": 1.3,
        "ramPricePerGbPerMonth": 0.4,
        "diskPricePerGbPerMonth": 0.03,
        "diskType": "SSD",
        "currency": "EUR",
        "cpuOptions": [],  # Empty!
        "ramOptions": [4, 8],
        "diskOptions": [60, 120],
    }

    vds_config = {
        "type": "VDS",
        "cpuPricePerMonth": 1.7,
        "ramPricePerGbPerMonth": 0.55,
        "diskPricePerGbPerMonth": 0.03,
        "diskType": "SSD",
        "currency": "EUR",
        "cpuOptions": [2, 4],
        "ramOptions": [4, 8],
        "diskOptions": [60, 120],
    }

    def stub(url, *args, **kwargs):
        from scripts.fetchers.atlanticcloud import VPS_URL, VDS_URL
        if url == VPS_URL:
            return vps_config_empty
        elif url == VDS_URL:
            return vds_config
        else:
            raise ValueError(f"Unexpected URL: {url}")

    from pathlib import Path as P
    from scripts.fetchers import common
    import pytest

    with pytest.raises(common.FetchError, match="vps config returned no options"):
        atlanticcloud.fetch(common.Context(prices_dir=P("prices"), http_get_json=stub))


def test_fetch_raises_error_on_empty_dedicated_grid():
    vps_config = {
        "type": "VPS",
        "cpuPricePerMonth": 1.3,
        "ramPricePerGbPerMonth": 0.4,
        "diskPricePerGbPerMonth": 0.03,
        "diskType": "SSD",
        "currency": "EUR",
        "cpuOptions": [2, 4],
        "ramOptions": [4, 8],
        "diskOptions": [60, 120],
    }

    vds_config_empty = {
        "type": "VDS",
        "cpuPricePerMonth": 1.7,
        "ramPricePerGbPerMonth": 0.55,
        "diskPricePerGbPerMonth": 0.03,
        "diskType": "SSD",
        "currency": "EUR",
        "cpuOptions": [2, 4],
        "ramOptions": [],  # Empty!
        "diskOptions": [60, 120],
    }

    def stub(url, *args, **kwargs):
        from scripts.fetchers.atlanticcloud import VPS_URL, VDS_URL
        if url == VPS_URL:
            return vps_config
        elif url == VDS_URL:
            return vds_config_empty
        else:
            raise ValueError(f"Unexpected URL: {url}")

    from pathlib import Path as P
    from scripts.fetchers import common
    import pytest

    with pytest.raises(common.FetchError, match="dedicated config returned no options"):
        atlanticcloud.fetch(common.Context(prices_dir=P("prices"), http_get_json=stub))
