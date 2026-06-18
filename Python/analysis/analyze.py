"""
analyze.py — ZENIT Performance Testing

FIXES (cumulative):
  BUG-01-C3C4: C3/C4 groups dapat extract trpc-only values tanpa REST partner.
               Sebelumnya: pairs kosong → semua metrik no_data → decomposition gagal.
  BUG-02-N11:  Duplicate run number detection sebelum analisis dimulai.
               Re-run yang di-append tanpa hapus entry lama akan terdeteksi.
  ISS-09:      Counterbalancing check diperluas ke stress/spike (N=3).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from parse_k6 import parse_k6
from parse_resources import parse_resources
from parse_pgstats import parse_pgstats
from parse_network import parse_network
from stats import (
    analyze_load, analyze_exploratory, descriptive, difference_scores,
    analyze_decomposition_c3, analyze_decomposition_c4,
)


# ---------------------------------------------------------------------------
# METRIC EXTRACTORS
# ---------------------------------------------------------------------------

def build_metric_extractors() -> list[tuple]:
    """
    Return list of (metric_name, extractor_fn).
    Extractor: (k6, resource, pgstats, network) → float | None

    from_res_flex: handle mode='mean' (scalar) dan mode='series' (list) untuk soak.
    from_net_flex: idem untuk network.
    """
    def from_k6(key):
        return lambda k6, res, pg, net: k6.get(key) if k6 else None

    def from_res_flex(key_mean, key_list):
        def extractor(k6, res, pg, net):
            if res is None:
                return None
            val = res.get(key_mean)
            if val is not None:
                return float(val)
            lst = res.get(key_list)
            if lst and isinstance(lst, list) and len(lst) > 0:
                return sum(lst) / len(lst)
            return None
        return extractor

    def from_net_flex(key_mean, key_list):
        def extractor(k6, res, pg, net):
            if net is None:
                return None
            val = net.get(key_mean)
            if val is not None:
                return float(val)
            lst = net.get(key_list)
            if lst and isinstance(lst, list) and len(lst) > 0:
                return sum(lst) / len(lst)
            return None
        return extractor

    def from_pg(key):
        return lambda k6, res, pg, net: pg.get(key) if pg else None

    return [
        ("avg_rt",             from_k6("avg_rt")),
        ("med_rt",             from_k6("med_rt")),
        ("p90",                from_k6("p90")),
        ("p95",                from_k6("p95")),
        ("p99",                from_k6("p99")),
        ("min_rt",             from_k6("min_rt")),
        ("max_rt",             from_k6("max_rt")),
        ("throughput",         from_k6("throughput")),
        ("functional_error",   from_k6("functional_error_rate")),
        ("sla_breach",         from_k6("sla_breach_rate")),
        ("checks_pass_rate",   from_k6("checks_pass_rate")),
        ("payload_bytes",      from_k6("payload_size_bytes")),
        ("http_count",         from_k6("http_req_count")),
        ("cpu_pct",            from_res_flex("cpu_pct_mean",    "cpu_pct")),
        ("mem_mb",             from_res_flex("mem_mb_mean",     "mem_mb")),
        ("pg_active",          from_res_flex("pg_active_mean",  "pg_active")),
        ("pg_cache_hit_ratio", from_res_flex("pg_cache_hit_mean", "pg_cache_hit_ratio")),
        ("pg_tps_delta",       from_res_flex("pg_tps_mean",       "pg_tps_delta")),
        ("db_query_avg_ms",    from_pg("weighted_avg_ms")),
        ("network_total_kb_s", from_net_flex("total_mean_kb_s", "total_kb_s")),
    ]


METRIC_EXTRACTORS = build_metric_extractors()


# ---------------------------------------------------------------------------
# MANIFEST & VALIDATION
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: str) -> list[dict]:
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest tidak ditemukan: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(results_dir: str, filename: str | None) -> str | None:
    if not filename:
        return None
    return os.path.join(results_dir, filename)


def check_duplicate_runs(manifest: list[dict]) -> list[tuple]:
    """
    BUG-02-N11 FIX: Deteksi duplicate (scenario, test_type, condition, run_num).
    Re-run yang di-append tanpa hapus entry lama akan terdeteksi di sini.
    Returns list of duplicate keys (non-empty = ada masalah).
    """
    seen  = set()
    dupes = []
    for entry in manifest:
        key = (
            entry.get("scenario",  "?"),
            entry.get("test_type", "?"),
            entry.get("condition", "C2"),
            entry.get("run",       -1),
        )
        if key in seen:
            dupes.append(key)
        seen.add(key)
    return dupes


def validate_counterbalancing(manifest: list[dict]) -> dict:
    """
    Validasi counterbalancing (alternating rest-first / trpc-first).
    ISS-09: diperluas ke stress/spike, tidak hanya load.
    """
    warnings = []
    summary  = {}

    groups = {}
    for entry in manifest:
        key = (
            entry.get("scenario", "?"),
            entry.get("test_type", "load"),
            entry.get("condition", "C2"),
        )
        groups.setdefault(key, []).append(entry)

    for key, entries in groups.items():
        scenario, test_type, condition = key
        # ISS-09: check semua test types, bukan hanya load
        orders     = [e.get("execution_order", "unknown") for e in entries]
        rest_first = orders.count("rest-first")
        trpc_first = orders.count("trpc-first")
        n          = len(orders)

        summary[str(key)] = {"n": n, "rest_first": rest_first, "trpc_first": trpc_first}

        # Consecutive same-order check
        for i in range(1, len(orders)):
            if orders[i] == orders[i - 1] and orders[i] != "unknown":
                warnings.append(
                    f"{scenario}/{test_type}/{condition}: "
                    f"Run {i} dan {i+1} berturut-turut sama ({orders[i]})"
                )

        # Balance check hanya untuk load N=10
        if test_type == "load" and n == 10 and (rest_first != 5 or trpc_first != 5):
            warnings.append(
                f"{scenario}/{test_type}/{condition}: "
                f"N=10 balance tidak 5:5 (rest-first={rest_first}, trpc-first={trpc_first})"
            )

        # Stress/spike: warn jika semua sama-order (N=3)
        if test_type in ("stress", "spike") and n == 3:
            if rest_first == 3 or trpc_first == 3:
                warnings.append(
                    f"{scenario}/{test_type}/{condition}: "
                    f"Semua {n} run {orders[0]} — tidak ada counterbalancing di stress/spike"
                )

    return {"ok": len(warnings) == 0, "warnings": warnings, "summary": summary}


# ---------------------------------------------------------------------------
# PARSING
# ---------------------------------------------------------------------------

def parse_run_entry(entry: dict, results_dir: str) -> dict:
    """
    Parse semua file untuk satu run entry dari manifest.
    Untuk C3/C4: entry mungkin tidak punya 'rest' (null) — ini valid.
    """
    errors    = []
    scenario  = entry.get("scenario", "")
    test_type = entry.get("test_type", "load")
    condition = entry.get("condition", "C2")
    csv_mode  = "series" if test_type == "soak" else "mean"

    def safe_parse(fn, path, *args, **kwargs):
        if path is None:
            return None
        try:
            return fn(path, *args, **kwargs)
        except Exception as e:
            errors.append(f"{os.path.basename(path)}: {e}")
            return None

    rest_data = {
        "k6":       safe_parse(parse_k6,       resolve_path(results_dir, entry.get("rest")),          scenario=scenario),
        "resource": safe_parse(parse_resources, resolve_path(results_dir, entry.get("rest_resource")), mode=csv_mode),
        "pgstats":  safe_parse(parse_pgstats,   resolve_path(results_dir, entry.get("rest_pgstats"))),
        "network":  safe_parse(parse_network,   resolve_path(results_dir, entry.get("rest_network")),  mode=csv_mode),
    }
    trpc_data = {
        "k6":       safe_parse(parse_k6,       resolve_path(results_dir, entry.get("trpc")),           scenario=scenario),
        "resource": safe_parse(parse_resources, resolve_path(results_dir, entry.get("trpc_resource")), mode=csv_mode),
        "pgstats":  safe_parse(parse_pgstats,   resolve_path(results_dir, entry.get("trpc_pgstats"))),
        "network":  safe_parse(parse_network,   resolve_path(results_dir, entry.get("trpc_network")),  mode=csv_mode),
    }

    return {"meta": entry, "rest": rest_data, "trpc": trpc_data, "errors": errors}


def extract_metric_value(parsed_run: dict, side: str, metric_name: str) -> float | None:
    data = parsed_run[side]
    for name, extractor in METRIC_EXTRACTORS:
        if name == metric_name:
            return extractor(
                data.get("k6") or {},
                data.get("resource"),
                data.get("pgstats"),
                data.get("network"),
            )
    return None


# ---------------------------------------------------------------------------
# GROUPING
# ---------------------------------------------------------------------------

def group_runs(parsed_runs: list[dict]) -> dict:
    """Group by (scenario, test_type, condition). Default condition = 'C2'."""
    groups = {}
    for run in parsed_runs:
        meta      = run["meta"]
        scenario  = meta.get("scenario", "unknown")
        test_type = meta.get("test_type", "load")
        condition = meta.get("condition", "C2")
        key       = (scenario, test_type, condition)
        groups.setdefault(key, []).append(run)
    return groups


# ---------------------------------------------------------------------------
# SOAK TIMESERIES
# ---------------------------------------------------------------------------

def _extract_soak_timeseries(runs: list[dict]) -> dict | None:
    """Extract time-series dari soak run + memory slope (linear regression)."""
    if not runs:
        return None

    run = runs[0]

    def get_series(side: str) -> dict | None:
        data     = run[side]
        resource = data.get("resource") or {}
        network  = data.get("network")  or {}

        if resource.get("mode") != "series":
            return None

        ts = {
            "timestamps": resource.get("timestamps", []),
            "n_samples":  resource.get("n_samples", 0),
            "cpu_pct":    resource.get("cpu_pct", []),
            "mem_mb":     resource.get("mem_mb", []),
            "pg_active":  resource.get("pg_active", []),
            "cpu_total":  resource.get("cpu_total_pct", []),
        }

        if network.get("mode") == "series":
            ts["network_kb_s"] = network.get("total_kb_s", [])

        # Memory slope — hitung actual interval dari timestamps jika tersedia
        mem_vals   = ts["mem_mb"]
        timestamps = ts["timestamps"]

        if len(mem_vals) >= 5:
            try:
                from scipy.stats import linregress

                # Hitung actual sampling interval dari timestamps
                interval_sec = 5.0  # default fallback
                if len(timestamps) >= 2:
                    try:
                        from datetime import datetime
                        fmt_candidates = [
                            "%Y-%m-%dT%H:%M:%S",
                            "%Y-%m-%d %H:%M:%S",
                            "%Y-%m-%dT%H:%M:%SZ",
                        ]
                        t0, t1 = None, None
                        for fmt in fmt_candidates:
                            try:
                                t0 = datetime.strptime(timestamps[0].strip(), fmt)
                                t1 = datetime.strptime(timestamps[1].strip(), fmt)
                                break
                            except (ValueError, AttributeError):
                                continue
                        if t0 is not None and t1 is not None:
                            diff_s = abs((t1 - t0).total_seconds())
                            if diff_s > 0:
                                interval_sec = diff_s
                    except Exception:
                        pass  # fallback to default 5s

                x = list(range(len(mem_vals)))
                slope, _, r_val, p_val, _ = linregress(x, mem_vals)
                mb_per_hour = slope * 3600 / interval_sec

                ts["mem_slope_per_sample"]  = float(slope)
                ts["mem_slope_r2"]          = float(r_val ** 2)
                ts["mem_slope_p"]           = float(p_val)
                ts["mem_slope_mb_per_hour"] = float(mb_per_hour)
                ts["sampling_interval_sec"] = float(interval_sec)
                ts["mem_slope_note"] = (
                    f"slope = {slope:.4f} MB/sample "
                    f"(≈{mb_per_hour:.2f} MB/hour @ {interval_sec:.0f}s interval), "
                    f"R²={r_val**2:.3f}, p={p_val:.4f}"
                )
            except Exception as e:
                ts["mem_slope_note"] = f"Regression error: {e}"

        return ts

    rest_ts = get_series("rest")
    trpc_ts = get_series("trpc")

    if rest_ts is None and trpc_ts is None:
        return None

    return {"rest": rest_ts, "trpc": trpc_ts}


# ---------------------------------------------------------------------------
# GROUP ANALYSIS
# ---------------------------------------------------------------------------

def analyze_group(group_key: tuple, runs: list[dict]) -> dict:
    """
    Analisis satu group (scenario, test_type, condition).

    BUG-01-C3C4 FIX: Untuk condition C3/C4, REST data mungkin null.
    Dalam kasus ini, extract trpc-only values untuk descriptive analysis,
    yang kemudian dipakai oleh compute_decompositions untuk perhitungan gap.
    """
    scenario, test_type, condition = group_key
    n         = len(runs)
    all_errors = []

    for run in runs:
        all_errors.extend(run.get("errors", []))

    results_per_metric = {}
    is_decomp_condition = condition in ("C3", "C4")

    for metric_name, _ in METRIC_EXTRACTORS:
        rest_vals_raw = [extract_metric_value(r, "rest", metric_name) for r in runs]
        trpc_vals_raw = [extract_metric_value(r, "trpc", metric_name) for r in runs]

        # BUG-01-C3C4 FIX: Untuk C3/C4, REST bisa null (tidak ada REST run baru).
        # Extract trpc-only untuk decomposition; simpan sebagai observational.
        if is_decomp_condition:
            trpc_only = [v for v in trpc_vals_raw if v is not None]
            if not trpc_only:
                results_per_metric[metric_name] = {
                    "status": "no_data",
                    "note":   f"Tidak ada tRPC data untuk {metric_name}"
                }
                continue
            # Simpan trpc descriptive saja — REST tidak diukur baru untuk C3/C4
            analysis = {
                "metric":    metric_name,
                "n":         len(trpc_only),
                "framing":   f"decomposition_{condition.lower()} — trpc-only",
                "trpc_vals": trpc_only,
                "descriptive": {
                    "trpc": descriptive(trpc_only),
                },
            }
            analysis["n_valid"]     = len(trpc_only)
            analysis["n_requested"] = n
            results_per_metric[metric_name] = analysis
            continue

        # Regular paired analysis
        pairs = [(r, t) for r, t in zip(rest_vals_raw, trpc_vals_raw)
                 if r is not None and t is not None]

        if not pairs:
            results_per_metric[metric_name] = {
                "status": "no_data",
                "note":   "Tidak ada data untuk metrik ini di semua run"
            }
            continue

        rest_vals = [p[0] for p in pairs]
        trpc_vals = [p[1] for p in pairs]
        n_valid   = len(pairs)

        if test_type == "load" and n_valid >= 2:
            analysis = analyze_load(rest_vals, trpc_vals, metric_name=metric_name)
        elif test_type in ("stress", "spike") and n_valid >= 2:
            analysis = analyze_exploratory(rest_vals, trpc_vals, metric_name=metric_name)
        elif test_type == "soak" or n_valid < 2:
            d = difference_scores(rest_vals, trpc_vals)
            analysis = {
                "metric":    metric_name,
                "n":         n_valid,
                "framing":   "observational",
                "rest_vals": rest_vals,
                "trpc_vals": trpc_vals,
                "diff":      d,
                "descriptive": {
                    "rest": descriptive(rest_vals),
                    "trpc": descriptive(trpc_vals),
                },
            }
        else:
            analysis = {"metric": metric_name, "status": "insufficient_data", "n": n_valid}

        analysis["n_valid"]     = n_valid
        analysis["n_requested"] = n
        results_per_metric[metric_name] = analysis

    # Per-endpoint metrics — union dari SEMUA runs
    all_endpoints = set()
    for run in runs:
        k6_rest = (run["rest"].get("k6") or {})
        k6_trpc = (run["trpc"].get("k6") or {})
        all_endpoints.update(k6_rest.get("endpoints", {}).keys())
        all_endpoints.update(k6_trpc.get("endpoints", {}).keys())

    endpoint_results = {}
    if not is_decomp_condition:  # skip endpoint analysis for C3/C4
        for ep in sorted(all_endpoints):
            rest_ep_vals, trpc_ep_vals = [], []
            for run in runs:
                rp95 = (run["rest"].get("k6") or {}).get("endpoints", {}).get(ep, {}).get("p95")
                tp95 = (run["trpc"].get("k6") or {}).get("endpoints", {}).get(ep, {}).get("p95")
                if rp95 is not None and tp95 is not None:
                    rest_ep_vals.append(rp95)
                    trpc_ep_vals.append(tp95)

            if len(rest_ep_vals) >= 2:
                fn = analyze_load if test_type == "load" else analyze_exploratory
                endpoint_results[ep] = fn(rest_ep_vals, trpc_ep_vals, metric_name=f"{ep}_p95")

    result = {
        "scenario":         scenario,
        "test_type":        test_type,
        "condition":        condition,
        "n":                n,
        "metrics":          results_per_metric,
        "endpoint_metrics": endpoint_results,
        "errors":           list(set(all_errors)),
    }

    if test_type == "soak":
        ts = _extract_soak_timeseries(runs)
        if ts:
            result["timeseries"] = ts

    return result


# ---------------------------------------------------------------------------
# DECOMPOSITION
# ---------------------------------------------------------------------------

def compute_decompositions(group_results: dict) -> dict:
    """
    Hitung decomposition analysis untuk C3 dan C4 groups.

    BUG-01-C3C4 FIX: C3 trpc_vals sekarang tersedia karena analyze_group
    menyimpan trpc descriptive untuk decomposition conditions.
    C1 REST mean diambil dari C2 group (REST tidak berubah antara C2 dan C3/C4).
    """
    decomp_results = {}

    for group_key, group in group_results.items():
        condition = group.get("condition", "C2")
        if condition not in ("C3", "C4"):
            continue

        scenario  = group["scenario"]
        test_type = group["test_type"]
        c2_key    = f"{scenario}__{test_type}__C2"

        if c2_key not in group_results:
            decomp_results[group_key] = {
                "status":    "missing_c2",
                "note":      f"C2 group tidak ditemukan: {c2_key}",
                "condition": condition,
                "scenario":  scenario,
            }
            continue

        c2_group = group_results[c2_key]

        if condition == "C3":
            metrics_c3 = {}
            for metric_name in ["avg_rt", "p95", "p99", "throughput", "cpu_pct", "mem_mb"]:
                c2_m = c2_group["metrics"].get(metric_name, {})
                c3_m = group["metrics"].get(metric_name, {})

                # C1 (REST baseline) dari C2 group descriptive.rest
                c1_mean = (c2_m.get("descriptive") or {}).get("rest", {}).get("mean")
                # C2 (tRPC baseline) dari C2 group descriptive.trpc
                c2_mean = (c2_m.get("descriptive") or {}).get("trpc", {}).get("mean")
                # C3 (tRPC auth-equalized) dari C3 group descriptive.trpc
                # BUG-01-C3C4 FIX: C3 group menyimpan trpc descriptive sekarang
                c3_mean = (c3_m.get("descriptive") or {}).get("trpc", {}).get("mean")

                if all(v is not None for v in [c1_mean, c2_mean, c3_mean]):
                    metrics_c3[metric_name] = analyze_decomposition_c3(
                        c1_mean, c2_mean, c3_mean, metric_name
                    )
                else:
                    metrics_c3[metric_name] = {
                        "status": "missing_data",
                        "note":   (f"Data tidak lengkap: "
                                   f"c1={c1_mean}, c2={c2_mean}, c3={c3_mean}")
                    }

            decomp_results[group_key] = {
                "condition": "C3", "scenario": scenario,
                "test_type": test_type, "metrics": metrics_c3,
            }

        elif condition == "C4":
            def _get_trpc_mean(grp, metric):
                m = grp["metrics"].get(metric, {})
                return (m.get("descriptive") or {}).get("trpc", {}).get("mean")

            c2_http  = _get_trpc_mean(c2_group, "http_count")
            c4_http  = _get_trpc_mean(group,    "http_count")
            c2_p95   = _get_trpc_mean(c2_group, "p95")
            c4_p95   = _get_trpc_mean(group,    "p95")
            c2_tput  = _get_trpc_mean(c2_group, "throughput")
            c4_tput  = _get_trpc_mean(group,    "throughput")

            if all(v is not None for v in [c2_http, c4_http, c2_p95, c4_p95, c2_tput, c4_tput]):
                c4_result = analyze_decomposition_c4(
                    c2_http, c4_http, c2_p95, c4_p95, c2_tput, c4_tput, scenario=scenario
                )
            else:
                c4_result = {
                    "status": "missing_data",
                    "note":   "Satu atau lebih metrik tidak tersedia untuk C4 decomposition"
                }

            decomp_results[group_key] = {
                "condition":     "C4",
                "scenario":      scenario,
                "test_type":     test_type,
                "decomposition": c4_result,
            }

    return decomp_results


# ---------------------------------------------------------------------------
# ORDER EFFECT VERIFICATION (Issue #5 Fix)
# ---------------------------------------------------------------------------

def verify_order_effects(group_results: dict, parsed_runs: list[dict]) -> dict:
    """
    Issue #5 Fix: Verifikasi bahwa counterbalancing 5:5 efektif —
    tidak ada perbedaan sistematis antara rest-first vs trpc-first subgroup.

    Logika:
      - Untuk setiap load group (N=10), pisah runs by execution_order
      - Hitung mean REST dan tRPC untuk masing-masing subgroup
      - Bandingkan Δ (REST-tRPC) di rest-first vs trpc-first
      - Jika Δ berlawanan arah atau magnitude sangat berbeda → order effect

    Returns:
      dict berisi per-group order effect analysis.
    """
    results = {}

    for group_key, group in group_results.items():
        scenario  = group["scenario"]
        test_type = group["test_type"]
        condition = group.get("condition", "C2")

        # Hanya untuk load (N=10), skip C3/C4 dan non-load
        if test_type != "load" or condition not in ("C2",) or group["n"] < 6:
            continue

        # Cari parsed runs untuk group ini
        group_runs = [
            r for r in parsed_runs
            if r["meta"].get("scenario") == scenario
            and r["meta"].get("test_type") == test_type
            and r["meta"].get("condition", "C2") == condition
        ]

        if not group_runs:
            continue

        rf_runs = [r for r in group_runs if r["meta"].get("execution_order") == "rest-first"]
        tf_runs = [r for r in group_runs if r["meta"].get("execution_order") == "trpc-first"]

        if not rf_runs or not tf_runs:
            results[group_key] = {
                "scenario": scenario, "test_type": test_type, "condition": condition,
                "note": f"Tidak cukup data per subgroup (rf={len(rf_runs)}, tf={len(tf_runs)})",
                "order_effect_detected": False,
            }
            continue

        primary_metrics = ["p95", "throughput", "cpu_pct", "mem_mb"]
        metric_comparison = {}
        any_order_effect  = False

        for metric_name in primary_metrics:
            def _get_vals(runs, side, mn=metric_name):
                vals = []
                for r in runs:
                    val = extract_metric_value(r, side, mn)
                    if val is not None:
                        vals.append(val)
                return vals

            rf_rest = _get_vals(rf_runs, "rest")
            rf_trpc = _get_vals(rf_runs, "trpc")
            tf_rest = _get_vals(tf_runs, "rest")
            tf_trpc = _get_vals(tf_runs, "trpc")

            if not rf_rest or not rf_trpc or not tf_rest or not tf_trpc:
                continue

            rf_rest_m = sum(rf_rest) / len(rf_rest)
            rf_trpc_m = sum(rf_trpc) / len(rf_trpc)
            tf_rest_m = sum(tf_rest) / len(tf_rest)
            tf_trpc_m = sum(tf_trpc) / len(tf_trpc)

            # Δ = REST - tRPC di masing-masing subgroup
            # Jika counterbalancing efektif: Δ seharusnya sama arah dan magnitude serupa
            rf_delta = rf_rest_m - rf_trpc_m
            tf_delta = tf_rest_m - tf_trpc_m

            same_direction = (rf_delta >= 0) == (tf_delta >= 0)

            # Cek magnitude ratio — jika salah satu 3x lebih besar dari lainnya, curigai order effect
            if rf_delta != 0 and tf_delta != 0:
                mag_ratio = abs(rf_delta / tf_delta)
                large_discrepancy = mag_ratio > 3.0 or mag_ratio < 0.33
            else:
                mag_ratio      = None
                large_discrepancy = False

            order_effect = not same_direction or large_discrepancy
            if order_effect:
                any_order_effect = True

            metric_comparison[metric_name] = {
                "rest_first":        {"n": len(rf_rest), "rest_mean": round(rf_rest_m, 3), "trpc_mean": round(rf_trpc_m, 3), "delta": round(rf_delta, 3)},
                "trpc_first":        {"n": len(tf_rest), "rest_mean": round(tf_rest_m, 3), "trpc_mean": round(tf_trpc_m, 3), "delta": round(tf_delta, 3)},
                "same_direction":    same_direction,
                "magnitude_ratio":   round(mag_ratio, 2) if mag_ratio is not None else None,
                "order_effect_flag": order_effect,
            }

        results[group_key] = {
            "scenario":             scenario,
            "test_type":            test_type,
            "condition":            condition,
            "n_rest_first":         len(rf_runs),
            "n_trpc_first":         len(tf_runs),
            "metrics":              metric_comparison,
            "order_effect_detected": any_order_effect,
            "note": (
                "⚠ ORDER EFFECT TERDETEKSI — periksa metrik yang flagged"
                if any_order_effect else
                "✓ Counterbalancing efektif — tidak ada order effect signifikan"
            ),
        }

    return results


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def run_analysis(manifest_path: str, results_dir: str) -> dict:
    """
    Entry point utama.

    BUG-02-N11 FIX: Cek duplicate run numbers sebelum analisis.
    Issue #5 Fix: Order effect verification untuk load groups.
    """
    print(f"Loading manifest: {manifest_path}")
    manifest = load_manifest(manifest_path)
    print(f"Found {len(manifest)} run entries\n")

    # BUG-02-N11 FIX: Duplicate check
    dupes = check_duplicate_runs(manifest)
    if dupes:
        print("[!!!] DUPLICATE RUN NUMBERS TERDETEKSI!")
        for d in dupes:
            print(f"   [!!!] Duplicate: scenario={d[0]}, test={d[1]}, condition={d[2]}, run={d[3]}")
        print("   Hapus entry lama dari run_manifest.json sebelum menjalankan analisis!")
        print("   Analisis dilanjutkan tapi N mungkin salah.\n")

    # Counterbalancing validation
    cb_check = validate_counterbalancing(manifest)
    if not cb_check["ok"]:
        print("⚠  COUNTERBALANCING WARNINGS:")
        for w in cb_check["warnings"]:
            print(f"   ⚠  {w}")
        print()

    # Parse semua run
    parsed_runs = []
    for i, entry in enumerate(manifest):
        run_num   = entry.get("run", i + 1)
        scenario  = entry.get("scenario", "?")
        test_type = entry.get("test_type", "?")
        condition = entry.get("condition", "C2")

        print(f"  Parsing run {run_num}: {scenario}/{test_type}/{condition}...", end=" ")
        parsed = parse_run_entry(entry, results_dir)
        if parsed["errors"]:
            print(f"[WARN: {len(parsed['errors'])} errors]")
            for e in parsed["errors"]:
                print(f"    ⚠ {e}")
        else:
            print("OK")
        parsed_runs.append(parsed)

    # Group dan analisis
    groups = group_runs(parsed_runs)
    print(f"\nAnalyzing {len(groups)} group(s)...\n")

    group_results = {}
    for group_key, runs in sorted(groups.items()):
        scenario, test_type, condition = group_key
        print(f"  {scenario} / {test_type} / {condition} (N={len(runs)})")
        result = analyze_group(group_key, runs)
        group_results[f"{scenario}__{test_type}__{condition}"] = result

    decomp_results = compute_decompositions(group_results)
    if decomp_results:
        print(f"\n  Decomposition: {len(decomp_results)} group(s)")

    # Issue #5 Fix: Order effect verification
    order_effects = verify_order_effects(group_results, parsed_runs)

    return {
        "manifest_path":          manifest_path,
        "results_dir":            results_dir,
        "n_runs":                 len(manifest),
        "n_groups":               len(groups),
        "groups":                 group_results,
        "decompositions":         decomp_results,
        "counterbalancing_check": cb_check,
        "duplicate_runs":         dupes,
        "order_effects":          order_effects,    # Issue #5 Fix
    }