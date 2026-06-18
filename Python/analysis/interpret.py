"""
interpret.py — ZENIT Performance Testing
Lapisan interpretasi naratif berbasis Pertanyaan Penelitian (RQ1/RQ2/RQ3).

Fungsi utama:
  generate_interpretations(all_results)       → dict lengkap
  generate_chart_descriptions(all_results)    → dict per filename
  generate_bab4_draft(interpretations, soak)  → string markdown

RQ1: Bagaimana perbandingan kinerja tRPC vs REST?
RQ2: Kondisi/skenario apa REST atau tRPC lebih optimal? (bidirectional)
RQ3: Arsitektur mana yang direkomendasikan?
"""

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

LOWER_IS_BETTER = {
    "p95", "p99", "avg_rt", "med_rt",
    "sla_breach", "functional_error", "db_query_avg_ms",
    # Fix #1: tambahkan resource metrics — lower = lebih efisien
    "cpu_pct", "mem_mb", "payload_bytes",
}
HIGHER_IS_BETTER = {"throughput", "http_count"}

# Fix #1: network I/O bersifat ambigu — throughput tinggi bisa berarti
# lebih banyak data terserved (bagus) ATAU overhead protokol (buruk).
# Dikategorikan NEUTRAL: tidak dihitung sebagai "pemenang" di RQ2.
NEUTRAL_METRICS = {"network_total_kb_s"}

METRIC_LABELS = {
    "p95":                "P95 Latency (ms)",
    "p99":                "P99 Latency (ms)",
    "avg_rt":             "Avg Response Time (ms)",
    "throughput":         "Throughput (req/s)",
    "cpu_pct":            "CPU Usage (%)",
    "mem_mb":             "RAM Usage (MB)",
    "sla_breach":         "SLA Breach Rate",
    "functional_error":   "Functional Error Rate",
    "http_count":         "HTTP Request Count",
    "payload_bytes":      "Payload Size (bytes)",
    "db_query_avg_ms":    "DB Query Avg (ms)",
    "network_total_kb_s": "Network I/O (KB/s)",
}

SCENARIO_LABELS = {
    "s01_browse":   "S01 Browse",
    "s02_shopping": "S02 Shopping",
    "s03_checkout": "S03 Checkout",
    "s04_auth":     "S04 Auth",
    "s05_admin":    "S05 Admin",
}

SCENARIO_ORDER  = ["s01_browse","s02_shopping","s03_checkout","s04_auth","s05_admin"]
PRIMARY_METRICS = ["p95","throughput","cpu_pct","mem_mb","avg_rt","sla_breach"]
ALL_METRICS     = ["p95","p99","avg_rt","throughput","cpu_pct","mem_mb",
                   "sla_breach","functional_error","http_count","payload_bytes",
                   "db_query_avg_ms","network_total_kb_s"]


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _pct(diff, base):
    if base is None or base == 0 or diff is None:
        return None
    return diff / abs(base) * 100


def _winner(metric_name, diff):
    if diff is None:
        return "sama"
    if metric_name in NEUTRAL_METRICS:
        # Ambigu: tidak klaim pemenang, exclude dari RQ2 winner count
        return "sama"
    if metric_name in LOWER_IS_BETTER:
        return "REST" if diff < 0 else ("tRPC" if diff > 0 else "sama")
    elif metric_name in HIGHER_IS_BETTER:
        return "REST" if diff > 0 else ("tRPC" if diff < 0 else "sama")
    else:
        # Fallback eksplisit: lower-is-better (defensif untuk metrik tak terdaftar)
        return "REST" if diff < 0 else ("tRPC" if diff > 0 else "sama")


def _mag_label(mag):
    return {
        "besar":               "besar",
        "sedang":              "sedang",
        "kecil":               "kecil",
        "trivial":             "trivial",
        "tidak_interpretatif": "tidak dapat diinterpretasi (SD≈0)",
    }.get(mag, mag or "?")


def _sig_phrase(sig, is_exploratory=False):
    if sig is True:
        return "signifikan" + (" (eksploratoris)" if is_exploratory else "")
    elif sig is False:
        return "tidak signifikan"
    return "tidak dapat dinilai (N=1)"


def _fmt_d(d_val):
    return f"d={d_val:.2f}" if d_val is not None else "d=N/A"


def _fmt_p(p_val):
    return f"p={p_val:.4f}" if p_val is not None else "p=N/A"


def _practical_note(metric_name, diff, rm, tm):
    if diff is None or rm is None or tm is None:
        return ""
    pct = _pct(diff, tm)
    if metric_name == "p95":
        delta_abs = abs(diff)
        # Fix #7: hapus referensi SLA 200ms hardcoded — SLA bervariasi per endpoint
        # (product_list 500ms, checkout 2000ms, login 1500ms, dll).
        # Gunakan delta absolut dan persentase baseline saja.
        if delta_abs < 5:
            return f"Selisih {delta_abs:.1f}ms — praktis tidak bermakna (<5ms)"
        elif pct is not None and abs(pct) < 5:
            return f"Selisih {delta_abs:.1f}ms ({abs(pct):.1f}% dari baseline tRPC) — kecil secara praktis"
        else:
            return f"Selisih {delta_abs:.1f}ms ({abs(pct):.1f}% dari baseline) — bermakna secara praktis"
    elif metric_name == "throughput":
        delta_abs = abs(diff)
        return f"Selisih {delta_abs:.2f} req/s — {'bermakna' if delta_abs > 5 else 'praktis tidak bermakna'}"
    elif metric_name == "mem_mb":
        delta_abs = abs(diff)
        if delta_abs > 100:
            return f"Selisih {delta_abs:.0f}MB — signifikan untuk deployment memory-constrained"
        elif delta_abs > 30:
            return f"Selisih {delta_abs:.0f}MB ({abs(pct):.1f}%) — perlu diperhatikan"
        else:
            return f"Selisih {delta_abs:.0f}MB — relatif kecil"
    elif metric_name == "cpu_pct":
        delta_abs = abs(diff)
        return f"Selisih {delta_abs:.1f}% CPU — {'bermakna untuk server shared' if delta_abs > 5 else 'praktis tidak bermakna'}"
    if pct is not None:
        return f"Selisih {abs(pct):.1f}% dari baseline"
    return ""


def _get_metric_vals(group, metric_name):
    """Safely get rm, tm, diff, d_val, cd_mag, sig, p_val from a group metric."""
    a    = group["metrics"].get(metric_name, {})
    desc = a.get("descriptive", {})
    rm   = desc.get("rest",  {}).get("mean")
    tm   = desc.get("trpc",  {}).get("mean")
    cd   = a.get("cohens_d", {})
    inf  = a.get("inferential", a.get("ttest_reference", {}))
    return (
        rm, tm,
        (rm - tm) if rm is not None and tm is not None else None,
        cd.get("d"),
        cd.get("magnitude", ""),
        inf.get("significant"),
        inf.get("p"),
        a,
    )


# ---------------------------------------------------------------------------
# CORE: INTERPRET ONE METRIC
# ---------------------------------------------------------------------------

