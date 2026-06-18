"""
generate_manifest.py — ZENIT Performance Testing
Helper untuk generate dan append entry ke run_manifest.json.

Usage:
  # Append satu entry
  python generate_manifest.py append \\
    --scenario s01_browse --test-type load --condition C2 \\
    --run 1 --order rest-first --results-dir ../results/S1/LOAD

  # Validate manifest yang sudah ada
  python generate_manifest.py validate --manifest ../results/run_manifest.json \\
    --results-dir ../results/

  # List semua entry di manifest
  python generate_manifest.py list --manifest ../results/run_manifest.json

  # Generate template entry (tanpa append, hanya print)
  python generate_manifest.py template --scenario s01_browse --test-type soak \\
    --condition C2 --run 1 --order rest-first

Struktur file yang diharapkan di results_dir:
  RUN {N} R-T/  (rest-first)
  RUN {N} T-R/  (trpc-first)
    s{XX}_{scenario}_{side}_{test_type}_{timestamp}.json
    resource_{side}_{test_type}_{scenario}_{timestamp}.csv
    pgstats_{side}_{test_type}_{scenario}_{timestamp}.csv
    network_{side}_{test_type}_{scenario}_{timestamp}.csv
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCENARIO_CODES = {
    "s01_browse":   "s01",
    "s02_shopping": "s02",
    "s03_checkout": "s03",
    "s04_auth":     "s04",
    "s05_admin":    "s05",
}

CONDITION_MAP = {
    "C1": "rest",
    "C2": "C2",
    "C3": "C3",
    "C4": "C4",
}


def find_files_in_dir(dirpath: str, scenario: str, test_type: str) -> dict:
    """
    Auto-detect file timestamps dari direktori run.
    Return dict: {rest: {json, resource, pgstats, network}, trpc: {...}}
    """
    if not os.path.exists(dirpath):
        return {}

    sc = SCENARIO_CODES.get(scenario, scenario[:3])

    result = {"rest": {}, "trpc": {}}

    for fname in os.listdir(dirpath):
        fpath = fname  # relative

        # k6 JSON
        if fname.endswith(".json") and scenario in fname:
            if "_rest_" in fname:
                result["rest"]["json"] = fpath
            elif "_trpc_" in fname:
                result["trpc"]["json"] = fpath

        # resource CSV
        elif fname.startswith("resource_") and fname.endswith(".csv"):
            if "_rest_" in fname:
                result["rest"]["resource"] = fpath
            elif "_trpc_" in fname:
                result["trpc"]["resource"] = fpath

        # pgstats CSV
        elif fname.startswith("pgstats_") and fname.endswith(".csv"):
            if "_rest_" in fname:
                result["rest"]["pgstats"] = fpath
            elif "_trpc_" in fname:
                result["trpc"]["pgstats"] = fpath

        # network CSV
        elif fname.startswith("network_") and fname.endswith(".csv"):
            if "_rest_" in fname:
                result["rest"]["network"] = fpath
            elif "_trpc_" in fname:
                result["trpc"]["network"] = fpath

    return result


def make_entry(
    scenario: str,
    test_type: str,
    condition: str,
    run_num: int,
    order: str,
    base_path_prefix: str,
    files: dict | None = None,
    note: str = "",
    recorded_at: str | None = None,
) -> dict:
    """
    Buat satu manifest entry dict.

    Args:
        scenario:        e.g. "s01_browse"
        test_type:       "load" | "stress" | "spike" | "soak"
        condition:       "C1" | "C2" | "C3" | "C4"
        run_num:         nomor run (1–10 untuk load)
        order:           "rest-first" | "trpc-first"
        base_path_prefix: prefix path di dalam results_dir, e.g. "S1/LOAD/RUN 1 R-T"
        files:           dict dari find_files_in_dir (opsional)
        note:            catatan
        recorded_at:     ISO timestamp (default: now)
    """
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fp(side, file_type):
        """Build full relative path untuk manifest."""
        if files and files.get(side, {}).get(file_type):
            return f"{base_path_prefix}/{files[side][file_type]}"
        return None

    entry = {
        "run":             run_num,
        "scenario":        scenario,
        "test_type":       test_type,
        "condition":       condition,
        "execution_order": order,
        "recorded_at":     recorded_at,
        "rest":            fp("rest",  "json"),
        "trpc":            fp("trpc",  "json"),
        "rest_resource":   fp("rest",  "resource"),
        "trpc_resource":   fp("trpc",  "resource"),
        "rest_pgstats":    fp("rest",  "pgstats"),
        "trpc_pgstats":    fp("trpc",  "pgstats"),
        "rest_network":    fp("rest",  "network"),
        "trpc_network":    fp("trpc",  "network"),
        "note":            note,
    }
    return entry


def validate_manifest(manifest_path: str, results_dir: str) -> dict:
    """
    Validasi manifest:
      1. File yang di-reference exist?
      2. Counterbalancing terjaga per (scenario, test_type, condition)?
      3. Tidak ada duplicate (scenario, test_type, condition, run)?
      4. C3/C4 max 1 per (scenario, test_type)?
    """
    if not os.path.exists(manifest_path):
        return {"ok": False, "errors": [f"Manifest tidak ditemukan: {manifest_path}"]}

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    errors   = []
    warnings = []
    seen     = set()

    file_fields = ["rest", "trpc", "rest_resource", "trpc_resource",
                   "rest_pgstats", "trpc_pgstats", "rest_network", "trpc_network"]

    for i, entry in enumerate(manifest):
        scenario  = entry.get("scenario", "?")
        test_type = entry.get("test_type", "?")
        condition = entry.get("condition", "C2")
        run_num   = entry.get("run", i + 1)

        # Duplicate check
        key = (scenario, test_type, condition, run_num)
        if key in seen:
            errors.append(f"DUPLICATE: {key}")
        seen.add(key)

        # C3/C4 max 1 check
        if condition in ("C3", "C4"):
            c_count = sum(1 for e in manifest
                          if e.get("scenario") == scenario
                          and e.get("test_type") == test_type
                          and e.get("condition") == condition)
            if c_count > 1:
                warnings.append(f"C3/C4 lebih dari 1 run: {scenario}/{test_type}/{condition} "
                                 f"(N={c_count}) — pastikan ini disengaja")

        # File existence check
        for field in file_fields:
            fname = entry.get(field)
            if fname:
                full_path = os.path.join(results_dir, fname)
                if not os.path.exists(full_path):
                    errors.append(f"Run {run_num} {scenario}/{condition}: "
                                  f"{field} tidak ada: {full_path}")

    # Counterbalancing check per (scenario, test_type, condition) load group
    groups = {}
    for entry in manifest:
        k = (entry.get("scenario"), entry.get("test_type"), entry.get("condition", "C2"))
        groups.setdefault(k, []).append(entry.get("execution_order", "unknown"))

    for (sc, tt, cond), orders in groups.items():
        if tt != "load":
            continue
        rf = orders.count("rest-first")
        tf = orders.count("trpc-first")
        n  = len(orders)
        if n == 10 and rf != 5:
            warnings.append(f"Balance tidak 5:5: {sc}/{tt}/{cond} "
                            f"(rest-first={rf}, trpc-first={tf})")
        for j in range(1, len(orders)):
            if orders[j] == orders[j - 1] and orders[j] != "unknown":
                warnings.append(f"Consecutive same order run {j} dan {j+1}: "
                                 f"{sc}/{tt}/{cond} ({orders[j]})")

    return {
        "ok":       len(errors) == 0,
        "n_entries": len(manifest),
        "errors":   errors,
        "warnings": warnings,
    }


def list_manifest(manifest_path: str):
    """List semua entry di manifest."""
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"\nManifest: {manifest_path}")
    print(f"Total entries: {len(manifest)}\n")
    print(f"{'Run':<5} {'Scenario':<15} {'Type':<8} {'Cond':<5} {'Order':<12} {'Recorded':<22} Note")
    print("-" * 90)
    for entry in manifest:
        run       = entry.get("run", "?")
        scenario  = entry.get("scenario", "?")
        test_type = entry.get("test_type", "?")
        condition = entry.get("condition", "C2")
        order     = entry.get("execution_order", "?")
        recorded  = entry.get("recorded_at", "?")[:19]
        note      = entry.get("note", "")[:30]
        print(f"{str(run):<5} {scenario:<15} {test_type:<8} {condition:<5} {order:<12} {recorded:<22} {note}")


def cmd_append(args):
    manifest_path = os.path.abspath(args.manifest)
    results_dir   = os.path.abspath(args.results_dir) if args.results_dir else None

    # Load existing manifest
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = []
        print(f"Manifest baru akan dibuat: {manifest_path}")

    # Determine run folder name
    order_suffix = "R-T" if args.order == "rest-first" else "T-R"
    run_folder   = f"RUN {args.run} {order_suffix}"

    # Determine base_path_prefix
    sc_upper = args.scenario[:2].upper()  # "S1" dari "s01_browse"
    tt_upper = args.test_type.upper()     # "LOAD"
    base_prefix = f"S{int(args.scenario[1:3])}/{tt_upper}/{run_folder}"

    # Auto-detect files jika results_dir diberikan
    files = {}
    if results_dir:
        run_dir = os.path.join(results_dir, base_prefix)
        if os.path.exists(run_dir):
            files = find_files_in_dir(run_dir, args.scenario, args.test_type)
            if not any(files.get(s) for s in ("rest", "trpc")):
                print(f"⚠  Tidak ada file yang ditemukan di: {run_dir}")
        else:
            print(f"⚠  Direktori tidak ditemukan: {run_dir}")

    entry = make_entry(
        scenario        = args.scenario,
        test_type       = args.test_type,
        condition       = args.condition,
        run_num         = args.run,
        order           = args.order,
        base_path_prefix= base_prefix,
        files           = files,
        note            = args.note or f"Run {args.run} — {args.order}",
        recorded_at     = args.recorded_at,
    )

    manifest.append(entry)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"✓ Entry ditambahkan ke: {manifest_path}")
    print(f"  Run {args.run}: {args.scenario}/{args.test_type}/{args.condition} ({args.order})")
    print(f"  Total entries: {len(manifest)}")


def cmd_validate(args):
    result = validate_manifest(
        os.path.abspath(args.manifest),
        os.path.abspath(args.results_dir),
    )

    print(f"\nValidasi: {args.manifest}")
    print(f"Total entries: {result['n_entries']}")
    print(f"Status: {'✓ OK' if result['ok'] else '✗ ADA ERROR'}\n")

    if result["errors"]:
        print(f"ERRORS ({len(result['errors'])}):")
        for e in result["errors"]:
            print(f"  ✗ {e}")
    else:
        print("Tidak ada error file.")

    if result["warnings"]:
        print(f"\nWARNINGS ({len(result['warnings'])}):")
        for w in result["warnings"]:
            print(f"  ⚠  {w}")
    else:
        print("Tidak ada warning counterbalancing.")


def cmd_list(args):
    list_manifest(os.path.abspath(args.manifest))


def cmd_template(args):
    """Print template entry tanpa append."""
    order_suffix = "R-T" if args.order == "rest-first" else "T-R"
    run_folder   = f"RUN {args.run} {order_suffix}"
    sc_num       = int(args.scenario[1:3])
    base_prefix  = f"S{sc_num}/{args.test_type.upper()}/{run_folder}"

    entry = make_entry(
        scenario         = args.scenario,
        test_type        = args.test_type,
        condition        = args.condition,
        run_num          = args.run,
        order            = args.order,
        base_path_prefix = base_prefix,
        files            = None,
        note             = f"Run {args.run} — {args.order}",
    )
    print(json.dumps(entry, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ZENIT Manifest Helper")
    subparsers = parser.add_subparsers(dest="command")

    # --- append ---
    p_append = subparsers.add_parser("append", help="Append entry ke manifest")
    p_append.add_argument("--manifest",    default="../results/run_manifest.json")
    p_append.add_argument("--results-dir", default="../results/",
                          help="Root results dir (untuk auto-detect file)")
    p_append.add_argument("--scenario",    required=True,
                          choices=["s01_browse","s02_shopping","s03_checkout","s04_auth","s05_admin"])
    p_append.add_argument("--test-type",   required=True,
                          choices=["load","stress","spike","soak"])
    p_append.add_argument("--condition",   default="C2",
                          choices=["C1","C2","C3","C4"])
    p_append.add_argument("--run",         type=int, required=True)
    p_append.add_argument("--order",       required=True,
                          choices=["rest-first","trpc-first"])
    p_append.add_argument("--note",        default="")
    p_append.add_argument("--recorded-at", default=None,
                          help="ISO timestamp (default: now)")

    # --- validate ---
    p_val = subparsers.add_parser("validate", help="Validasi manifest")
    p_val.add_argument("--manifest",    default="../results/run_manifest.json")
    p_val.add_argument("--results-dir", default="../results/")

    # --- list ---
    p_list = subparsers.add_parser("list", help="List semua entry")
    p_list.add_argument("--manifest", default="../results/run_manifest.json")

    # --- template ---
    p_tmpl = subparsers.add_parser("template", help="Print template entry")
    p_tmpl.add_argument("--scenario",  required=True)
    p_tmpl.add_argument("--test-type", required=True)
    p_tmpl.add_argument("--condition", default="C2")
    p_tmpl.add_argument("--run",       type=int, required=True)
    p_tmpl.add_argument("--order",     required=True,
                        choices=["rest-first","trpc-first"])

    args = parser.parse_args()

    if args.command == "append":
        cmd_append(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "template":
        cmd_template(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
