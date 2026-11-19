# EU Cloud Prices

Open dataset of European cloud provider pricing for VPS instances, Kubernetes, and related services.

Data source for [eucloudcost.com](https://www.eucloudcost.com)

## Providers

| Provider | Type | Country |
|----------|------|---------|
| Hetzner | VM + Managed K8s | DE |
| OVH | VM + Managed K8s | FR |
| Scaleway | VM + Managed K8s | FR |
| IONOS | VM + Managed K8s | DE |
| UpCloud | VM + Managed K8s | FI |
| Exoscale | VM + Managed K8s | CH |
| STACKIT | VM + Managed K8s | DE |
| Civo | Managed K8s | UK |
| Infomaniak | VM + Managed K8s | CH |
| Hikube | VM + Managed K8s + GPU + DBaaS | CH |
| netcup | VM | DE |
| Contabo | VM | DE |
| Atlantic.Cloud | VM | PT |
| Euronodes | VM | CY |
| Hostinger | VM | LT |
| Aruba Cloud | VM | IT |
| Webdock | VM | DK |
| gridscale | VM | DE |
| Leafcloud | VM | NL |
| plusserver | VM | DE |
| Cyso | VM | NL |
| metalstack | VM | DE |
| T Cloud Public | VM + Managed K8s + GPU | DE |
| SysEleven | VM + Managed K8s + GPU | DE |
| enum | Managed K8s | DE |
| Elastx | VM + Managed K8s + GPU | SE |
| Verda | VM + GPU | FI |
| AWS | VM + Managed K8s | US |
| Azure | VM + Managed K8s | US |
| GCP | VM + Managed K8s | US |

## Structure

```
├── prices/              # Individual provider pricing files
│   ├── hetzner.json
│   ├── aws.json
│   └── ...
├── normalized.json      # Combined data from all providers
└── providers.json       # Provider metadata (features, certifications, locations)
```

## Automated updates

Seven providers are refreshed automatically every Friday by
[`.github/workflows/fetch-prices.yml`](.github/workflows/fetch-prices.yml):
`aws`, `azure`, `gcp`, `ovh`, `scaleway`, `stackit`, `atlanticcloud`. They are
the providers with public, unauthenticated pricing data. Every other provider
file is maintained by hand and is never written to by automation.

`gcp` reads Google's public pricing pages on cloud.google.com (general-purpose,
compute-optimized, memory-optimized). Those pages embed the tables for every
region in the HTML; the fetcher takes the europe-west1 on-demand hourly prices
and fails if it finds fewer than 100 machine types, so a page redesign shows up
as a failed-fetch issue rather than a shrunken file. The old JSON feed on
www.gstatic.com was withdrawn in Aug 2026 (#36).

The workflow fetches all providers in one pass, validates required fields, runs
the schema-validation tests against `prices/schema.json`, then runs
`scripts/plausibility_check.py` once for every provider (`--json-out` gives a
per-provider verdict alongside the human-readable report). It then opens **one
PR per provider that actually changed**, each on its own `bot/prices-<provider>`
branch:

- A provider whose file changed and passed the gate cleanly gets a normal PR,
  ready for review.
- A provider whose file changed but tripped a hard finding still gets a PR —
  as a **draft**, labelled `blocked`, with the warning and that provider's
  findings at the top of the body. The gate's job is to make the risk
  unmissable, not to hide the diff: reviewing a blocked PR is how you see
  exactly what changed and decide whether to mark it ready.
- A provider whose fetch itself failed (e.g. an upstream 404) never produces a
  diff, so it gets no PR; instead the workflow opens (or updates) one issue
  per failed provider.
- A provider with no change at all gets nothing.

Re-running the workflow updates each provider's existing PR in place (body,
and a ready ⇄ draft transition if its verdict flipped) rather than opening a
duplicate. `normalized.json` is never touched by this workflow — pushing to
`main` under `prices/**` regenerates it separately via
[`.github/workflows/normalize.yml`](.github/workflows/normalize.yml), so five
per-provider PRs never fight over regenerating it themselves. A human reviews
and merges every data change.

Run it locally:

```bash
pip install -r requirements-dev.txt
python scripts/fetch_prices.py --provider all --dry-run
python scripts/plausibility_check.py                       # gate everything
python scripts/plausibility_check.py --provider aws        # gate one provider
python scripts/plausibility_check.py --json-out /tmp/verdicts.json
```

### Design decisions

Some choices in the fetchers look arbitrary until you know why they were made.

**Exchange rates have no fallback.** `scripts/fetchers/fx.py` fetches the ECB
daily reference rate and raises if it cannot. It never substitutes a default.
A guessed rate would silently misprice every USD-sourced instance with no trace
in the output, so `aws` and `gcp` are skipped on an ECB outage instead. The
providers that are EUR at source — `azure`, `ovh`, `scaleway`,
`atlanticcloud` — do not depend on the rate and keep updating regardless.