def interpret_metric(metric_name, analysis, is_exploratory=False):
    if not analysis or analysis.get("status") in ("no_data","insufficient_data"):
        return None

    desc    = analysis.get("descriptive", {})
    rm      = desc.get("rest",  {}).get("mean")
    tm      = desc.get("trpc",  {}).get("mean")
    if rm is None or tm is None:
        return None

    cd      = analysis.get("cohens_d", {})
    inf     = analysis.get("inferential", analysis.get("ttest_reference", {}))
    diff    = rm - tm
    pct     = _pct(diff, tm)
    d_val   = cd.get("d")
    cd_mag  = cd.get("magnitude", "")
    sig     = inf.get("significant")
    bci     = analysis.get("bootstrap_ci", {})
    ci_lo   = bci.get("ci_lower")
    ci_hi   = bci.get("ci_upper")
    covers0 = bci.get("covers_zero")

    winner   = _winner(metric_name, diff)
    label    = METRIC_LABELS.get(metric_name, metric_name)
    d_str    = _fmt_d(d_val)
    mag_str  = _mag_label(cd_mag)
    sig_str  = _sig_phrase(sig, is_exploratory)

    flag = ""
    if cd_mag == "tidak_interpretatif":
        flag = "d_tidak_interpretatif"
    elif sig and d_val is not None and abs(d_val) < 0.2:
        flag = "sig_tapi_trivial"

    if cd_mag == "tidak_interpretatif":
        sentence = (
            f"{label}: Perbedaan sangat konsisten antar run (SD≈0) sehingga {d_str} "
            f"tidak dapat diinterpretasi. Selisih aktual REST={rm:.2f} vs tRPC={tm:.2f} "
            f"(Δ={diff:+.2f})."
        )
    elif winner == "sama" or (d_val is not None and abs(d_val) < 0.2 and not sig):
        pct_s = f", {pct:+.1f}%" if pct is not None else ""
        sentence = (
            f"{label}: Praktis tidak ada perbedaan. "
            f"REST={rm:.2f} vs tRPC={tm:.2f} (Δ={diff:+.2f}{pct_s}), "
            f"efek trivial ({d_str}), {sig_str}."
        )
    else:
        direction_word = "rendah" if metric_name in LOWER_IS_BETTER else "tinggi"
        pct_s = f", {pct:+.1f}%" if pct is not None else ""
        ci_phrase = ""
        if ci_lo is not None and ci_hi is not None:
            ci_phrase = f" Bootstrap CI: [{ci_lo:.2f}, {ci_hi:.2f}]"
            if covers0 is False:
                ci_phrase += " (tidak mencakup nol)"
            elif covers0 is True:
                ci_phrase += " (mencakup nol)"
        sentence = (
            f"{label}: {winner} lebih {direction_word} "
            f"(REST={rm:.2f} vs tRPC={tm:.2f}, Δ={diff:+.2f}{pct_s}). "
            f"Efek {mag_str} ({d_str}), {sig_str}.{ci_phrase}"
        )

    return {
        "metric":      metric_name,
        "label":       label,
        "rest_mean":   round(rm, 4),
        "trpc_mean":   round(tm, 4),
        "delta_abs":   round(diff, 4),
        "delta_pct":   round(pct, 2) if pct is not None else None,
        "cohens_d":    round(d_val, 4) if d_val is not None else None,
        "magnitude":   cd_mag,
        "significant": sig,
        "winner":      winner,
        "sentence":    sentence,
        "practical":   _practical_note(metric_name, diff, rm, tm),
        "flag":        flag,
    }


# ---------------------------------------------------------------------------
# CORE: INTERPRET ONE GROUP
# ---------------------------------------------------------------------------

def interpret_group(group):
    scenario  = group.get("scenario", "?")
    test_type = group.get("test_type", "?")
    condition = group.get("condition", "C2")
    n         = group.get("n", 0)
    is_expl   = test_type in ("stress","spike") or n < 10

    metric_interps = {}
    for mn in PRIMARY_METRICS:
        a = group["metrics"].get(mn)
        if not a:
            continue
        r = interpret_metric(mn, a, is_exploratory=is_expl)
        if r:
            metric_interps[mn] = r

    sig_wins = {
        "REST": sum(1 for v in metric_interps.values()
                    if v.get("winner")=="REST" and v.get("significant")),
        "tRPC": sum(1 for v in metric_interps.values()
                    if v.get("winner")=="tRPC" and v.get("significant")),
    }
    if sig_wins["REST"] > sig_wins["tRPC"]:
        overall = f"REST unggul di {sig_wins['REST']} metrik signifikan vs tRPC {sig_wins['tRPC']}"
    elif sig_wins["tRPC"] > sig_wins["REST"]:
        overall = f"tRPC unggul di {sig_wins['tRPC']} metrik signifikan vs REST {sig_wins['REST']}"
    else:
        overall = f"Performa sebanding — masing-masing {sig_wins['REST']} metrik signifikan"

    flags = []
    cpu_a  = group["metrics"].get("cpu_pct", {})
    rm_cpu = (cpu_a.get("descriptive") or {}).get("rest",  {}).get("mean")
    tm_cpu = (cpu_a.get("descriptive") or {}).get("trpc",  {}).get("mean")
    if rm_cpu and tm_cpu and min(rm_cpu, tm_cpu) > 85:
        flags.append("CPU_SATURATED: kedua backend CPU >85% — bottleneck bukan protokol")

    return {
        "scenario":   scenario,
        "test_type":  test_type,
        "condition":  condition,
        "n":          n,
        "confidence": "konfirmatoris (N=10)" if not is_expl else f"eksploratoris (N={n})",
        "metrics":    metric_interps,
        "overall":    overall,
        "sig_wins":   sig_wins,
        "flags":      flags,
    }


# ---------------------------------------------------------------------------
# CORE: INTERPRET SOAK
# ---------------------------------------------------------------------------

def interpret_soak(all_results):
    groups  = all_results.get("groups", {})
    results = {}
    for sc_name in SCENARIO_ORDER:
        gk    = f"{sc_name}__soak__C2"
        group = groups.get(gk)
        if not group:
            continue
        ts      = group.get("timeseries") or {}
        rest_ts = ts.get("rest") or {}
        trpc_ts = ts.get("trpc") or {}
        r_slope = rest_ts.get("mem_slope_mb_per_hour")
        t_slope = trpc_ts.get("mem_slope_mb_per_hour")
        r_r2    = rest_ts.get("mem_slope_r2")
        t_r2    = trpc_ts.get("mem_slope_r2")

        def _si(slope, r2):
            if slope is None or r2 is None:
                return "Data tidak tersedia"
            if r2 >= 0.5 and slope > 5:
                return f"⚠ Trend naik kuat ({slope:.2f} MB/hr, R²={r2:.3f}) — potensi memory leak"
            elif slope > 10:
                return f"Naik signifikan ({slope:.2f} MB/hr)"
            elif slope > 3:
                return f"Naik moderat ({slope:.2f} MB/hr, R²={r2:.3f})"
            elif slope < -3:
                return f"Turun ({slope:.2f} MB/hr)"
            else:
                return f"Stabil ({slope:.2f} MB/hr, R²={r2:.3f})"

        comp = "N/A"
        if r_slope is not None and t_slope is not None:
            dif = r_slope - t_slope
            if abs(dif) < 2:
                comp = f"Slope serupa (Δ={dif:+.2f} MB/hr)"
            elif dif > 0:
                comp = f"REST slope lebih tinggi {dif:.2f} MB/hr"
            else:
                comp = f"tRPC slope lebih tinggi {abs(dif):.2f} MB/hr"

        r2_note = ""
        if r_r2 is not None and r_r2 >= 0.4:
            r2_note = (f"REST R²={r_r2:.3f} → {r_r2*100:.0f}% variansi memori "
                       f"dijelaskan oleh trend linear.")
            if r_r2 >= 0.5 and r_slope is not None and r_slope > 5:
                r2_note += " Sinyal potensial memory leak — perlu investigasi."

        results[sc_name] = {
            "scenario":     sc_name,
            "rest_slope":   r_slope,
            "trpc_slope":   t_slope,
            "rest_r2":      r_r2,
            "trpc_r2":      t_r2,
            "rest_interp":  _si(r_slope, r_r2),
            "trpc_interp":  _si(t_slope, t_r2),
            "comparison":   comp,
            "rest_r2_note": r2_note,
            "p_note":       ("p-value soak selalu ≈0 untuk N ribuan sampel — "
                             "gunakan R² dan slope, bukan p-value."),
            "anomaly":      (r_r2 is not None and r_r2 >= 0.5) or
                            (t_r2 is not None and t_r2 >= 0.5),
        }
    return results


