#!/usr/bin/env python3
"""Project only strongly typed operations; native form contracts are bundled separately."""
import argparse
import copy
import json
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE.parents[1] / "openapi/telepilot.openapi.json"
OUTPUT = PACKAGE / "Sources/TelePilotCore/openapi.json"
OPERATIONS = {
    "/api/auth/csrf": ["get"], "/api/auth/login": ["post"],
    "/api/auth/logout": ["post"], "/api/auth/me": ["get"],
    "/api/accounts": ["get"], "/api/accounts/{aid}": ["get"],
    "/api/accounts/{aid}/pause": ["post"], "/api/accounts/{aid}/resume": ["post"],
    "/api/system/health-overview": ["get"], "/api/system/resource-dashboard": ["get"],
    "/api/system/version": ["get"],
}
READ_FIELDS = {
    "AccountSummary": {"id", "phone", "display_name", "tg_username", "status", "tags", "enabled_features", "created_at", "proxy"},
    "AccountDetail": {"id", "phone", "display_name", "tg_username", "status", "tags", "enabled_features", "created_at", "proxy", "notes"},
    "ProxySummary": {"label", "host", "port"},
    "HealthOverview": {"db", "redis", "alembic", "workers"},
    "DbStatus": {"ok", "error"}, "RedisStatus": {"ok", "error"}, "AlembicStatus": {"ok", "error"},
    "WorkersStatus": {"total", "by_status", "runtime_alive", "runtime_desired_running", "runtime_desired_running_alive", "runtime_failing"},
    "ResourceDashboard": {"host", "workers"},
    "HostResource": {"cpu_percent", "memory_used_percent", "disk_used_percent", "uptime_seconds"},
    "WorkerRuntimeResource": {"account_id", "alive", "desired", "fail_count"},
    "VersionInfo": {"version", "channel", "revision"},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = json.loads(SOURCE.read_text())
    for name, fields in READ_FIELDS.items():
        schema = source["components"]["schemas"][name]
        missing = fields - schema["properties"].keys()
        if missing:
            raise SystemExit(f"Consumed fields removed from {name}: {sorted(missing)}")
        schema["properties"] = {k: v for k, v in schema["properties"].items() if k in fields}
        if "required" in schema:
            schema["required"] = [k for k in schema["required"] if k in fields]
    document = {
        "openapi": source["openapi"], "info": source["info"],
        "paths": {path: {method: source["paths"][path][method] for method in methods}
                  for path, methods in OPERATIONS.items()},
        "components": {"schemas": {}, "securitySchemes": source["components"]["securitySchemes"]},
    }

    def include_refs(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in document["components"]["schemas"]:
                    schema = source["components"]["schemas"][name]
                    document["components"]["schemas"][name] = schema
                    include_refs(schema)
            for nested in value.values():
                include_refs(nested)
        elif isinstance(value, list):
            for nested in value:
                include_refs(nested)

    include_refs(document["paths"])

    def normalize(value):
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = copy.deepcopy(value)
        union = value.get("anyOf")
        if isinstance(union, list) and len(union) == 2 and any(branch.get("type") == "null" for branch in union):
            concrete = next(branch for branch in union if branch.get("type") != "null")
            if "$ref" in concrete:
                concrete = source["components"]["schemas"][concrete["$ref"].rsplit("/", 1)[1]]
            if isinstance(concrete.get("type"), str):
                value.pop("anyOf")
                value = {**copy.deepcopy(concrete), **value, "type": [concrete["type"], "null"]}
        return {k: normalize(v) for k, v in value.items()}

    document = normalize(document)
    if args.check:
        saved = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
        saved["info"] = document["info"]
        if saved != document:
            raise SystemExit("Native OpenAPI differs; run ios/TelePilotCore/scripts/sync-openapi.py")
    else:
        OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
