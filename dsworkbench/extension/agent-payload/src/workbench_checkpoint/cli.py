"""CLI diagnosis Checkpoint Runner.

Bukan pengganti tombol UI. Berguna untuk membuktikan parser dan argv tanpa
menyalakan seluruh Control API.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import CheckpointRequest
from .runner import RegistryCheckpointRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="workbench-checkpoint",
        description="Jalankan satu checkpoint lewat handler allowlist.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="jalankan handler terhadap workspace lokal")
    run_p.add_argument("--handler", required=True, choices=("python-check", "sql-check"))
    run_p.add_argument("--artifact", required=True)
    run_p.add_argument("--package-root", required=True, type=Path)
    run_p.add_argument("--workspace-root", required=True, type=Path)
    run_p.add_argument("--timeout", type=int, default=180)
    run_p.add_argument("--module-dir", default=None, help="python-check: moduleDir")
    run_p.add_argument("--checksum-ref", default=None)
    run_p.add_argument("--run-notebook", action="store_true")
    run_p.add_argument("--service", default=None, help="sql-check: alias service")
    run_p.add_argument("--database", default=None, help="sql-check: nama database")
    run_p.add_argument("--endpoint", default=None, help="sql-check: host:port service")
    run_p.add_argument("--json", action="store_true", dest="as_json")

    handlers_p = sub.add_parser("handlers", help="daftar handler allowlist")
    handlers_p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)
    if args.cmd == "handlers":
        runner = RegistryCheckpointRunner()
        data = list(runner.known_handlers)
        if args.as_json:
            json.dump({"handlers": data}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            for name in data:
                print(name)
        return 0

    params: dict = {}
    endpoints: dict = {}
    if args.handler == "python-check":
        if not args.module_dir:
            print("error: --module-dir wajib untuk python-check", file=sys.stderr)
            return 2
        params["moduleDir"] = args.module_dir
        if args.checksum_ref:
            params["checksumRef"] = args.checksum_ref
        params["runNotebook"] = bool(args.run_notebook)
    else:
        if not args.service or not args.database:
            print("error: --service dan --database wajib untuk sql-check", file=sys.stderr)
            return 2
        params["service"] = args.service
        params["database"] = args.database
        if args.endpoint:
            endpoints[args.service] = args.endpoint

    request = CheckpointRequest(
        handler=args.handler,
        artifact=args.artifact,
        timeout_seconds=args.timeout,
        params=params,
        workspace_root=str(args.workspace_root.resolve()),
        package_root=str(args.package_root.resolve()),
        runtime_endpoints=endpoints,
    )
    result = RegistryCheckpointRunner().run(request)
    if args.as_json:
        json.dump(result.to_public(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"[{result.status.value}] {result.summary}")
        for f in result.findings:
            extra = f" — {f.detail}" if f.detail else ""
            print(f"  [{f.status.value}] {f.title}{extra}")
    return 0 if result.passed or result.status.value == "LEWAT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