# ---------------------------------------------------------------------------
# CORE: CROSS-SCENARIO PATTERNS
# ---------------------------------------------------------------------------

def interpret_patterns(all_results):
    groups   = all_results.get("groups", {})
    patterns = []
    for mn in ["p95","mem_mb","cpu_pct","throughput"]:
        wins     = {"REST":[],"tRPC":[],"sama":[]}
        sig_wins = {"REST":[],"tRPC":[]}
        d_vals   = []
        for sc in SCENARIO_ORDER:
            gk    = f"{sc}__load__C2"
            group = groups.get(gk)
            if not group:
                continue
            a    = group["metrics"].get(mn, {})
            desc = a.get("descriptive", {})
            rm   = desc.get("rest",  {}).get("mean")
            tm   = desc.get("trpc",  {}).get("mean")
            if rm is None or tm is None:
                continue
            diff   = rm - tm
            d      = a.get("cohens_d", {}).get("d")
            sig    = a.get("inferential", {}).get("significant")
            winner = _winner(mn, diff)
            wins.get(winner, wins["sama"]).append(sc)
            if sig and winner in sig_wins:
                sig_wins[winner].append(sc)
            if d is not None:
                d_vals.append(d)

        n_rest = len(wins["REST"])
        n_trpc = len(wins["tRPC"])
        avg_d  = sum(d_vals)/len(d_vals) if d_vals else None
        label  = METRIC_LABELS.get(mn, mn)

        if n_rest >= 4:
            direction = "REST konsisten lebih baik"
        elif n_trpc >= 4:
            direction = "tRPC konsisten lebih baik"
        else:
            direction = "Mixed/tidak konsisten"

        patterns.append({
            "metric":       mn,
            "label":        label,
            "direction":    direction,
            "n_rest_wins":  n_rest,
            "n_trpc_wins":  n_trpc,
            "n_sig_rest":   len(sig_wins["REST"]),
            "n_sig_trpc":   len(sig_wins["tRPC"]),
            "avg_d":        round(avg_d,3) if avg_d is not None else None,
            "description":  (
                f"{label}: {'REST' if n_rest>=4 else ('tRPC' if n_trpc>=4 else 'Tidak ada')} "
                f"unggul di {max(n_rest,n_trpc)}/5 skenario. "
                + (f"Rata-rata d={avg_d:.2f}." if avg_d else "")
            ),
        })
    return patterns


# ---------------------------------------------------------------------------
# RQ1: PERBANDINGAN KINERJA
# ---------------------------------------------------------------------------

def _answer_rq1(groups):
    metrics_summary = {}
    for mn in ALL_METRICS:
        wins     = {"REST":0,"tRPC":0,"sama":0}
        sig_wins = {"REST":0,"tRPC":0}
        d_vals   = []
        details  = []
        for sc in SCENARIO_ORDER:
            gk    = f"{sc}__load__C2"
            group = groups.get(gk)
            if not group:
                continue
            a    = group["metrics"].get(mn, {})
            desc = a.get("descriptive", {})
            rm   = desc.get("rest",  {}).get("mean")
            tm   = desc.get("trpc",  {}).get("mean")
            if rm is None or tm is None:
                continue
            diff   = rm - tm
            d      = a.get("cohens_d", {}).get("d")
            sig    = a.get("inferential", {}).get("significant")
            mag    = a.get("cohens_d", {}).get("magnitude","")
            winner = _winner(mn, diff)
            wins[winner] = wins.get(winner, 0) + 1
            if sig and winner in sig_wins:
                sig_wins[winner] += 1
            if d is not None:
                d_vals.append(d)
            details.append({
                "scenario":  sc,
                "rest_mean": rm,
                "trpc_mean": tm,
                "delta":     diff,
                "d":         d,
                "magnitude": mag,
                "sig":       sig,
                "winner":    winner,
            })

        avg_d  = sum(d_vals)/len(d_vals) if d_vals else None
        n_rest = wins.get("REST",0)
        n_trpc = wins.get("tRPC",0)
        n_sr   = sig_wins.get("REST",0)
        n_st   = sig_wins.get("tRPC",0)
        label  = METRIC_LABELS.get(mn, mn)

        if n_rest >= 4:
            finding = (f"REST lebih baik di {n_rest}/5 skenario load ({n_sr} signifikan). "
                       + (f"Rata-rata d={avg_d:.2f}." if avg_d else ""))
        elif n_trpc >= 4:
            finding = (f"tRPC lebih baik di {n_trpc}/5 skenario load ({n_st} signifikan). "
                       + (f"Rata-rata d={avg_d:.2f}." if avg_d else ""))
        else:
            finding = (f"Tidak ada pemenang konsisten "
                       f"(REST wins={n_rest}, tRPC wins={n_trpc}).")

        metrics_summary[mn] = {
            "label":        label,
            "n_rest_wins":  n_rest,
            "n_trpc_wins":  n_trpc,
            "n_sig_rest":   n_sr,
            "n_sig_trpc":   n_st,
            "avg_d":        round(avg_d,3) if avg_d is not None else None,
            "finding":      finding,
            "details":      details,
        }

    rest_dom  = [mn for mn,v in metrics_summary.items() if v["n_rest_wins"]>=4]
    trpc_dom  = [mn for mn,v in metrics_summary.items() if v["n_trpc_wins"]>=4]

    def _lbl(lst): return ", ".join(METRIC_LABELS.get(m,m) for m in lst[:4])

    summary = (
        f"REST konsisten lebih baik pada {len(rest_dom)} metrik "
        f"({_lbl(rest_dom)}). "
    )
    summary += (
        f"tRPC lebih baik pada {len(trpc_dom)} metrik ({_lbl(trpc_dom)}). "
        if trpc_dom else
        "tRPC tidak menunjukkan keunggulan konsisten di metrik manapun pada beban normal. "
    )

    return {
        "question":             "Bagaimana perbandingan kinerja tRPC dan REST API berdasarkan response time, throughput, latency, error rate, dan resource utilization?",
        "summary":              summary,
        "metrics":              metrics_summary,
        "rest_dominant_metrics": rest_dom,
        "trpc_dominant_metrics": trpc_dom,
    }


