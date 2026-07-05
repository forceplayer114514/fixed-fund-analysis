import os
import shutil
import argparse
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

def main():
    parser = argparse.ArgumentParser(description="Cleanup historical funds data.")
    parser.add_argument("--funds", nargs="+", required=True, help="List of fund IDs to delete.")
    args = parser.parse_args()
    
    if not os.path.exists(REGISTRY_PATH):
        print(f"Registry not found at {REGISTRY_PATH}")
        return
        
    with open(REGISTRY_PATH, "r") as f:
        registry = yaml.safe_load(f) or {}
        
    for fund_id in args.funds:
        print(f"\n--- Cleaning up {fund_id} ---")
        # 1. Delete from registry
        if fund_id in registry:
            del registry[fund_id]
            print(f"Removed '{fund_id}' from registry.")
        else:
            print(f"'{fund_id}' not found in registry.")
        
        # 2. Delete raw data
        raw_path = os.path.join(BASE_DIR, "data", "raw", fund_id)
        if os.path.exists(raw_path):
            shutil.rmtree(raw_path)
            print(f"Deleted raw data: {raw_path}")
            
        # 3. Delete cleaned data
        cleaned_path = os.path.join(BASE_DIR, "data", "cleaned", fund_id)
        if os.path.exists(cleaned_path):
            shutil.rmtree(cleaned_path)
            print(f"Deleted cleaned data: {cleaned_path}")
            
    # Save updated registry
    with open(REGISTRY_PATH, "w") as f:
        yaml.safe_dump(registry, f, sort_keys=False)
        
    print("\nCleanup completed.")
        
if __name__ == "__main__":
    main()
