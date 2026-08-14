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

Five providers are refreshed automatically every Friday by
[`.github/workflows/fetch-prices.yml`](.github/workflows/fetch-prices.yml):
`aws`, `azure`, `ovh`, `scaleway`, `atlanticcloud`. They are the providers
with public, unauthenticated pricing APIs. `gcp` is also registered but is
currently failing on every run: its public pricing JSON was withdrawn and its
documented successor requires an API key, which this project does not use.
Every other provider file is maintained by hand and is never written to by
automation.

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
- A provider whose fetch itself failed (e.g. `gcp`'s 404s) never produces a
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

**What the gate does not check.** It compares the `instances` array only.
`control_plane_cost`, `load_balancer`, `block_storage`, `egress` and
`object_storage` are written by the fetchers but no rule validates them, so an
error there reaches the PR unflagged. For `aws` and `gcp` those blocks are also
derived from hardcoded source-currency constants that do not refresh, so they
drift from the providers' real prices over time. Review them by eye.

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