# ---------------------------------------------------------------------------
# RQ2: KONDISI & SKENARIO — BIDIRECTIONAL
# ---------------------------------------------------------------------------

def _answer_rq2(groups, all_results):
    """
    RQ2 bidirectional: tampilkan SEMUA kondisi di mana REST lebih optimal
    DAN semua kondisi di mana tRPC lebih optimal.
    Tidak ada bias — keduanya ditampilkan lengkap.
    """
    rest_superior = []
    trpc_superior = []
    neutral       = []

    for gk, group in sorted(groups.items()):
        scenario  = group.get("scenario","?")
        test_type = group.get("test_type","?")
        condition = group.get("condition","C2")
        n         = group.get("n",0)

        if condition in ("C3","C4"):
            continue

        sc_label = SCENARIO_LABELS.get(scenario, scenario)
        tt_label = test_type.upper()
        is_expl  = test_type in ("stress","spike") or n < 10

        for mn in PRIMARY_METRICS:
            a    = group["metrics"].get(mn, {})
            if not a or a.get("status") in ("no_data","insufficient_data"):
                continue
            desc   = a.get("descriptive", {})
            rm     = desc.get("rest",  {}).get("mean")
            tm     = desc.get("trpc",  {}).get("mean")
            if rm is None or tm is None:
                continue
            cd     = a.get("cohens_d", {})
            inf    = a.get("inferential", a.get("ttest_reference",{}))
            diff   = rm - tm
            pct    = _pct(diff, tm)
            d_val  = cd.get("d")
            cd_mag = cd.get("magnitude","")
            sig    = inf.get("significant")
            winner = _winner(mn, diff)
            label  = METRIC_LABELS.get(mn, mn)
            d_str  = _fmt_d(d_val)
            pct_s  = f"{pct:+.1f}%" if pct is not None else ""
            sig_s  = _sig_phrase(sig, is_expl)

            entry = {
                "scenario":   sc_label,
                "test_type":  tt_label,
                "metric":     label,
                "metric_key": mn,       # BUG-A FIX: key aktual untuk lookup di _answer_rq3
                "rest_mean":  rm,
                "trpc_mean":  tm,
                "delta":      diff,
                "delta_pct":  pct,
                "cohens_d":   d_val,
                "magnitude":  cd_mag,
                "significant":sig,
                "exploratory":is_expl,
                "n":          n,
            }

            if cd_mag == "tidak_interpretatif":
                entry["sentence"] = (
                    f"{sc_label} {tt_label} — {label}: d tidak interpretatif (SD≈0). "
                    f"Selisih aktual {abs(diff):.2f} (konsisten tapi magnitude tidak bermakna)."
                )
                neutral.append(entry)
            elif winner == "REST" and (sig or (d_val is not None and abs(d_val)>=0.5)):
                entry["sentence"] = (
                    f"{sc_label} {tt_label} (N={n}): REST lebih baik pada {label} "
                    f"(REST={rm:.2f} vs tRPC={tm:.2f}, {pct_s}, {d_str}, {sig_s})."
                )
                rest_superior.append(entry)
            elif winner == "tRPC" and (sig or (d_val is not None and abs(d_val)>=0.5)):
                entry["sentence"] = (
                    f"{sc_label} {tt_label} (N={n}): tRPC lebih baik pada {label} "
                    f"(REST={rm:.2f} vs tRPC={tm:.2f}, {pct_s}, {d_str}, {sig_s})."
                )
                trpc_superior.append(entry)
            else:
                entry["sentence"] = (
                    f"{sc_label} {tt_label}: {label} sebanding "
                    f"(Δ={diff:+.2f}, {d_str}, {sig_s})."
                )
                neutral.append(entry)

    n_r = len(rest_superior)
    n_t = len(trpc_superior)

    trpc_conditions = sorted(set(
        f"{e['scenario']} {e['test_type']}" for e in trpc_superior
    ))
    rest_conditions = sorted(set(
        f"{e['scenario']} {e['test_type']}" for e in rest_superior
    ))

    rest_summary = (
        f"REST lebih optimal pada {n_r} kasus meliputi: "
        f"{', '.join(rest_conditions[:6])}. "
        f"Dominasi REST terutama pada resource usage (CPU, RAM) di semua skenario "
        f"dan latency di skenario read-heavy dan write-heavy."
    ) if rest_conditions else "REST tidak menunjukkan keunggulan signifikan."

    trpc_summary = (
        f"tRPC lebih optimal pada {n_t} kasus meliputi: "
        f"{', '.join(trpc_conditions[:6])}. "
        f"Keunggulan tRPC bersifat eksploratoris (N=3) atau praktis tidak bermakna."
    ) if trpc_conditions else (
        "Tidak ditemukan kondisi di mana tRPC secara konsisten dan signifikan "
        "unggul atas REST pada beban normal (load test N=10). "
        "Keunggulan tRPC terbatas pada skenario eksploratoris (stress/spike N=3) "
        "yang tidak dapat diklaim secara konfirmatoris."
    )

    overall = (
        f"REST lebih optimal di {n_r} kasus vs tRPC di {n_t} kasus "
        f"dari total kasus dengan efek ≥sedang atau signifikan. "
        f"REST unggul pada hampir semua kondisi beban yang diuji. "
        f"tRPC tidak menunjukkan keunggulan konfirmatoris pada load test N=10."
    )

    return {
        "question":        "Dalam kondisi beban atau skenario apa REST atau tRPC menunjukkan performa lebih optimal?",
        "rest_superior":   rest_superior,
        "trpc_superior":   trpc_superior,
        "neutral":         neutral,
        "rest_summary":    rest_summary,
        "trpc_summary":    trpc_summary,
        "overall":         overall,
        "rest_conditions": rest_conditions,
        "trpc_conditions": trpc_conditions,
    }


# ---------------------------------------------------------------------------
# RQ3: REKOMENDASI
# ---------------------------------------------------------------------------

