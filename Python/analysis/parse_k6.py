"""
parse_k6.py — ZENIT Performance Testing
Baca file JSON summary k6 dan ekstrak semua metrik terstruktur.

Metrik yang diekstrak:
  M-01  avg response time    → http_req_duration.values.avg
  M-02  median response time → http_req_duration.values.med
  M-03  P95 latency          → http_req_duration.values.p(95)
  M-04  P99 latency          → http_req_duration.values.p(99)
  M-05  throughput           → http_reqs.values.rate
  M-06  functional error     → functional_error_rate.values.rate
  M-07  SLA breach rate      → sla_breach_rate.values.rate
  M-08  payload size avg     → payload_size_bytes.values.avg
  M-09  HTTP request count   → http_reqs.values.count
  M-10  per-endpoint P95     → latency_* custom metrics

FIX (S04 compatibility — no re-run needed):
  S04 uses per-endpoint prefixed metric names instead of global ones,
  and uses "p(50)" + no "min" in summaryTrendStats.

  med_rt:            fallback to p(50) if "med" absent
  min_rt:            None for S04 (not in summaryTrendStats) — acceptable,
                     min_rt is not a primary hypothesis metric
  functional_error:  aggregate from s04_*_func_err_rate passes/fails
  sla_breach:        aggregate dari s04_me_sla_breach_rate + s04_refresh_sla_breach_rate
                     (login excluded: 96% breach adalah bcrypt CPU saturation, bukan protocol diff;
                      logout tidak di-track SLA — Rate metric-nya tidak didefinisikan di k6 script;
                      me+refresh adalah SLA-sensitive session endpoints yang relevan)
  payload_bytes:     None for S04 — auth responses have trivial payload,
                     not a primary comparison metric for this scenario
"""

import json
import os
from typing import Optional

# Pemetaan endpoint custom metrics per skenario
# Key = scenario name, Value = list nama custom metric di JSON
ENDPOINT_METRICS = {
    "s01_browse": [
        "latency_product_list",
        "latency_product_detail",
        "latency_product_search",
        "latency_product_filter",
    ],
    "s02_shopping": [
        "latency_login",       # LIM-07 FIX: declared & recorded di k6, sebelumnya tidak diekstrak
        "latency_browse",
        "latency_cart_get",
        "latency_cart_add",
        "latency_cart_update",
        "latency_cart_remove",
    ],
    "s03_checkout": [
        "latency_login",       # LIM-07 FIX: declared & recorded di k6, sebelumnya tidak diekstrak
        "latency_browse",
        "latency_cart_add",
        "latency_checkout",
        "latency_order_detail",
    ],
    "s04_auth": [
        "s04_login_duration",
        "s04_me_duration",
        "s04_refresh_duration",
        "s04_logout_duration",
    ],
    "s05_admin": [
        "latency_dashboard",
        "latency_admin_products",
        "latency_admin_orders",
        "latency_admin_users",
        "latency_product_create",
        "latency_product_update",
    ],
}


def _get(metrics: dict, key: str, stat: str) -> Optional[float]:
    """Safely get a stat from a metric entry."""
    entry = metrics.get(key)
    if entry is None:
        return None
    return entry.get("values", {}).get(stat)


def _aggregate_rate(metrics: dict, keys: list[str]) -> Optional[float]:
    """
    Aggregate multiple Rate metrics into one rate via passes/fails counts.
    Returns total_passes / (total_passes + total_fails), or None if no keys found.

    In k6 Rate metrics: add(True) → passes++, add(False) → fails++.
    For error rates: add(!isSuccess) → passes = error count, fails = ok count.
    So: aggregate_error_rate = sum(passes) / sum(passes + fails).
    """
    total_p = 0
    total_f = 0
    found   = False
    for key in keys:
        entry = metrics.get(key)
        if entry is None:
            continue
        vals = entry.get("values", {})
        p = vals.get("passes")
        f = vals.get("fails")
        if p is None or f is None:
            continue
        total_p += p
        total_f += f
        found = True
    if not found or (total_p + total_f) == 0:
        return None
    return total_p / (total_p + total_f)