**The plausibility gate measures price movement net of FX.** A file records the
rate it was built with in its `fx` block, so a 50% swing caused purely by the
euro moving is not mistaken for a 50% price change. Only files carrying an `fx`
block are netted; the rest are compared directly.

**A month is 730 hours everywhere.** Hourly-to-monthly conversion uses the same
constant for every provider. One provider's file previously used 720, which
made it look ~1.4% cheaper than its competitors in any comparison. Consistency
matters more here than matching any single provider's own arithmetic.

**OVH's flavor specs are maintained by hand.** OVH's public catalog publishes
prices but no vCPU/RAM/disk figures, so `scripts/fetchers/data/ovh_flavors.json`
supplies them. Flavors absent from that map are reported in the file's
`unknown_plan_codes` rather than dropped, which is why the count is large — OVH
sells far more tiers (GPU, bare metal, high-memory) than this dataset covers.
Adding a tier means adding its specs to that map.

**OVH is one file from two catalogs.** Besides the Public Cloud flavors, the
fetcher reads OVH's VPS order catalog and adds the range OVH currently sells
(the plans tagged `website:show`) as instances with `category: "vps"`. Their
specs come from the catalog itself. The price is the monthly rate without
commitment, not the 12-month "from" price on ovhcloud.com, and there is no
hourly price because VPS are billed monthly. Older VPS generations still in
the catalog and local-zone plans are skipped. VPS are not Public Cloud
instances and cannot be Managed Kubernetes worker nodes, so anything that
prices an OVH cluster has to leave `category: "vps"` out.

**Component checks.** Besides `instances`, the gate inspects
`control_plane_cost` and the `load_balancer`, `block_storage`,
`object_storage` and `egress` blocks. Every numeric price-like field is
checked (names containing `price`, `retrieval`, or ending in `_monthly`,
`_hourly`, `_cost`; nested `tiers` included). Notes, `currency`, bandwidth,
quotas, booleans and strings are skipped.

| Check | Severity | Rule |
|---|---|---|
| `component_negative_price` | hard | any price field below 0 |
| `component_price_missing` | hard | number at HEAD, now missing, null or non-numeric |
| `component_price_zeroed` | hard | positive at HEAD, now exactly 0 |
| `component_currency_change` | hard | a block's own `currency` differs from HEAD (prices then not compared) |
| `component_price_move` | soft | moved more than `COMPONENT_MOVE_PCT` (15 %, per-provider override via `OVERRIDES`) |
| `egress_free_allowance_change` | soft | any `free_*` field of `egress` changed, including number <-> `"unlimited"` |

Movement is measured net of FX exactly like instance prices: for files with a
USD rate on both sides the EUR figures are converted back to source currency
first. A block that carries its own `currency` is compared as-is.

**What the gate does not check.** For `aws` and `gcp` the component blocks are
derived from hardcoded source-currency constants that do not refresh. The gate
flags movement versus HEAD, so it cannot detect a constant that never changes
and has drifted from the provider's real price. Nor does it check that a
component exists at all (a provider that never had one is not flagged), tier
structure, or `object_storage` allowances such as `included_*`/`free_*`.

## Data Format

### prices/*.json

Each provider file contains:

```json
{
  "provider": "hetzner",
  "fetched_at": "2026-01-25T19:32:47.301Z",
  "instances": [
    {
      "id": "ccx13",
      "name": "CCX13",
      "vcpu": 2,
      "ram_gb": 8,
      "disk_gb": 80,
      "disk_type": "nvme",
      "price_hourly": 0.02,
      "price_monthly": 12.49,
      "currency": "EUR",
      "architecture": "x86",
      "location": "eu",
      "category": "dedicated"
    }
  ],
  "load_balancer": { "price_monthly": 5.39 },
  "block_storage": { "price_per_gb_monthly": 0.044 },
  "egress": { "free_tb": 20, "price_per_gb_overage": 0.01 }
}
```

### normalized.json

Combined data from all providers:

```json
{
  "last_updated": "2026-01-25T19:32:53.284Z",
  "providers": {
    "hetzner": { ... },
    "aws": { ... }
  },
  "errors": []
}
```

### providers.json

Provider metadata:

```json
{
  "hetzner": {
    "name": "Hetzner",
    "country": "DE",
    "flag": "de",
    "type": "managed",
    "managed_k8s": true,
    "control_plane_cost": 0,
    "locations": ["Falkenstein", "Nuremberg", "Helsinki"],
    "certifications": ["ISO27001"],
    "free_egress_tb": 20
  }
}
```

## Currency

All prices are in **EUR**.

## Contributing

Found outdated pricing? Open an issue or PR.

## License

This data is provided as-is for informational purposes. Pricing data is sourced from public APIs and provider websites. Always verify with the provider before making purchasing decisions.