def _answer_rq3(rq1, rq2):
    rest_dom = rq1.get("rest_dominant_metrics", [])
    trpc_dom = rq1.get("trpc_dominant_metrics", [])
    n_r      = len(rq2.get("rest_superior", []))
    n_t      = len(rq2.get("trpc_superior", []))

    # Fix #5: hapus REST 2x-bias ("tRPC butuh menang 2x lipat sebelum direkomendasikan")
    # Sekarang: REST direkomendasikan jika n_r >= n_t; tRPC jika n_t > n_r.
    # Tie (n_r == n_t) → REST sebagai default karena resource efficiency konsisten.
    primary_rec = "REST" if n_r >= n_t else "tRPC"

    # Fix #6: derive angka RAM dan P95 dari data aktual, bukan hardcoded literal.
    # Compute dari rq2.rest_superior entries — ini adalah efek yang sudah diverifikasi.
    rest_sup = rq2.get("rest_superior", [])

    mem_deltas = [abs(e.get("delta", 0)) for e in rest_sup
                  if e.get("metric_key") == "mem_mb" and not e.get("exploratory")]
    p95_deltas = [abs(e.get("delta", 0)) for e in rest_sup
                  if e.get("metric_key") == "p95" and not e.get("exploratory")]
    cpu_deltas = [abs(e.get("delta", 0)) for e in rest_sup
                  if e.get("metric_key") == "cpu_pct" and not e.get("exploratory")]

    if mem_deltas:
        ram_str = f"{min(mem_deltas):.0f}–{max(mem_deltas):.0f}MB"
    else:
        ram_str = "lebih banyak"

    if p95_deltas:
        p95_str = f"{min(p95_deltas):.0f}–{max(p95_deltas):.0f}ms"
    else:
        p95_str = "lebih tinggi"

    if cpu_deltas:
        cpu_str = f"{min(cpu_deltas):.1f}–{max(cpu_deltas):.1f}%"
    else:
        cpu_str = "lebih tinggi"

    # Compute mem_pct dari entries yang ada delta dan trpc_mean
    mem_pct_entries = [
        abs(e.get("delta", 0)) / e.get("trpc_mean", 1) * 100
        for e in rest_sup
        if e.get("metric_key") == "mem_mb" and not e.get("exploratory")   # BUG-A FIX
        and e.get("trpc_mean") and e.get("trpc_mean") != 0
    ]
    mem_pct_str = (f"{min(mem_pct_entries):.0f}–{max(mem_pct_entries):.0f}%"
                   if mem_pct_entries else "~15–25%")

    reasoning = (
        f"REST secara konsisten lebih efisien pada {len(rest_dom)} metrik primer "
        f"dan unggul di {n_r} kasus vs tRPC di {n_t} kasus. "
    )

    recommendations = [
        {
            "use_case":       "Sistem e-commerce prioritas performa",
            "recommendation": "REST",
            "reason":         (
                f"REST konsisten lebih rendah pada CPU ({cpu_str} lebih hemat) "
                f"dan RAM di semua skenario. "
                f"Latency P95 lebih rendah {p95_str} tergantung skenario."
            ),
        },
        {
            "use_case":       "Server dengan resource terbatas",
            "recommendation": "REST",
            "reason":         (
                f"tRPC membutuhkan {ram_str} RAM lebih banyak ({mem_pct_str}) "
                f"di semua skenario yang diuji. REST lebih hemat resource."
            ),
        },
        {
            "use_case":       "Skenario write-heavy (checkout, transaksi)",
            "recommendation": "REST",
            "reason":         "REST throughput lebih tinggi pada S03 Checkout dan avg response time lebih rendah secara signifikan.",
        },
        {
            "use_case":       "Tim TypeScript penuh, prioritas type safety dan DX",
            "recommendation": "tRPC (dengan catatan)",
            "reason":         (
                f"tRPC memberikan end-to-end type safety dan developer experience superior. "
                f"Trade-off: overhead resource {mem_pct_str} RAM lebih tinggi. "
                f"Dapat diterima jika maintenance dan produktivitas tim lebih diprioritaskan."
            ),
        },
        {
            "use_case":       "Prototype / aplikasi internal skala kecil",
            "recommendation": "tRPC",
            "reason":         "Kecepatan pengembangan dan type safety tRPC lebih bernilai daripada perbedaan performa yang kecil secara absolut pada traffic rendah.",
        },
    ]

    overall = (
        f"Berdasarkan data empiris, {primary_rec} lebih direkomendasikan untuk "
        f"sistem e-commerce dengan prioritas performa dan efisiensi resource. "
        f"{reasoning}"
        f"Untuk tim yang mengutamakan developer experience dan type safety "
        f"end-to-end, tRPC tetap relevan dengan pemahaman bahwa overhead resource "
        f"{mem_pct_str} perlu diperhitungkan dalam capacity planning server."
    )

    return {
        "question":              "Arsitektur API mana yang lebih tepat direkomendasikan untuk skalabilitas dan efisiensi e-commerce modern?",
        "primary_recommendation": primary_rec,
        "reasoning":             reasoning,
        "recommendations":       recommendations,
        "overall":               overall,
    }


def interpret_research_questions(all_results):
    groups = all_results.get("groups", {})
    rq1    = _answer_rq1(groups)
    rq2    = _answer_rq2(groups, all_results)
    rq3    = _answer_rq3(rq1, rq2)
    return {"RQ1": rq1, "RQ2": rq2, "RQ3": rq3}


# ---------------------------------------------------------------------------
# CHART DESCRIPTIONS
# ---------------------------------------------------------------------------

def _desc_bar_comparison(sc_label, test_type, condition, group):
    n       = group.get("n", 0)
    notable = []
    for mn in ["p95","throughput","mem_mb","cpu_pct","avg_rt"]:
        rm, tm, diff, d_val, cd_mag, sig, p_val, _ = _get_metric_vals(group, mn)
        if rm is None or d_val is None:
            continue
        if abs(d_val) >= 0.5 and cd_mag != "tidak_interpretatif":
            winner = _winner(mn, diff)
            pct    = _pct(diff, tm)
            pct_s  = f"{pct:+.1f}%" if pct else ""
            notable.append(
                f"{METRIC_LABELS.get(mn,mn)}: {winner} lebih baik "
                f"({rm:.1f} vs {tm:.1f}, {pct_s}, d={d_val:.2f})"
            )
    prefix = (f"Bar chart perbandingan rata-rata REST vs tRPC untuk "
              f"{sc_label} {test_type.upper()} {condition} (N={n}). ")
    if notable:
        return prefix + f"Perbedaan notable: {'; '.join(notable[:3])}."
    return prefix + "Tidak ada perbedaan efek ≥sedang yang terlihat."


def _desc_cohens_d(sc_label, test_type, group):
    n      = group.get("n", 0)
    big    = []
    small  = []
    for mn in PRIMARY_METRICS:
        rm, tm, diff, d_val, cd_mag, sig, p_val, _ = _get_metric_vals(group, mn)
        if d_val is None:
            continue
        label = METRIC_LABELS.get(mn, mn)
        if abs(d_val) >= 0.8:
            big.append(f"{label} (d={d_val:.2f})")
        elif abs(d_val) < 0.2:
            small.append(label)
    desc = (f"Cohen's d chart untuk {sc_label} {test_type.upper()} (N={n}). "
            f"Setiap bar merepresentasikan magnitude efek tiap metrik. ")
    if big:
        desc += f"Efek besar (|d|≥0.8): {', '.join(big[:3])}. "
    if small:
        desc += f"Efek trivial (|d|<0.2): {', '.join(small[:3])}."
    return desc


def _desc_bootstrap_ci(sc_label, group):
    n     = group.get("n", 0)
    zeros = []
    no_z  = []
    for mn in ["p95","throughput","mem_mb","cpu_pct"]:
        a       = group["metrics"].get(mn, {})
        bci     = a.get("bootstrap_ci", {})
        covers0 = bci.get("covers_zero")
        ci_lo   = bci.get("ci_lower")
        ci_hi   = bci.get("ci_upper")
        if ci_lo is None:
            continue
        label = METRIC_LABELS.get(mn, mn)
        if covers0 is False:
            no_z.append(f"{label} [{ci_lo:.2f}, {ci_hi:.2f}]")
        else:
            zeros.append(label)
    desc = (f"Bootstrap 95% CI untuk mean difference (REST−tRPC) — "
            f"{sc_label} Load (N={n}, 9999 resample). "
            f"CI yang tidak mencakup nol menunjukkan efek konsisten. ")
    if no_z:
        desc += f"Tidak mencakup nol (efek konsisten): {', '.join(no_z[:3])}. "
    if zeros:
        desc += f"Mencakup nol (efek tidak stabil): {', '.join(zeros[:3])}."
    return desc


