"""
parse_resources.py — ZENIT Performance Testing
Baca file CSV resource monitoring dan ekstrak metrik utilisasi server.

Kolom CSV (dari monitor_resources.sh):
  timestamp, cpu_total_pct, mem_used_mb, mem_total_mb,
  pg_active, pg_idle, pg_cache_hit_ratio, pg_tps_delta,
  target_pid_cpu_pct, target_pid_mem_mb

Metrik yang diekstrak:
  M-11  CPU backend spesifik (%)     → target_pid_cpu_pct
  M-12  RAM backend spesifik (MB)    → target_pid_mem_mb
  M-14  DB connections aktif         → pg_active
        DB cache hit ratio (%)       → pg_cache_hit_ratio
        DB TPS delta                 → pg_tps_delta

BUG-05 FIX: Per-row parsing yang robust.
Sebelumnya: satu kolom missing/invalid pada satu row → seluruh row di-skip via
try/except catch-all. Jika banyak row terkena (misal target_pid_cpu_pct="N/A"
karena PID tidak ditemukan monitoring script), semua resource metrics = None.

Fix: parse kolom satu per satu dengan fallback 0.0, skip row hanya jika
timestamp tidak ada (baris corrupt total). Nilai non-numeric per-kolom
di-replace dengan NaN yang kemudian di-filter saat komputasi mean.
"""

import csv
import math
import os


def _safe_float(value, fallback=None):
    """
    Convert value ke float. Return fallback jika gagal.
    fallback=None menyimpan info bahwa kolom ini invalid (untuk filtering).
    fallback=0.0 dipakai untuk kolom yang boleh default ke nol.
    """
    if value is None:
        return fallback
    try:
        f = float(str(value).strip())
        if math.isnan(f) or math.isinf(f):
            return fallback
        return f
    except (ValueError, TypeError):
        return fallback


def _mean_valid(vals: list) -> float | None:
    """Mean dari nilai yang tidak None dan tidak NaN."""
    valid = [v for v in vals if v is not None and not math.isnan(v)]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _max_valid(vals: list) -> float | None:
    valid = [v for v in vals if v is not None and not math.isnan(v)]
    return max(valid) if valid else None


def _std_valid(vals: list) -> float:
    valid = [v for v in vals if v is not None and not math.isnan(v)]
    if len(valid) < 2:
        return 0.0
    m = sum(valid) / len(valid)
    return (sum((v - m) ** 2 for v in valid) / len(valid)) ** 0.5


