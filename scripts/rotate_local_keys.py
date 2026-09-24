"""Rotate both capability keys while the local API is stopped; never print keys."""
from pathlib import Path
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.hyh.db import DB_PATH
from src.hyh.security import credential_path, process_ownership, write_new_credentials


def main():
    if "IDM_ADMIN_TOKEN" in os.environ or "IDM_COLLECTOR_TOKEN" in os.environ:
        raise RuntimeError("Environment keys are configured; rotate both environment values instead.")
    with process_ownership(DB_PATH):
        path = credential_path(DB_PATH)
        write_new_credentials(path, replace=True)
    print(f"Keys rotated: {path}. Restart the service and reconnect both clients.")


if __name__ == "__main__":
    main()