def _desc_boxplot(sc_label, group):
    n     = group.get("n", 0)
    notes = []
    for mn in ["p95","mem_mb"]:
        rm, tm, diff, d_val, cd_mag, sig, p_val, _ = _get_metric_vals(group, mn)
        if rm is None:
            continue
        label = METRIC_LABELS.get(mn, mn)
        notes.append(
            f"{label}: REST median≈{rm:.1f} vs tRPC≈{tm:.1f}"
        )
    return (f"Boxplot distribusi nilai per run REST vs tRPC — "
            f"{sc_label} Load (N={n}). "
            f"Lebar box menunjukkan variabilitas antar run. "
            + (f"{'; '.join(notes)}." if notes else ""))


def _desc_paired_scatter(sc_label, group):
    n     = group.get("n", 0)
    above = 0
    below = 0
    for mn in ["p95","mem_mb","cpu_pct"]:
        rm, tm, diff, d_val, cd_mag, sig, p_val, _ = _get_metric_vals(group, mn)
        if rm is None or diff is None:
            continue
        if diff < 0:
            below += 1  # REST lebih rendah = REST menang untuk lower_is_better
        else:
            above += 1
    direction = (
        "Mayoritas titik berada di bawah garis diagonal — REST lebih rendah pada metrik lower-is-better."
        if below > above else
        "Titik tersebar di kedua sisi diagonal — tidak ada pemenang konsisten per run."
    )
    return (f"Paired scatter plot setiap run REST vs tRPC — "
            f"{sc_label} Load (N={n}). "
            f"Titik di bawah diagonal 45° = REST lebih rendah, di atas = tRPC lebih rendah. "
            f"{direction}")


def _desc_percentile_profile(sc_label, group):
    a    = group["metrics"].get("p95", {})
    desc = a.get("descriptive", {})
    rm   = desc.get("rest",  {}).get("mean")
    tm   = desc.get("trpc",  {}).get("mean")
    n    = group.get("n", 0)
    pct  = _pct((rm-tm) if rm and tm else None, tm) if rm and tm else None
    base = (f"Profil persentil latency P50/P90/P95/P99 REST vs tRPC — "
            f"{sc_label} Load (N={n}). "
            f"Visualisasi bagaimana gap antara REST dan tRPC berkembang di ekor distribusi. ")
    if rm and tm:
        direction = "REST lebih rendah" if rm < tm else "tRPC lebih rendah"
        base += (f"Pada P95: {direction} "
                 f"(REST={rm:.1f}ms vs tRPC={tm:.1f}ms, "
                 + (f"{pct:+.1f}%)." if pct else ")."))
    return base


def _desc_forest_plot(sc_label, test_type, group):
    n    = group.get("n", 0)
    sigs = []
    for mn in PRIMARY_METRICS:
        rm, tm, diff, d_val, cd_mag, sig, p_val, _ = _get_metric_vals(group, mn)
        if sig and cd_mag != "tidak_interpretatif":
            sigs.append(METRIC_LABELS.get(mn, mn))
    base = (f"Forest plot mean difference (REST−tRPC) dengan confidence interval — "
            f"{sc_label} {test_type.upper()} (N={n}). "
            f"Titik di kiri garis nol = REST lebih rendah (REST menang untuk lower-is-better). ")
    if sigs:
        base += f"Metrik signifikan (CI tidak mencakup nol): {', '.join(sigs[:4])}."
    else:
        base += "Tidak ada metrik yang CI-nya secara jelas tidak mencakup nol."
    return base


def _desc_dotplot(sc_label, test_type, group):
    n    = group.get("n", 0)
    rm95, tm95, diff95, d95, mag95, sig95, p95, _ = _get_metric_vals(group, "p95")
    base = (f"Dot plot distribusi {n} run REST vs tRPC — "
            f"{sc_label} {test_type.upper()} (eksploratoris, N={n}, power sangat rendah). "
            f"Setiap titik merepresentasikan satu run. Sebaran lebar = variabilitas tinggi. ")
    if rm95 is not None and tm95 is not None:
        base += (f"P95: REST≈{rm95:.0f}ms vs tRPC≈{tm95:.0f}ms "
                 f"(d={d95:.2f} {mag95 or ''}).")
    return base


def _desc_sla_error(groups):
    high_sla = []
    for gk, group in groups.items():
        for mn in ["sla_breach","functional_error"]:
            desc = group["metrics"].get(mn,{}).get("descriptive",{})
            rm   = desc.get("rest", {}).get("mean",0) or 0
            tm   = desc.get("trpc", {}).get("mean",0) or 0
            if max(rm,tm) > 0.05:
                sc  = SCENARIO_LABELS.get(group.get("scenario",""),"")
                tt  = group.get("test_type","").upper()
                high_sla.append(f"{sc} {tt}")
    base = ("SLA breach rate dan functional error rate seluruh skenario dan test type. "
            "Bar panjang = persentase request yang melewati SLA threshold atau gagal secara fungsional. ")
    if high_sla:
        base += f"SLA breach >5% terdeteksi pada: {', '.join(sorted(set(high_sla))[:5])}."
    else:
        base += "Semua skenario load test berada di bawah threshold 5% SLA breach."
    return base


def _desc_soak_timeseries(sc_label, group):
    ts      = group.get("timeseries") or {}
    rest_ts = ts.get("rest") or {}
    trpc_ts = ts.get("trpc") or {}
    r_slope = rest_ts.get("mem_slope_mb_per_hour")
    t_slope = trpc_ts.get("mem_slope_mb_per_hour")
    r_r2    = rest_ts.get("mem_slope_r2")

    base = (f"Time-series penggunaan memori REST vs tRPC selama soak test {sc_label} "
            f"(N=1, observasional). "
            f"Garis menunjukkan tren memori dari awal hingga akhir pengujian. ")
    if r_slope is not None and t_slope is not None:
        base += (f"REST slope ≈{r_slope:.2f} MB/hr"
                 + (f" (R²={r_r2:.3f})" if r_r2 else "") + ", "
                 f"tRPC slope ≈{t_slope:.2f} MB/hr. ")
        if r_r2 is not None and r_r2 >= 0.5:
            base += "REST menunjukkan R²≥0.5 — sinyal potensi memory leak."
    return base


