"""
parse_pgstats.py — ZENIT Performance Testing
Baca file CSV pg_stat_statements dan ekstrak metrik query database.

Kolom CSV:
  query, calls, avg_ms, total_ms, rows

Metrik yang diekstrak:
  M-15  DB Query Time avg_ms per query → top queries by total_ms
        Aggregate avg_ms (weighted mean) → satu nilai per run

Tujuan: mengisolasi apakah bottleneck ada di layer API atau layer DB.
Jika avg_ms query DB identik antara REST dan tRPC tapi latency berbeda,
bottleneck ada di lapisan protokol/auth.
"""

import csv
import os


def parse_pgstats(filepath: str, top_n: int = 10) -> dict:
    """
    Parse file CSV pg_stat_statements.

    Args:
        filepath: path ke file CSV
        top_n:    jumlah query teratas yang dikembalikan (berdasar total_ms)

    Returns:
        dict berisi:
          - weighted_avg_ms: rata-rata avg_ms berbobot total_calls (M-15 agregat)
          - total_calls:     jumlah total query calls
          - top_queries:     list top_n query berdasarkan total_ms
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File tidak ditemukan: {filepath}")

    queries = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                calls    = int(float(row["calls"]))
                avg_ms   = float(row["avg_ms"])
                total_ms = float(row["total_ms"])
                rows_val = int(float(row["rows"])) if row.get("rows") else 0
                query    = row["query"].strip()

                if calls <= 0:
                    continue

                queries.append({
                    "query":    query,
                    "calls":    calls,
                    "avg_ms":   avg_ms,
                    "total_ms": total_ms,
                    "rows":     rows_val,
                })
            except (ValueError, KeyError):
                continue

    if not queries:
        raise ValueError(f"Tidak ada data valid di file: {filepath}")

    total_calls = sum(q["calls"] for q in queries)
    total_ms_all = sum(q["total_ms"] for q in queries)

    # Weighted mean avg_ms = total waktu semua query / total calls
    weighted_avg_ms = total_ms_all / total_calls if total_calls > 0 else 0.0

    # Sort by total_ms descending — query paling banyak konsumsi waktu
    top_queries = sorted(queries, key=lambda q: q["total_ms"], reverse=True)[:top_n]

    # Trucate query string untuk display
    for q in top_queries:
        raw = q["query"]
        q["query_short"] = (raw[:80] + "...") if len(raw) > 80 else raw

    return {
        "n_queries":       len(queries),
        "total_calls":     total_calls,
        "total_ms":        total_ms_all,
        "weighted_avg_ms": weighted_avg_ms,  # M-15 — nilai utama untuk paired analysis
        "top_queries":     top_queries,
    }


def parse_pgstats_pair(
    rest_path: str,
    trpc_path: str,
    top_n: int = 10,
) -> tuple[dict, dict]:
    """
    Parse sepasang CSV pgstats (REST dan tRPC) sekaligus.

    Returns:
        (rest_pgstats, trpc_pgstats)
    """
    rest = parse_pgstats(rest_path, top_n=top_n)
    trpc = parse_pgstats(trpc_path, top_n=top_n)
    return rest, trpc


if __name__ == "__main__":
    import sys

    results_dir = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"

    rest_path = os.path.join(results_dir, "pgstats_rest_load_s01_1778479572.csv")
    trpc_path = os.path.join(results_dir, "pgstats_trpc_load_s01_1778483339.csv")

    rest, trpc = parse_pgstats_pair(rest_path, trpc_path, top_n=5)

    print("=== PARSE PGSTATS — Run 1 S01 Browse ===\n")
    print(f"{'':30} {'REST':>12} {'tRPC':>12}")
    print("-" * 55)
    print(f"{'Total queries jenis':<30} {rest['n_queries']:>12} {trpc['n_queries']:>12}")
    print(f"{'Total calls':<30} {rest['total_calls']:>12,} {trpc['total_calls']:>12,}")
    print(f"{'Weighted avg_ms (M-15)':<30} {rest['weighted_avg_ms']:>12.3f} {trpc['weighted_avg_ms']:>12.3f}")
    delta = rest['weighted_avg_ms'] - trpc['weighted_avg_ms']
    print(f"{'Δ weighted_avg_ms':<30} {delta:>+12.3f}")

    print("\n--- Top 5 Queries REST (by total_ms) ---")
    for i, q in enumerate(rest["top_queries"][:5], 1):
        print(f"  {i}. calls={q['calls']:,}  avg={q['avg_ms']:.1f}ms  total={q['total_ms']/1000:.1f}s")
        print(f"     {q['query_short']}")

    print("\n--- Top 5 Queries tRPC (by total_ms) ---")
    for i, q in enumerate(trpc["top_queries"][:5], 1):
        print(f"  {i}. calls={q['calls']:,}  avg={q['avg_ms']:.1f}ms  total={q['total_ms']/1000:.1f}s")
        print(f"     {q['query_short']}")
