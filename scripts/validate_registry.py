import os
import sys
import yaml
from collections import defaultdict
from pydantic import ValidationError
from registry_schema import FundEntrySchema

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_registry(filepath: str) -> dict:
    if not os.path.exists(filepath):
        print(f"Error: Registry file not found at {filepath}", file=sys.stderr)
        sys.exit(1)

    with open(filepath, 'r', encoding='utf-8') as f:
        registry = yaml.safe_load(f)

    if not registry:
        print(f"Error: Registry file is empty at {filepath}", file=sys.stderr)
        sys.exit(1)

    return registry

def validate_registry(registry_data: dict) -> list[str]:
    errors = []
    apir_seen = {}  # 记录已见过的 APIR 代码，用于查重: apir_code -> fund_id

    for fund_id, fund_data in registry_data.items():
        try:
            # Pydantic 校验单个条目格式
            validated_entry = FundEntrySchema(**fund_data)

            # 全局业务规则校验：APIR 不能重复
            apir_code = validated_entry.apir_code
            if apir_code in apir_seen:
                duplicate_fund_id = apir_seen[apir_code]
                errors.append(
                    f"[{fund_id}/{apir_code}] apir_code: Duplicate APIR code. Already used by fund '{duplicate_fund_id}'."
                )
            else:
                apir_seen[apir_code] = fund_id

        except ValidationError as e:
            # 收集该基金下所有的验证错误
            apir_code_fallback = fund_data.get('apir_code', 'UNKNOWN_APIR')
            for error in e.errors():
                loc = ".".join(map(str, error['loc']))
                msg = error['msg']
                errors.append(f"[{fund_id}/{apir_code_fallback}] {loc}: {msg}")
        except Exception as e:
            apir_code_fallback = fund_data.get('apir_code', 'UNKNOWN_APIR')
            errors.append(f"[{fund_id}/{apir_code_fallback}] Unexpected error: {str(e)}")

    return errors

def main():
    registry_path = os.path.join(BASE_DIR, "references", "fund_registry.yaml")
    print("Validating fund_registry.yaml...")

    registry_data = load_registry(registry_path)
    errors = validate_registry(registry_data)

    if errors:
        print("\nCRITICAL: Registry validation failed with the following errors:\n", file=sys.stderr)
        for err in errors:
            print(f" - {err}", file=sys.stderr)
        print(f"\nTotal errors found: {len(errors)}", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: Registry validation passed.")
    sys.exit(0)

if __name__ == "__main__":
    main()