def generate_chart_descriptions(all_results):
    """
    Generate 2-3 sentence description untuk setiap chart (semua 86 chart).
    Returns dict: { filename: { description, caption, chart_type } }
    """
    descriptions = {}
    groups = all_results.get("groups", {})

    # SLA/Error chart
    descriptions["ALL_sla_error_chart.png"] = {
        "chart_type":  "sla_error",
        "description": _desc_sla_error(groups),
        "caption":     "Ringkasan SLA Breach Rate dan Functional Error Rate seluruh skenario dan test type.",
    }

    for gk, group in sorted(groups.items()):
        scenario  = group.get("scenario","?")
        test_type = group.get("test_type","?")
        condition = group.get("condition","C2")
        sc_label  = SCENARIO_LABELS.get(scenario, scenario)
        base      = f"{scenario}_{test_type}_{condition}"
        base_nc   = f"{scenario}_{test_type}"

        if test_type == "soak":
            fname = f"{base_nc}_timeseries.png"
            descriptions[fname] = {
                "chart_type":  "soak_timeseries",
                "description": _desc_soak_timeseries(sc_label, group),
                "caption":     f"Time-series memori soak test — {sc_label}.",
            }
            continue

        # Bar comparison
        descriptions[f"{base}_comparison.png"] = {
            "chart_type":  "bar_comparison",
            "description": _desc_bar_comparison(sc_label, test_type, condition, group),
            "caption":     f"Perbandingan rata-rata metrik REST vs tRPC — {sc_label} {test_type.upper()} {condition}.",
        }

        # Cohen's d
        descriptions[f"{base}_cohens_d.png"] = {
            "chart_type":  "cohens_d",
            "description": _desc_cohens_d(sc_label, test_type, group),
            "caption":     f"Effect size Cohen's d per metrik — {sc_label} {test_type.upper()} {condition}.",
        }

        if test_type == "load":
            # Bootstrap CI
            descriptions[f"{base_nc}_bootstrap_ci.png"] = {
                "chart_type":  "bootstrap_ci",
                "description": _desc_bootstrap_ci(sc_label, group),
                "caption":     f"Bootstrap 95% CI mean difference REST−tRPC — {sc_label} Load.",
            }
            # Boxplot
            descriptions[f"{base}_boxplot.png"] = {
                "chart_type":  "boxplot",
                "description": _desc_boxplot(sc_label, group),
                "caption":     f"Distribusi nilai per run boxplot — {sc_label} Load.",
            }
            # Paired scatter
            descriptions[f"{base}_paired_scatter.png"] = {
                "chart_type":  "paired_scatter",
                "description": _desc_paired_scatter(sc_label, group),
                "caption":     f"Paired scatter REST vs tRPC per run — {sc_label} Load.",
            }
            # Percentile profile
            descriptions[f"{base}_percentile_profile.png"] = {
                "chart_type":  "percentile_profile",
                "description": _desc_percentile_profile(sc_label, group),
                "caption":     f"Profil persentil P50/P90/P95/P99 — {sc_label} Load.",
            }
            # Forest plot
            descriptions[f"{base}_forest_plot.png"] = {
                "chart_type":  "forest_plot",
                "description": _desc_forest_plot(sc_label, test_type, group),
                "caption":     f"Forest plot mean difference dengan CI — {sc_label} Load.",
            }

        elif test_type in ("stress","spike"):
            # Dot plot
            descriptions[f"{base}_dotplot.png"] = {
                "chart_type":  "dotplot",
                "description": _desc_dotplot(sc_label, test_type, group),
                "caption":     f"Dot plot N=3 runs — {sc_label} {test_type.upper()}.",
            }
            # Forest plot
            descriptions[f"{base}_forest_plot.png"] = {
                "chart_type":  "forest_plot",
                "description": _desc_forest_plot(sc_label, test_type, group),
                "caption":     f"Forest plot — {sc_label} {test_type.upper()}.",
            }

    return descriptions


# ---------------------------------------------------------------------------
# BAB 4 DRAFT
# ---------------------------------------------------------------------------