def parse_resources(filepath: str, mode: str = "mean") -> dict:
    """
    Parse file CSV resource monitoring.

    Args:
        filepath: path ke file CSV
        mode: 'mean'   → return scalar mean per metrik
              'series' → return full time-series list

    BUG-05 FIX: Robust per-row parsing — kolom invalid di-replace None,
    bukan skip seluruh row.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File tidak ditemukan: {filepath}")

    rows = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip jika tidak ada timestamp sama sekali (baris corrupt total)
            ts = row.get("timestamp", "").strip()
            if not ts:
                continue

            # BUG-05 FIX: Parse kolom satu per satu dengan fallback None
            # Ini memastikan row tetap masuk meski beberapa kolom invalid
            rows.append({
                "timestamp":          ts,
                # System-wide metrics — fallback 0.0 (selalu ada jika script berjalan)
                "cpu_total_pct":      _safe_float(row.get("cpu_total_pct"),      0.0),
                "mem_used_mb":        _safe_float(row.get("mem_used_mb"),        0.0),
                "mem_total_mb":       _safe_float(row.get("mem_total_mb"),       0.0),
                # DB metrics — fallback 0.0
                "pg_active":          _safe_float(row.get("pg_active"),          0.0),
                "pg_idle":            _safe_float(row.get("pg_idle"),            0.0),
                "pg_cache_hit_ratio": _safe_float(row.get("pg_cache_hit_ratio"), 0.0),
                "pg_tps_delta":       _safe_float(row.get("pg_tps_delta"),       0.0),
                # PID-specific metrics — fallback None (mungkin tidak tersedia)
                # None difilter saat komputasi mean sehingga tidak bias hasil
                "target_pid_cpu_pct": _safe_float(row.get("target_pid_cpu_pct"), None),
                "target_pid_mem_mb":  _safe_float(row.get("target_pid_mem_mb"),  None),
            })

    if not rows:
        raise ValueError(f"Tidak ada data valid di file: {filepath}")

    n = len(rows)

    if mode == "series":
        return {
            "mode":       "series",
            "n_samples":  n,
            "timestamps": [r["timestamp"] for r in rows],
            "cpu_pct":    [r["target_pid_cpu_pct"] for r in rows],
            "mem_mb":     [r["target_pid_mem_mb"] for r in rows],
            "pg_active":  [r["pg_active"] for r in rows],
            "pg_cache_hit_ratio": [r["pg_cache_hit_ratio"] for r in rows],
            "pg_tps_delta":       [r["pg_tps_delta"] for r in rows],
            "cpu_total_pct":      [r["cpu_total_pct"] for r in rows],
            "mem_used_mb":        [r["mem_used_mb"] for r in rows],
        }

    # mode == "mean"
    cpu_vals   = [r["target_pid_cpu_pct"] for r in rows]
    mem_vals   = [r["target_pid_mem_mb"]  for r in rows]
    pgact_vals = [r["pg_active"] for r in rows]
    pghit_vals = [r["pg_cache_hit_ratio"] for r in rows]
    pgtps_vals = [r["pg_tps_delta"] for r in rows]

    # Hitung berapa banyak baris yang tidak punya PID data
    n_pid_missing = sum(1 for v in cpu_vals if v is None)
    pid_coverage  = (n - n_pid_missing) / n if n > 0 else 0.0

    return {
        "mode":      "mean",
        "n_samples": n,
        "n_pid_missing": n_pid_missing,
        "pid_coverage_pct": pid_coverage * 100,

        # M-11: CPU backend spesifik (%)
        # _mean_valid skip None (baris dimana PID tidak ditemukan)
        "cpu_pct_mean": _mean_valid(cpu_vals),
        "cpu_pct_std":  _std_valid(cpu_vals),
        "cpu_pct_max":  _max_valid(cpu_vals),

        # M-12: RAM backend spesifik (MB)
        "mem_mb_mean": _mean_valid(mem_vals),
        "mem_mb_std":  _std_valid(mem_vals),
        "mem_mb_max":  _max_valid(mem_vals),

        # M-14: DB connections aktif
        "pg_active_mean": _mean_valid(pgact_vals),
        "pg_active_max":  _max_valid(pgact_vals),

        # DB cache hit ratio dan TPS
        "pg_cache_hit_mean": _mean_valid(pghit_vals),
        "pg_tps_mean":       _mean_valid(pgtps_vals),
    }


def parse_resources_pair(
    rest_path: str,
    trpc_path: str,
    mode: str = "mean",
) -> tuple[dict, dict]:
    rest = parse_resources(rest_path, mode=mode)
    trpc = parse_resources(trpc_path, mode=mode)
    return rest, trpc


if __name__ == "__main__":
    import sys

    results_dir = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"

    rest_path = os.path.join(results_dir, "resource_rest_load_s01_1778479626.csv")
    trpc_path = os.path.join(results_dir, "resource_trpc_load_s01_1778483426.csv")

    rest, trpc = parse_resources_pair(rest_path, trpc_path, mode="mean")

    print("=== PARSE RESOURCES ===\n")
    print(f"Samples REST: {rest['n_samples']}  |  tRPC: {trpc['n_samples']}")
    print(f"PID coverage REST: {rest.get('pid_coverage_pct', 0):.1f}%  "
          f"|  tRPC: {trpc.get('pid_coverage_pct', 0):.1f}%\n")

    display = [
        ("CPU mean (%)",      "cpu_pct_mean"),
        ("CPU max (%)",       "cpu_pct_max"),
        ("RAM mean (MB)",     "mem_mb_mean"),
        ("RAM max (MB)",      "mem_mb_max"),
        ("DB active (mean)",  "pg_active_mean"),
        ("Cache hit % (mean)","pg_cache_hit_mean"),
        ("DB TPS (mean)",     "pg_tps_mean"),
    ]

    print(f"{'Metrik':<25} {'REST':>10} {'tRPC':>10} {'Δ':>10}")
    print("-" * 55)
    for label, key in display:
        rv = rest.get(key)
        tv = trpc.get(key)
        if rv is not None and tv is not None:
            print(f"{label:<25} {rv:>10.2f} {tv:>10.2f} {rv - tv:>+10.2f}")
        else:
            rv_s = f"{rv:.2f}" if rv is not None else "N/A"
            tv_s = f"{tv:.2f}" if tv is not None else "N/A"
            print(f"{label:<25} {rv_s:>10} {tv_s:>10}")