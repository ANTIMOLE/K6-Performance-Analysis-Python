"""
main.py — ZENIT Performance Testing Analysis
Entry point utama. Jalankan dengan:

  python main.py --manifest ../results/run_manifest.json --results ../results/ --output ../charts/

Flags:
  --manifest   path ke run_manifest.json (default: ../results/run_manifest.json)
  --results    direktori file hasil (default: ../results/)
  --output     direktori output laporan (default: ../charts/)
  --no-charts  skip chart generation

FIXES:
  INC-01: ✓* untuk stress/spike (exploratory) vs ✓ untuk load (confirmatory)
  INC-02: Tambah metrik di terminal: sla_breach, functional_error,
          db_query_avg_ms, network_total_kb_s, http_count, payload_bytes
  ADD-01: SLA breach & functional error alert otomatis
  BUG-02: Tampilkan duplicate run warning di terminal summary
  BUG-N2: n_warning ditampilkan di terminal dengan jelas
  Issue-03: ⚠ untuk sig tapi d tidak_interpretatif — tidak lagi ✓ palsu
  Issue-05: Order effect verification ditampilkan setelah summary
  Issue-02: Anomaly flag pada C3 decomposition >100% atau <0%

REMOVED:
  Issue-01: Bonferroni sensitivity analysis dihapus. Thesis menggunakan RQ-based
  framing; order effect verification berdiri sendiri di terminal dan Excel.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from analyze import run_analysis
from report import generate_report
from interpret import generate_interpretations


def _print_interpretations(results: dict, interpretations: dict):
    """Print interpretasi naratif berbasis RQ1/RQ2/RQ3."""
    print(f"\n{'=' * 80}")
    print("INTERPRETASI NARATIF — JAWABAN PERTANYAAN PENELITIAN")
    print("=" * 80)

    print(f"\n  {interpretations.get('overall_summary','')}\n")

    rq = interpretations.get("research_questions", {})

    # ── RQ1 ──────────────────────────────────────────────────────────────
    rq1 = rq.get("RQ1", {})
    if rq1:
        print(f"  {'─' * 76}")
        print(f"  RQ1: {rq1.get('question','')[:80]}")
        print(f"  {'─' * 76}")
        print(f"\n  {rq1.get('summary','')}\n")
        rest_dom = rq1.get("rest_dominant_metrics",[])
        trpc_dom = rq1.get("trpc_dominant_metrics",[])
        if rest_dom:
            from interpret import METRIC_LABELS as ML
            print(f"  REST dominan: {', '.join(ML.get(m,m) for m in rest_dom[:5])}")
        if trpc_dom:
            print(f"  tRPC dominan: {', '.join(ML.get(m,m) for m in trpc_dom[:5])}")
        if not trpc_dom:
            print(f"  tRPC: tidak menunjukkan keunggulan konsisten di metrik manapun pada beban normal.")

    # ── RQ2 ──────────────────────────────────────────────────────────────
    rq2 = rq.get("RQ2", {})
    if rq2:
        print(f"\n  {'─' * 76}")
        print(f"  RQ2: {rq2.get('question','')[:80]}")
        print(f"  {'─' * 76}")
        n_r = len(rq2.get("rest_superior",[]))
        n_t = len(rq2.get("trpc_superior",[]))
        print(f"\n  REST lebih optimal ({n_r} kasus):")
        print(f"    {rq2.get('rest_summary','')[:150]}")
        print(f"\n  tRPC lebih optimal ({n_t} kasus):")
        print(f"    {rq2.get('trpc_summary','')[:150]}")
        print(f"\n  Overall: {rq2.get('overall','')[:150]}")

        # Show top confirmatory REST wins
        conf_rest = [e for e in rq2.get("rest_superior",[]) if not e.get("exploratory")]
        if conf_rest:
            print(f"\n  Kasus REST konfirmatoris:")
            for e in conf_rest[:5]:
                print(f"    → {e.get('sentence','')[:100]}")

        # Show tRPC wins (all — should be few or none)
        if rq2.get("trpc_superior"):
            print(f"\n  Kasus tRPC:")
            for e in rq2["trpc_superior"][:5]:
                print(f"    ↑ {e.get('sentence','')[:100]}")
        else:
            print(f"\n  Kasus tRPC: Tidak ada kasus konfirmatoris tRPC unggul atas REST.")

    # ── RQ3 ──────────────────────────────────────────────────────────────
    rq3 = rq.get("RQ3", {})
    if rq3:
        print(f"\n  {'─' * 76}")
        print(f"  RQ3: {rq3.get('question','')[:80]}")
        print(f"  {'─' * 76}")
        print(f"\n  Rekomendasi utama: {rq3.get('primary_recommendation','')}")
        print(f"  {rq3.get('overall','')[:200]}")
        print(f"\n  Per use case:")
        for rec in rq3.get("recommendations",[]):
            print(f"    [{rec.get('recommendation','')}] {rec.get('use_case','')}")
            print(f"          {rec.get('reason','')[:100]}")

    # ── Notable findings per skenario ────────────────────────────────────
    print(f"\n  {'─' * 76}")
    print(f"  TEMUAN NOTABLE PER SKENARIO (Load C2)")
    print(f"  {'─' * 76}")
    for gk, gi in sorted(interpretations.get("groups",{}).items()):
        if gi.get("test_type") != "load" or gi.get("condition","C2") != "C2":
            continue
        sc = gi.get("scenario","?")
        print(f"\n  {sc.upper()} / LOAD: {gi.get('overall','')}")
        for flag in gi.get("flags",[]):
            print(f"    ⚠ {flag}")
        for mn, mi in gi.get("metrics",{}).items():
            if mi and mi.get("significant") and mi.get("magnitude") in ("besar","sedang"):
                print(f"    → {mi.get('sentence','')[:100]}")
                if mi.get("practical"):
                    print(f"       Praktis: {mi.get('practical','')}")


# ---------------------------------------------------------------------------
# SUMMARY TABLE
# ---------------------------------------------------------------------------

def print_summary_table(results: dict):
    """Print ringkasan ke terminal."""
    print("\n" + "=" * 80)
    print("RINGKASAN HASIL ANALISIS")
    print("=" * 80)

    # Duplicate warning
    dupes = results.get("duplicate_runs", [])
    if dupes:
        print("\n[!!!] DUPLICATE RUNS TERDETEKSI — N mungkin salah:")
        for d in dupes:
            print(f"   [!!!] {d[0]}/{d[1]}/{d[2]} run={d[3]}")
        print()

    # Counterbalancing warnings
    cb = results.get("counterbalancing_check", {})
    if cb and not cb.get("ok"):
        print("⚠  COUNTERBALANCING WARNINGS:")
        for w in cb.get("warnings", []):
            print(f"   ⚠  {w}")
        print()

    for group_key, group in sorted(results["groups"].items()):
        scenario  = group["scenario"]
        test_type = group["test_type"]
        condition = group.get("condition", "C2")
        n         = group["n"]

        cond_label = f" / {condition}" if condition != "C2" else ""
        print(f"\n{scenario.upper()} / {test_type.upper()}{cond_label} (N={n})")

        # N warning
        if test_type == "load" and n < 10:
            print(f"   ⚠  INTERIM: N={n} < 10 — bukan confirmatory penuh")

        is_exploratory = test_type in ("stress", "spike")
        sig_header     = "Sig*" if is_exploratory else "Sig"

        print(f"   {'Metrik':<26} {'REST':>10} {'tRPC':>10} "
              f"{'Δ':>10} {'d':>7} {'Mag':>8} {sig_header:>5}")
        print("   " + "-" * 80)

        # INC-02: expanded metric list
        metric_order = [
            "p95", "p99", "throughput", "avg_rt",
            "cpu_pct", "mem_mb",
            "sla_breach", "functional_error",
            "http_count", "payload_bytes",
            "db_query_avg_ms", "network_total_kb_s",
        ]

        label_map = {
            "p95":               "P95 (ms)",
            "p99":               "P99 (ms)",
            "throughput":        "Throughput (req/s)",
            "avg_rt":            "Avg RT (ms)",
            "cpu_pct":           "CPU %",
            "mem_mb":            "RAM (MB)",
            "sla_breach":        "SLA Breach Rate",
            "functional_error":  "Func. Error Rate",
            "http_count":        "HTTP Req Count",
            "payload_bytes":     "Payload Avg (bytes)",
            "db_query_avg_ms":   "DB Query Avg (ms)",
            "network_total_kb_s":"Network (KB/s)",
        }

        rate_metrics = {"sla_breach", "functional_error"}

        for metric_name in metric_order:
            analysis = group["metrics"].get(metric_name, {})
            if analysis.get("status") in ("no_data", "insufficient_data"):
                continue

            desc  = analysis.get("descriptive", {})
            dr    = desc.get("rest", {})
            dt    = desc.get("trpc", {})
            cd    = analysis.get("cohens_d", {})
            inf   = analysis.get("inferential", analysis.get("ttest_reference", {}))

            rm    = dr.get("mean")
            tm    = dt.get("mean")
            diff  = (rm - tm) if rm is not None and tm is not None else None
            d_val = cd.get("d")
            mag   = (cd.get("magnitude") or "?")[:6]

            sig_raw   = inf.get("significant")
            cd_mag    = cd.get("magnitude")

            # Issue #3 Fix: jika d tidak_interpretatif, tampilkan ⚠ bukan ✓
            # Alasan: d meledak karena SD(d_i) ≈ 0, bukan efek protokol besar.
            # Menampilkan ✓ pada kasus ini menyesatkan pembaca.
            if cd_mag == "tidak_interpretatif":
                sig_str = "⚠"
            elif sig_raw is True:
                sig_str = "✓*" if is_exploratory else "✓"
            elif sig_raw is False:
                sig_str = "✗"
            else:
                sig_str = "—"

            label = label_map.get(metric_name, metric_name)

            is_rate = metric_name in rate_metrics
            if is_rate:
                rm_s   = f"{rm:.4f}" if rm   is not None else "N/A"
                tm_s   = f"{tm:.4f}" if tm   is not None else "N/A"
                diff_s = f"{diff:+.4f}" if diff is not None else "N/A"
            elif metric_name in ("http_count", "payload_bytes"):
                rm_s   = f"{rm:.0f}"   if rm   is not None else "N/A"
                tm_s   = f"{tm:.0f}"   if tm   is not None else "N/A"
                diff_s = f"{diff:+.0f}" if diff is not None else "N/A"
            else:
                rm_s   = f"{rm:.2f}"   if rm   is not None else "N/A"
                tm_s   = f"{tm:.2f}"   if tm   is not None else "N/A"
                diff_s = f"{diff:+.2f}" if diff is not None else "N/A"

            d_s = f"{d_val:.2f}" if d_val is not None else "N/A"

            print(f"   {label:<26} {rm_s:>10} {tm_s:>10} "
                  f"{diff_s:>10} {d_s:>7} {mag:>8} {sig_str:>5}")

        if is_exploratory:
            print(f"   (* = exploratory N={n}, t-test referensi saja — "
                  f"power sangat rendah, df={n-1})")

        print(f"   Legenda Sig: ✓ signifikan | ✗ tidak | ⚠ sig tapi d tidak_interpretatif (SD≈0) | — tidak dapat dinilai")

        # ADD-01: SLA breach & functional error alert
        _print_sla_alert(group)

        # Endpoint metrics
        if group.get("endpoint_metrics"):
            print(f"\n   Per-Endpoint P95 (ms):")
            for ep_name, ep_analysis in group["endpoint_metrics"].items():
                desc  = ep_analysis.get("descriptive", {})
                dr    = desc.get("rest", {})
                dt    = desc.get("trpc", {})
                cd    = ep_analysis.get("cohens_d", {})
                rm    = dr.get("mean")
                tm    = dt.get("mean")
                diff  = (rm - tm) if rm is not None and tm is not None else None
                d_val = cd.get("d")
                rm_s   = f"{rm:.1f}"    if rm    is not None else "N/A"
                tm_s   = f"{tm:.1f}"    if tm    is not None else "N/A"
                diff_s = f"{diff:+.1f}" if diff  is not None else "N/A"
                d_s    = f"{d_val:.2f}" if d_val is not None else "N/A"
                print(f"     {ep_name:<37} REST={rm_s:>7}  "
                      f"tRPC={tm_s:>7}  Δ={diff_s:>7}  d={d_s}")

        # Soak: memory slope
        if test_type == "soak":
            ts      = group.get("timeseries") or {}
            rest_ts = ts.get("rest") or {}
            trpc_ts = ts.get("trpc") or {}
            if rest_ts.get("mem_slope_note"):
                print(f"\n   Memory slope REST: {rest_ts['mem_slope_note']}")
            if trpc_ts.get("mem_slope_note"):
                print(f"   Memory slope tRPC: {trpc_ts['mem_slope_note']}")

        if group.get("errors"):
            shown = group["errors"][:5]
            print(f"\n   ⚠  Errors ({len(group['errors'])}):")
            for e in shown:
                print(f"     {e}")
            if len(group["errors"]) > 5:
                print(f"     ... dan {len(group['errors']) - 5} lainnya")

    # Decomposition summary
    decomp = results.get("decompositions", {})
    if decomp:
        print(f"\n{'=' * 80}")
        print("DECOMPOSITION ANALYSIS")
        print("=" * 80)
        for gk, dc in sorted(decomp.items()):
            condition = dc.get("condition", "?")
            scenario  = dc.get("scenario", "?")
            print(f"\n  {condition} — {scenario}")
            if condition == "C3":
                has_anomaly = False
                for metric_name, mdata in dc.get("metrics", {}).items():
                    pct = mdata.get("auth_contribution_pct")
                    if pct is not None:
                        rem    = mdata.get("remaining_gap_pct", 0)
                        anm    = mdata.get("anomaly_flag", False)
                        flag   = " ⚠" if anm else ""
                        print(f"    {metric_name:<20} Auth contrib: {pct:.1f}%  "
                              f"Remaining: {rem:.1f}%{flag}")
                        if anm:
                            has_anomaly = True
                if has_anomaly:
                    print(f"\n    ⚠ ANOMALY TERDETEKSI — satu atau lebih metrik memiliki")
                    print(f"      auth contrib >100% atau <0%. Ini bukan bug pipeline;")
                    print(f"      kemungkinan single run noise (N=1 C3). Interpretasi")
                    print(f"      hanya indikatif, bukan konfirmatori. Lihat catatan")
                    print(f"      di bab 4 pembahasan untuk framing yang tepat.")
            elif condition == "C4":
                cd4 = dc.get("decomposition", {})
                if cd4.get("note"):
                    print(f"    {cd4['note']}")


def _print_sla_alert(group: dict):
    """ADD-01: Alert otomatis jika SLA breach > 5% atau functional error > 1%."""
    SLA_THRESHOLD = 0.05
    ERR_THRESHOLD = 0.01

    def _get_mean(metric):
        m  = group["metrics"].get(metric, {})
        dr = (m.get("descriptive") or {}).get("rest", {})
        dt = (m.get("descriptive") or {}).get("trpc", {})
        return dr.get("mean"), dt.get("mean")

    sla_rest, sla_trpc = _get_mean("sla_breach")
    err_rest, err_trpc = _get_mean("functional_error")

    alerts = []

    if sla_rest is not None and sla_trpc is not None:
        if max(sla_rest, sla_trpc) > SLA_THRESHOLD:
            alerts.append(
                f"⚠  SLA BREACH TINGGI: REST={sla_rest:.1%}  "
                f"tRPC={sla_trpc:.1%} (threshold={SLA_THRESHOLD:.0%})"
            )

    if err_rest is not None and err_trpc is not None:
        if max(err_rest, err_trpc) > ERR_THRESHOLD:
            alerts.append(
                f"✗  FUNCTIONAL ERROR TINGGI: REST={err_rest:.1%}  "
                f"tRPC={err_trpc:.1%} (threshold={ERR_THRESHOLD:.0%})"
            )

    if alerts:
        print()
        for a in alerts:
            print(f"   {a}")


def _print_order_effects(results: dict):
    """Issue #5 Fix: Tampilkan order effect verification."""
    oe = results.get("order_effects", {})
    if not oe:
        return

    print(f"\n{'=' * 80}")
    print("ORDER EFFECT VERIFICATION — Counterbalancing Check")
    print("=" * 80)
    print(f"  Verifikasi bahwa Δ (REST-tRPC) konsisten di rest-first vs trpc-first subgroup.\n")

    any_flagged = False
    for group_key, gr in sorted(oe.items()):
        scenario = gr["scenario"]
        note     = gr.get("note", "")
        n_rf     = gr.get("n_rest_first", 0)
        n_tf     = gr.get("n_trpc_first", 0)
        flagged  = gr.get("order_effect_detected", False)

        if flagged:
            any_flagged = True

        prefix = "⚠" if flagged else "✓"
        print(f"  {prefix} {scenario.upper()} (rf={n_rf}, tf={n_tf}): {note}")

        for metric_name, mdata in gr.get("metrics", {}).items():
            mflag = mdata.get("order_effect_flag", False)
            rf    = mdata.get("rest_first", {})
            tf    = mdata.get("trpc_first", {})
            ratio = mdata.get("magnitude_ratio")
            same  = mdata.get("same_direction", True)

            if mflag:
                print(f"      ⚠ {metric_name}: Δ_rf={rf.get('delta',0):+.2f}  "
                      f"Δ_tf={tf.get('delta',0):+.2f}  "
                      f"ratio={ratio}  same_dir={same}")

    if not any_flagged:
        print(f"\n  ✓ Tidak ada order effect signifikan terdeteksi di semua load groups.")
        print(f"  Counterbalancing 5:5 REST-first/tRPC-first berjalan efektif.")
    else:
        print(f"\n  ⚠ Order effect terdeteksi pada beberapa metrik/skenario.")
        print(f"  Periksa dan dokumentasikan di bab 5 limitasi.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ZENIT Analysis Pipeline — REST vs tRPC Performance"
    )
    parser.add_argument("--manifest",  default="../results/run_manifest.json")
    parser.add_argument("--results",   default="../results/")
    parser.add_argument("--output",    default="../charts/")
    parser.add_argument("--no-charts", action="store_true",
                        help="Skip chart generation")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))

    def resolve(p):
        return os.path.join(base_dir, p) if not os.path.isabs(p) else p

    manifest_path = resolve(args.manifest)
    results_dir   = resolve(args.results)
    output_dir    = resolve(args.output)

    print(f"Manifest:   {manifest_path}")
    print(f"Results:    {results_dir}")
    print(f"Output:     {output_dir}\n")

    results = run_analysis(manifest_path, results_dir)
    print_summary_table(results)
    _print_order_effects(results)

    interpretations = generate_interpretations(results)
    _print_interpretations(results, interpretations)

    if not args.no_charts:
        excel_path, chart_files = generate_report(results, output_dir,
                                                   interpretations=interpretations)
        print(f"\n[<<>>] Done")
        print(f"   Excel:  {excel_path}")
        print(f"   Charts: {len(chart_files)} file di {os.path.join(output_dir, 'charts')}/")
    else:
        print("\n⏭  Chart generation di-skip (--no-charts)")

    return results


if __name__ == "__main__":
    main()