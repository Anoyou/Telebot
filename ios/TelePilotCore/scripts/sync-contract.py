#!/usr/bin/env python3
"""Bundle server-owned form contracts without embedding server values or credentials."""
import argparse
import json
from pathlib import Path

package = Path(__file__).resolve().parents[1]
source = package.parents[1] / "openapi/telepilot.openapi.json"
output = package / "Sources/TelePilotCore/contract.json"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
document = json.loads(source.read_text())
contract = {
    "paths": {
        path: {method: {
            key: operation[key] for key in ("summary", "parameters", "requestBody") if key in operation
        } for method, operation in operations.items() if method in {"get", "post", "put", "patch", "delete"}}
        for path, operations in document["paths"].items() if path.startswith("/api/")
    },
    "components": {"schemas": document["components"]["schemas"]},
}
rendered = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
if args.check:
    if not output.exists() or output.read_text() != rendered:
        raise SystemExit("Native form contract differs; run ios/TelePilotCore/scripts/sync-contract.py")
else:
    output.write_text(rendered)
