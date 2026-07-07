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