# S04-specific metric name mappings (per-endpoint prefixed, no global aggregate)
_S04_FUNC_ERR_KEYS  = [
    "s04_login_func_err_rate",
    "s04_me_func_err_rate",
    "s04_refresh_func_err_rate",
    "s04_logout_func_err_rate",
]
# Login SLA breach excluded from aggregate tapi tetap ditrack secara individual
# karena 96% breach mencerminkan bcrypt CPU saturation, bukan perbedaan protokol.
# Logout: s04_logout_sla_breach_rate Rate metric TIDAK didefinisikan di k6 script
# (intentional — checkAndRecord dipanggil dengan slaBreachRate=null untuk logout).
# Aggregate SLA hanya dari me+refresh — keduanya adalah SLA-sensitive session endpoints.
_S04_SLA_BREACH_KEYS = [
    "s04_me_sla_breach_rate",
    "s04_refresh_sla_breach_rate",
]


def parse_k6(filepath: str, scenario: Optional[str] = None) -> dict:
    """
    Parse satu file JSON summary k6.

    Args:
        filepath: path ke file .json output k6
        scenario: nama skenario (misal 's01_browse') untuk extract endpoint metrics.
                  Kalau None, endpoint metrics di-skip.

    Returns:
        dict berisi semua metrik yang berhasil diekstrak.
        Nilai None berarti metrik tidak tersedia di file ini.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File tidak ditemukan: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    metrics = data.get("metrics", {})
    setup = data.get("setup_data", {})
    state = data.get("state", {})

    result = {
        # Metadata
        "api_type":    setup.get("apiType"),
        "test_type":   setup.get("testType"),
        "start_time":  setup.get("startTime"),
        "duration_ms": state.get("testRunDurationMs"),

        # M-01: Avg response time (ms)
        "avg_rt": _get(metrics, "http_req_duration", "avg"),

        # M-02: Median / P50 (ms)
        # FIX S04: s04_auth uses "p(50)" key instead of "med" in summaryTrendStats.
        # Fallback ensures med_rt is populated for all scenarios without re-run.
        "med_rt": (
            _get(metrics, "http_req_duration", "med")
            or _get(metrics, "http_req_duration", "p(50)")
        ),

        # M-03: P95 latency (ms) — METRIK PRIMER
        "p95": _get(metrics, "http_req_duration", "p(95)"),

        # M-04: P99 latency (ms)
        "p99": _get(metrics, "http_req_duration", "p(99)"),

        # M-05: Throughput (req/s) — METRIK PRIMER
        "throughput": _get(metrics, "http_reqs", "rate"),

        # M-06: Functional error rate (0–1)
        # FIX S04: s04_auth uses per-endpoint prefixed rates (s04_login_func_err_rate, etc.)
        # instead of a global functional_error_rate. Aggregate via passes/fails counts.
        # For other scenarios, global metric is used directly (no change).
        #
        # BUG-OR FIX: pakai "is not None" bukan "or" karena rate=0.0 adalah nilai
        # valid (tidak ada error) — 0.0 falsy sehingga "x or y" salah skip x=0.
        # Sebelumnya: _get(...)=0 → 0 or _aggregate(S04_keys)=None → None (BUG).
        # Sekarang: _get(...)=0 → is not None → return 0.0 (BENAR).
        "functional_error_rate": (
            _get(metrics, "functional_error_rate", "rate")
            if _get(metrics, "functional_error_rate", "rate") is not None
            else _aggregate_rate(metrics, _S04_FUNC_ERR_KEYS)
        ),

        # M-07: SLA breach rate (0–1)
        # FIX S04: same pattern — aggregate from me+refresh sla breach rates.
        # Login excluded: 96% breach is bcrypt CPU saturation, not protocol diff.
        #
        # BUG-OR FIX: same as M-06 — "is not None" prevents sla=0.0 from
        # being treated as falsy and falling through to S04 aggregate.
        "sla_breach_rate": (
            _get(metrics, "sla_breach_rate", "rate")
            if _get(metrics, "sla_breach_rate", "rate") is not None
            else _aggregate_rate(metrics, _S04_SLA_BREACH_KEYS)
        ),

        # M-08: Payload size avg (bytes)
        # S04 does not define payload_size_bytes — auth responses are trivial in size
        # and payload is not a primary comparison metric for the auth scenario.
        # Remains None for S04; no re-run needed.
        "payload_size_bytes": _get(metrics, "payload_size_bytes", "avg"),

        # M-09: Total HTTP request count
        "http_req_count": _get(metrics, "http_reqs", "count"),

        # Tambahan untuk kelengkapan
        "p90": _get(metrics, "http_req_duration", "p(90)"),
        # min_rt: None for S04 (not in summaryTrendStats) — not a hypothesis metric
        "min_rt": _get(metrics, "http_req_duration", "min"),
        "max_rt": _get(metrics, "http_req_duration", "max"),

        # Checks summary
        "checks_pass_rate": _get(metrics, "checks", "rate"),

        # Endpoint metrics (M-10)
        "endpoints": {},
    }

    # Extract per-endpoint P95 latency
    if scenario and scenario in ENDPOINT_METRICS:
        for metric_name in ENDPOINT_METRICS[scenario]:
            p95_val = _get(metrics, metric_name, "p(95)")
            avg_val = _get(metrics, metric_name, "avg")
            result["endpoints"][metric_name] = {
                "p95": p95_val,
                "avg": avg_val,
                "p99": _get(metrics, metric_name, "p(99)"),
            }

    return result


def parse_k6_pair(
    rest_path: str,
    trpc_path: str,
    scenario: Optional[str] = None,
) -> tuple[dict, dict]:
    """
    Parse sepasang file k6 (REST dan tRPC) sekaligus.

    Returns:
        (rest_metrics, trpc_metrics)
    """
    rest = parse_k6(rest_path, scenario=scenario)
    trpc = parse_k6(trpc_path, scenario=scenario)
    return rest, trpc


if __name__ == "__main__":
    # Quick test dengan data Run 1
    import sys

    results_dir = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"

    rest_file = os.path.join(results_dir, "s01_browse_rest_load_1778479572.json")
    trpc_file = os.path.join(results_dir, "s01_browse_trpc_load_1778483339.json")

    rest, trpc = parse_k6_pair(rest_file, trpc_file, scenario="s01_browse")

    print("=== PARSE K6 — Run 1 S01 Browse ===\n")
    print(f"{'Metrik':<28} {'REST':>12} {'tRPC':>12} {'Δ (ms/%)'}")
    print("-" * 60)

    metrics_display = [
        ("avg_rt (ms)",        "avg_rt"),
        ("p95 (ms)",           "p95"),
        ("p99 (ms)",           "p99"),
        ("throughput (req/s)", "throughput"),
        ("func_error_rate",    "functional_error_rate"),
        ("sla_breach_rate",    "sla_breach_rate"),
        ("payload_avg (bytes)","payload_size_bytes"),
        ("http_req_count",     "http_req_count"),
    ]

    for label, key in metrics_display:
        rv = rest.get(key)
        tv = trpc.get(key)
        if rv is not None and tv is not None:
            delta = rv - tv
            print(f"{label:<28} {rv:>12.3f} {tv:>12.3f} {delta:>+10.3f}")
        else:
            print(f"{label:<28} {'N/A':>12} {'N/A':>12}")

    print("\n--- Endpoint Metrics (P95 ms) ---")
    for ep, vals in rest["endpoints"].items():
        tv = trpc["endpoints"].get(ep, {})
        rv95 = vals.get("p95")
        tv95 = tv.get("p95")
        if rv95 and tv95:
            print(f"  {ep:<35} REST={rv95:.2f}  tRPC={tv95:.2f}  Δ={rv95-tv95:+.2f}")

    print(f"\nDurasi REST: {rest['duration_ms']/1000/60:.1f} menit")
    print(f"Durasi tRPC: {trpc['duration_ms']/1000/60:.1f} menit")