import json
from pathlib import Path
from unittest import mock

import pytest

from scripts.fetchers import common


def test_merge_preserves_hand_maintained_blocks():
    existing = {
        "provider": "ovh",
        "fetched_at": "2026-05-20T00:00:00Z",
        "manual": True,
        "control_plane_cost": 0,
        "instances": [{"id": "d2-2", "price_monthly": 8.98}],
        "load_balancer": {"price_monthly": 6.94},
        "block_storage": {"price_per_gb_monthly": 0.04},
        "egress": {"free_tb": "unlimited"},
        "object_storage": {"price_per_gb_monthly": 0.01},
    }
    fresh = {
        "provider": "ovh",
        "fetched_at": "2026-08-13T00:00:00Z",
        "manual": False,
        "instances": [{"id": "d2-2", "price_monthly": 7.59}],
    }

    merged = common.merge_price_file(existing, fresh)

    # fetcher-owned keys are replaced
    assert merged["fetched_at"] == "2026-08-13T00:00:00Z"
    assert merged["manual"] is False
    assert merged["instances"][0]["price_monthly"] == 7.59
    # hand-maintained keys survive untouched
    assert merged["load_balancer"] == {"price_monthly": 6.94}
    assert merged["block_storage"] == {"price_per_gb_monthly": 0.04}
    assert merged["egress"] == {"free_tb": "unlimited"}
    assert merged["object_storage"] == {"price_per_gb_monthly": 0.01}
    assert merged["control_plane_cost"] == 0


def test_merge_never_drops_an_unknown_existing_key():
    existing = {"provider": "x", "instances": [], "primary_ip": {"price_monthly": 1.0}}
    fresh = {"provider": "x", "instances": [{"id": "a"}]}

    merged = common.merge_price_file(existing, fresh)

    assert merged["primary_ip"] == {"price_monthly": 1.0}


def test_merge_on_missing_existing_file_returns_fresh():
    assert common.merge_price_file({}, {"provider": "new"}) == {"provider": "new"}


def test_write_price_file_merges_with_what_is_on_disk(tmp_path):
    (tmp_path / "ovh.json").write_text(json.dumps({
        "provider": "ovh",
        "instances": [],
        "load_balancer": {"price_monthly": 6.94},
    }))

    common.write_price_file("ovh", {"provider": "ovh", "instances": [{"id": "d2-2"}]}, tmp_path)

    written = json.loads((tmp_path / "ovh.json").read_text())
    assert written["load_balancer"] == {"price_monthly": 6.94}
    assert written["instances"] == [{"id": "d2-2"}]


def test_write_price_file_ends_with_newline(tmp_path):
    common.write_price_file("x", {"provider": "x", "instances": []}, tmp_path)
    assert (tmp_path / "x.json").read_text().endswith("}\n")


def test_http_get_json_rejects_a_host_not_on_the_allowlist():
    with pytest.raises(common.FetchError, match="not allowed"):
        common.http_get_json("https://evil.example.com/prices")


def test_http_get_json_rejects_redirect_to_disallowed_host():
    """A redirect to a host outside ALLOWED_HOSTS raises FetchError.

    This test directly exercises the redirect handler's validation logic.
    It verifies that the guard cannot be bypassed via HTTP redirect.
    """
    redirect_handler = common._AllowlistEnforcingHTTPRedirectHandler()
    mock_request = mock.Mock()
    mock_request.get_method.return_value = "GET"
    mock_fp = mock.Mock()

    # Redirect to a disallowed host should raise FetchError before
    # attempting to follow the redirect
    with pytest.raises(common.FetchError, match="not allowed"):
        redirect_handler.redirect_request(
            mock_request,
            mock_fp,
            code=302,
            msg="Found",
            hdrs={},
            newurl="https://evil.example.com/data"
        )


