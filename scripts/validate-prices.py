#!/usr/bin/env python3
"""
Validate price JSON files against the schema.
Exit code 0 = all valid, 1 = validation errors found.
"""

import json
import os
import sys
from pathlib import Path


def load_schema(schema_path: str) -> dict:
    """Load the JSON schema."""
    with open(schema_path) as f:
        return json.load(f)


def validate_instance(instance: dict, index: int, required_keys: list) -> list:
    """Validate a single instance object."""
    errors = []

    for key in required_keys:
        if key not in instance:
            errors.append(f"instances[{index}]: missing required key '{key}'")

    # Type validations
    if 'vcpu' in instance and not isinstance(instance['vcpu'], (int, float)):
        errors.append(f"instances[{index}]: 'vcpu' must be a number")

    if 'ram_gb' in instance and not isinstance(instance['ram_gb'], (int, float)):
        errors.append(f"instances[{index}]: 'ram_gb' must be a number")

    if 'price_monthly' in instance:
        if not isinstance(instance['price_monthly'], (int, float)):
            errors.append(f"instances[{index}]: 'price_monthly' must be a number")
        elif instance['price_monthly'] < 0:
            errors.append(f"instances[{index}]: 'price_monthly' cannot be negative")

    if 'currency' in instance and instance['currency'] not in ['EUR', 'USD']:
        errors.append(f"instances[{index}]: 'currency' must be 'EUR' or 'USD'")

    if 'architecture' in instance and instance['architecture'] not in ['x86', 'arm64']:
        errors.append(f"instances[{index}]: 'architecture' must be 'x86' or 'arm64'")

    return errors


def validate_price_file(filepath: str, schema: dict) -> list:
    """Validate a price file against the schema."""
    errors = []

    # Load the file
    try:
        with open(filepath) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]

    # Check required top-level keys
    required_top = schema.get('required', [])
    for key in required_top:
        if key not in data:
            errors.append(f"Missing required top-level key: '{key}'")

    # Validate provider matches filename
    filename = Path(filepath).stem
    if 'provider' in data and data['provider'] != filename:
        errors.append(f"'provider' ({data['provider']}) doesn't match filename ({filename})")

    # Validate instances array
    if 'instances' in data:
        if not isinstance(data['instances'], list):
            errors.append("'instances' must be an array")
        else:
            instance_schema = schema.get('$defs', {}).get('instance', {})
            required_instance_keys = instance_schema.get('required', [])

            for i, instance in enumerate(data['instances']):
                errors.extend(validate_instance(instance, i, required_instance_keys))

    # Validate control_plane_cost if present
    if 'control_plane_cost' in data:
        val = data['control_plane_cost']
        if val is not None and not isinstance(val, (int, float)):
            errors.append("'control_plane_cost' must be a number or null")
        elif isinstance(val, (int, float)) and val < 0:
            errors.append("'control_plane_cost' cannot be negative")

    # Validate storage pricing keys
    for storage_key in ['block_storage', 'object_storage']:
        if storage_key in data:
            storage = data[storage_key]
            if 'price_per_gb_monthly' in storage:
                val = storage['price_per_gb_monthly']
                if not isinstance(val, (int, float)) or val < 0:
                    errors.append(f"'{storage_key}.price_per_gb_monthly' must be a non-negative number")

    return errors


def main():
    """Main entry point."""
    prices_dir = Path(__file__).parent.parent / 'prices'
    schema_path = prices_dir / 'schema.json'

    if not schema_path.exists():
        print(f"❌ Schema file not found: {schema_path}")
        sys.exit(1)

    schema = load_schema(schema_path)

    # Find all price files (exclude schema.json)
    price_files = sorted([
        f for f in prices_dir.glob('*.json')
        if f.name != 'schema.json'
    ])

    if not price_files:
        print("❌ No price files found")
        sys.exit(1)

    print(f"Validating {len(price_files)} price files against schema...\n")

    total_errors = 0
    valid_count = 0

    for filepath in price_files:
        errors = validate_price_file(str(filepath), schema)

        if errors:
            print(f"❌ {filepath.name}")
            for error in errors:
                print(f"   - {error}")
            total_errors += len(errors)
        else:
            print(f"✅ {filepath.name}")
            valid_count += 1

    print(f"\n{'─' * 40}")
    print(f"Results: {valid_count}/{len(price_files)} files valid")

    if total_errors > 0:
        print(f"Total errors: {total_errors}")
        sys.exit(1)
    else:
        print("All validations passed!")
        sys.exit(0)


if __name__ == '__main__':
    main()
