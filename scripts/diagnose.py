#!/usr/bin/env python3
"""Print only the producer's allowlisted account metadata, never raw RPC data."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def main() -> int:
    try:
        path = Path(__file__).resolve().parents[1] / "runcat-neo-hook.py"
        spec = importlib.util.spec_from_file_location("runcat_diagnostic_hook", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Producer unavailable")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        data = hook.fetch_account_data()
        print(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception:
        print("RunCat diagnostic: account query failed; no raw response was printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