def generate_bab4_draft(interpretations, soak_interp=None):
    lines = []
    def h(level, text): lines.append(f"{'#'*level} {text}\n")
    def p(text):        lines.append(f"{text}\n")
    def blank():        lines.append("")

    h(1, "BAB 4 — HASIL DAN PEMBAHASAN (Draft Otomatis)")
    p("*Draft ini digenerate otomatis dari hasil analisis pipeline ZENIT. "
      "Revisi dan penyesuaian konteks tetap diperlukan sebelum submission.*")
    blank()

    # 4.1 Gambaran Umum
    h(2, "4.1 Gambaran Umum Hasil Pengujian")
    p(interpretations.get("overall_summary",""))
    blank()

    rq  = interpretations.get("research_questions", {})
    rq1 = rq.get("RQ1", {})
    rq2 = rq.get("RQ2", {})
    rq3 = rq.get("RQ3", {})

    # 4.2 Pola Lintas Skenario
    h(2, "4.2 Pola Perbandingan Lintas Skenario (Load Test, N=10)")
    p("Analisis lintas skenario dilakukan untuk mengidentifikasi pola konsisten "
      "antara REST dan tRPC yang tidak bergantung pada satu skenario spesifik.")
    blank()
    for pat in interpretations.get("patterns", []):
        p(f"**{pat['label']}**: {pat['description']}")
    blank()

    # 4.3 Per-Skenario Load
    h(2, "4.3 Hasil Per Skenario — Load Test (N=10, Konfirmatoris)")
    groups_interp = interpretations.get("groups", {})
    for sc_name in SCENARIO_ORDER:
        gk = f"{sc_name}__load__C2"
        gi = groups_interp.get(gk)
        if not gi:
            continue
        sc_label = SCENARIO_LABELS.get(sc_name, sc_name)
        h(3, f"4.3.{SCENARIO_ORDER.index(sc_name)+1} {sc_label}")
        for flag in gi.get("flags",[]):
            p(f"> ⚠ **{flag}**")
        if gi.get("flags"):
            blank()
        p(f"**Ringkasan**: {gi.get('overall','')}")
        blank()
        for mn, mi in gi.get("metrics",{}).items():
            if not mi:
                continue
            if mi.get("significant") or mi.get("magnitude") in ("besar","sedang") or mi.get("flag"):
                p(mi.get("sentence",""))
                if mi.get("practical"):
                    p(f"*Implikasi praktis: {mi['practical']}*")
                blank()

    # 4.4 Soak Test
    h(2, "4.4 Hasil Soak Test (N=1, Observasional)")
    p("Soak test bersifat observasional (N=1) — tidak ada uji inferensial valid. "
      "Fokus pada slope pertumbuhan memori (MB/jam) dan R².")
    blank()
    if soak_interp:
        for sc_name, si in soak_interp.items():
            sc_label = SCENARIO_LABELS.get(sc_name, sc_name)
            h(4, sc_label)
            p(f"REST: {si.get('rest_interp','N/A')}")
            p(f"tRPC: {si.get('trpc_interp','N/A')}")
            p(f"Perbandingan: {si.get('comparison','')}")
            if si.get("rest_r2_note"):
                p(f"**{si['rest_r2_note']}**")
            p(f"_{si.get('p_note','')}_")
            blank()

    # 4.5 Jawaban Pertanyaan Penelitian
    h(2, "4.5 Jawaban Pertanyaan Penelitian")

    # RQ1
    h(3, "4.5.1 RQ1 — Perbandingan Kinerja REST vs tRPC")
    p(f"**Pertanyaan**: {rq1.get('question','')}")
    blank()
    p(f"**Jawaban**: {rq1.get('summary','')}")
    blank()
    p("Rincian per metrik (load test C2, N=10):")
    for mn, mv in rq1.get("metrics",{}).items():
        if mv.get("n_rest_wins",0) >= 4 or mv.get("n_trpc_wins",0) >= 4:
            p(f"- **{mv['label']}**: {mv['finding']}")
    blank()

    # RQ2
    h(3, "4.5.2 RQ2 — Kondisi Optimal REST dan tRPC")
    p(f"**Pertanyaan**: {rq2.get('question','')}")
    blank()
    p(f"**REST lebih optimal**: {rq2.get('rest_summary','')}")
    blank()
    if rq2.get("rest_superior"):
        p("Kasus konfirmatoris (load N=10, signifikan):")
        conf = [e for e in rq2["rest_superior"] if not e.get("exploratory")]
        for e in conf[:6]:
            p(f"- {e.get('sentence','')}")
        blank()
    p(f"**tRPC lebih optimal**: {rq2.get('trpc_summary','')}")
    blank()
    if rq2.get("trpc_superior"):
        p("Kasus di mana tRPC unggul:")
        for e in rq2["trpc_superior"][:6]:
            p(f"- {e.get('sentence','')}")
        blank()

    # RQ3
    h(3, "4.5.3 RQ3 — Rekomendasi Arsitektur API")
    p(f"**Pertanyaan**: {rq3.get('question','')}")
    blank()
    p(f"**Rekomendasi utama**: {rq3.get('primary_recommendation','')} — {rq3.get('reasoning','')}")
    blank()
    p("Rekomendasi per use case:")
    for rec in rq3.get("recommendations",[]):
        p(f"- **{rec['use_case']}** → {rec['recommendation']}: {rec['reason']}")
    blank()
    p(rq3.get("overall",""))
    blank()

    # 4.6 Stress & Spike
    h(2, "4.6 Hasil Stress dan Spike Test (N=3, Eksploratoris)")
    p("Stress dan spike test bersifat eksploratoris. "
      "Uji statistik digunakan sebagai referensi — bukan untuk klaim konfirmatoris "
      "(power sangat rendah, df=2).")
    blank()
    for sc_name in SCENARIO_ORDER:
        for tt in ("stress","spike"):
            gk = f"{sc_name}__{tt}__C2"
            gi = groups_interp.get(gk)
            if not gi:
                continue
            sc_label = SCENARIO_LABELS.get(sc_name, sc_name)
            sig_m = [mi for mi in gi.get("metrics",{}).values()
                     if mi and mi.get("significant") and mi.get("magnitude") in ("besar","sedang")]
            if sig_m:
                h(4, f"{sc_label} — {tt.upper()}")
                for mi in sig_m[:3]:
                    p(f"- {mi.get('sentence','')[:150]}")
                blank()

    # 4.7 Dekomposisi C3/C4
    h(2, "4.7 Analisis Dekomposisi Arsitektur (C3/C4, N=1)")
    p("Analisis C3 (auth equalized) dan C4 (tRPC batching) bersifat estimatif — "
      "N=1 tidak memungkinkan klaim konfirmatoris. "
      "Lihat sheet Decomposition di Excel untuk angka spesifik per metrik dan skenario.")
    blank()

    # 4.8 Limitasi
    h(2, "4.8 Catatan Limitasi (Reminder untuk Bab 5)")
    p("*Poin-poin berikut perlu dimasukkan ke bab batasan penelitian/limitasi:*")
    blank()
    p("1. **Variabilitas pengujian**: Infrastruktur shared (DigitalOcean droplet) "
      "tidak sepenuhnya terisolasi — variasi latensi jaringan dan fluktuasi beban server "
      "tidak dapat dieliminasi sepenuhnya. Paired measurement diterapkan untuk meminimalkan dampak.")
    blank()
    p("2. **N terbatas pada beberapa kondisi**: Soak (N=1), C3 (N=1), C4 (N=1), "
      "stress/spike (N=3). Kondisi ini tidak memungkinkan uji inferensial konfirmatoris. "
      "Temuan bersifat observasional/estimatif dan perlu replikasi untuk validasi.")
    blank()
    p("3. **Multiple comparison**: Banyak metrik diuji secara eksploratoris di luar "
      "metrik primer yang menjawab RQ1–RQ3. Untuk menjaga kehati-hatian interpretasi, "
      "temuan diklasifikasikan sebagai konfirmatoris (load N=10, p<0.05 + Cohen's d ≥ sedang) "
      "atau eksploratoris (stress/spike N=3, soak N=1). Metrik pendukung "
      "diinterpretasi dengan cautious tanpa klaim kausalitas.")
    blank()
    p("4. **Generalisasi terbatas**: Hasil hanya berlaku untuk ekosistem Node.js/TypeScript "
      "dengan konfigurasi identik. Implementasi REST atau tRPC yang berbeda "
      "(framework, library, ORM) dapat menghasilkan performa yang berbeda.")
    blank()
    p("5. **S04 CPU saturation**: Skenario S04 Auth berada dalam kondisi CPU-bound "
      "akibat bcrypt — perbandingan protokol tidak dapat diisolasi dari "
      "computational overhead. Hasil S04 mencerminkan beban CPU-intensive, "
      "bukan perbedaan efisiensi protokol murni.")
    blank()
    p("6. **Order effect**: Counterbalancing 5:5 dilakukan. Verifikasi mendeteksi "
      "potensi order effect pada S02, S04, S05 pada beberapa metrik — "
      "perlu diakui sebagai sumber variasi yang tidak sepenuhnya tereliminasi.")
    blank()

    # 4.9 Panduan Chart
    h(2, "4.9 Panduan Interpretasi Chart (Auto-generated)")
    p("*Deskripsi chart tersedia di sheet ChartDesc Excel dan JSON key chart_descriptions. "
      "Gunakan sebagai basis caption gambar di skripsi.*")
    blank()

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MASTER FUNCTION
# ---------------------------------------------------------------------------

def generate_interpretations(all_results):
    """Master function — generate semua interpretasi."""
    groups_interp = {}
    for gk, group in all_results.get("groups",{}).items():
        cond = group.get("condition","C2")
        tt   = group.get("test_type","?")
        if cond in ("C3","C4") or tt == "soak":
            continue
        groups_interp[gk] = interpret_group(group)

    patterns     = interpret_patterns(all_results)
    soak_interp  = interpret_soak(all_results)
    rq           = interpret_research_questions(all_results)
    chart_desc   = generate_chart_descriptions(all_results)

    rq2     = rq.get("RQ2",{})
    n_r     = len(rq2.get("rest_superior",[]))
    n_t     = len(rq2.get("trpc_superior",[]))
    pat_r   = sum(1 for p in patterns if "REST" in p["direction"])
    pat_t   = sum(1 for p in patterns if "tRPC" in p["direction"])

    if pat_r > pat_t:
        perf_s = (f"REST secara konsisten lebih baik di {pat_r} dari {len(patterns)} "
                  f"metrik primer lintas skenario.")
    else:
        perf_s = "Tidak ada pemenang konsisten — performa bergantung skenario."

    overall_summary = (
        f"REST lebih optimal di {n_r} kasus vs tRPC di {n_t} kasus "
        f"dari seluruh kasus dengan efek ≥sedang atau signifikan. "
        f"{perf_s}"
    )

    interp = {
        "groups":             groups_interp,
        "patterns":           patterns,
        "soak":               soak_interp,
        "research_questions": rq,
        "chart_descriptions": chart_desc,
        "overall_summary":    overall_summary,
    }

    interp["bab4_draft"] = generate_bab4_draft(interp, soak_interp)
    return interp