def test_http_get_json_permits_redirect_to_allowed_host():
    """A redirect to a host inside ALLOWED_HOSTS is allowed.

    This test directly exercises the redirect handler to verify it permits
    redirects to allowed hosts (does not raise). It returns a request object
    to the parent handler, enabling the redirect to proceed.
    """
    redirect_handler = common._AllowlistEnforcingHTTPRedirectHandler()
    mock_request = mock.Mock()
    mock_request.get_method.return_value = "GET"
    mock_request.full_url = "https://api.ovh.com/prices"
    mock_request.headers = {}
    mock_fp = mock.Mock()

    # Redirect to an allowed host should NOT raise; validation passes
    # and parent handler's redirect_request is called
    result = redirect_handler.redirect_request(
        mock_request,
        mock_fp,
        code=302,
        msg="Found",
        hdrs={},
        newurl="https://api.scaleway.com/other/path"
    )

    # Parent handler returns a request for the new URL
    assert result is not None


def test_http_get_json_rejects_redirect_with_disallowed_scheme():
    """A redirect to a non-http/https scheme raises FetchError.

    This verifies that the scheme validation holds for redirects as well,
    preventing redirects to ftp://, file://, etc.
    """
    redirect_handler = common._AllowlistEnforcingHTTPRedirectHandler()
    mock_request = mock.Mock()
    mock_request.get_method.return_value = "GET"
    mock_fp = mock.Mock()

    # ftp:// is not allowed even if the host is in ALLOWED_HOSTS
    with pytest.raises(common.FetchError, match="scheme.*not allowed"):
        redirect_handler.redirect_request(
            mock_request,
            mock_fp,
            code=302,
            msg="Found",
            hdrs={},
            newurl="ftp://api.ovh.com/data"
        )


def test_now_iso_is_utc_zulu():
    assert common.now_iso().endswith("Z")


def test_validate_fetch_url_accepts_allowed_host_with_explicit_port():
    """An explicit ':443' must not defeat the allowlist (netloc vs hostname bug)."""
    common._validate_fetch_url("https://prices.azure.com:443/api/retail/prices")


def test_validate_fetch_url_is_case_insensitive_on_host():
    common._validate_fetch_url("https://API.OVH.COM/v1/order/catalog")


def test_validate_fetch_url_rejects_disallowed_host_with_explicit_port():
    with pytest.raises(common.FetchError, match="not allowed"):
        common._validate_fetch_url("https://evil.example.com:443/x")


def test_validate_fetch_url_rejects_url_with_no_host():
    with pytest.raises(common.FetchError, match="not allowed"):
        common._validate_fetch_url("file:///etc/passwd")


def test_http_get_json_permits_redirect_to_allowed_host_with_explicit_port():
    """A redirect target carrying an explicit port to an allowed host is accepted."""
    redirect_handler = common._AllowlistEnforcingHTTPRedirectHandler()
    mock_request = mock.Mock()
    mock_request.get_method.return_value = "GET"
    mock_request.full_url = "https://prices.azure.com/api/retail/prices"
    mock_request.headers = {}
    mock_fp = mock.Mock()

    result = redirect_handler.redirect_request(
        mock_request,
        mock_fp,
        code=302,
        msg="Found",
        hdrs={},
        newurl="https://prices.azure.com:443/api/retail/prices?next=1"
    )

    assert result is not None


def test_http_get_json_rejects_redirect_to_disallowed_host_with_explicit_port():
    """A redirect target carrying an explicit port to a disallowed host is rejected."""
    redirect_handler = common._AllowlistEnforcingHTTPRedirectHandler()
    mock_request = mock.Mock()
    mock_request.get_method.return_value = "GET"
    mock_fp = mock.Mock()

    with pytest.raises(common.FetchError, match="not allowed"):
        redirect_handler.redirect_request(
            mock_request,
            mock_fp,
            code=302,
            msg="Found",
            hdrs={},
            newurl="https://evil.example.com:443/x"
        )
