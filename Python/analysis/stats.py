"""
stats.py — ZENIT Performance Testing
Semua fungsi analisis statistik.

FIXES (cumulative):
  BUG-01: normal = bool(p > 0.05) — explicit Python bool, bukan numpy bool
  BUG-03: Shapiro guard range=0 — all-zero difference scores ditangani sebelum scipy
  BUG-N2: N=2 degenerate t-test guard — SD=0 dengan N kecil tidak menghasilkan
           sig=True palsu (t=-inf, p=0.0)
  ISS-06: Wilcoxon p_min check pakai n_nonzero bukan n — setelah ties di-remove,
           p_min dihitung ulang untuk mencerminkan effective sample size
  FIX-W3: Wilcoxon rank_biserial_r menggunakan formula exact dari W statistic
           (r = (W_max - 2W)/W_max, sign dari mean(d_nonzero)), menggantikan
           approximation z_approx yang tidak akurat untuk small-N exact distribution.

REMOVED:
  bonferroni_correction() — dihapus. Thesis menggunakan RQ-based framing, bukan
  formal hypothesis testing. Multiple comparison ditangani dengan pelaporan p-value
  flat α=0.05 per metrik + order effect verification terpisah.
"""

import math
import random

from scipy import stats as scipy_stats


# Minimum N untuk load test confirmatory yang dianggap valid
MIN_CONFIRMATORY_N = 5


# ---------------------------------------------------------------------------
# 1. DESCRIPTIVE STATISTICS
# ---------------------------------------------------------------------------

def descriptive(values: list[float]) -> dict:
    """Hitung statistik deskriptif. Returns dict: mean, std, median, min, max, n."""
    n = len(values)
    if n == 0:
        return {}

    mean     = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    std      = variance ** 0.5

    sorted_vals = sorted(values)
    if n % 2 == 0:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    else:
        median = sorted_vals[n // 2]

    return {
        "n":      n,
        "mean":   mean,
        "std":    std,
        "median": median,
        "min":    min(values),
        "max":    max(values),
    }


def difference_scores(rest_vals: list[float], trpc_vals: list[float]) -> list[float]:
    """
    d_i = REST_i - tRPC_i. Positif = REST lebih tinggi.
    """
    assert len(rest_vals) == len(trpc_vals), \
        f"Jumlah nilai tidak sama: REST={len(rest_vals)}, tRPC={len(trpc_vals)}"
    return [r - t for r, t in zip(rest_vals, trpc_vals)]


# ---------------------------------------------------------------------------
# 2. SHAPIRO-WILK
# ---------------------------------------------------------------------------

def shapiro_wilk(d: list[float]) -> dict:
    """
    Uji normalitas Shapiro-Wilk pada difference scores.

    BUG-01 FIX: bool(p > 0.05) — explicit Python bool.
    BUG-03 FIX: guard range=0 sebelum memanggil scipy — all-zero d_i
                menyebabkan scipy return nan/raise warning.
    """
    if len(d) < 3:
        return {
            "w": None, "p": None, "normal": None,
            "note": f"N={len(d)} terlalu kecil untuk Shapiro-Wilk (min N=3)"
        }

    # BUG-03 FIX: range=0 berarti semua d_i identik → trivially "normal" tapi
    # tidak ada variance. Kembalikan normal=True supaya t-test dipilih,
    # yang kemudian akan mendeteksi SD=0 dan handle sendiri.
    if max(d) == min(d):
        return {
            "w":      1.0,
            "p":      1.0,
            "normal": True,
            "note":   (f"Range difference scores = 0 (semua d_i = {d[0]:.4f}) "
                       f"— trivially normal, Shapiro-Wilk di-skip"),
        }

    w, p = scipy_stats.shapiro(d)

    # Guard terhadap NaN yang bisa datang dari kasus edge scipy
    if math.isnan(float(p)) or math.isnan(float(w)):
        return {
            "w": None, "p": None, "normal": True,
            "note": "Shapiro-Wilk return NaN — default ke t-test"
        }

    # BUG-01 FIX: explicit bool()
    normal = bool(p > 0.05)
    note   = "NORMAL (p > 0.05)" if normal else "TIDAK NORMAL (p ≤ 0.05)"

    return {
        "w":      float(w),
        "p":      float(p),
        "normal": normal,
        "note":   note,
    }


# ---------------------------------------------------------------------------
# 3. PAIRED T-TEST
# ---------------------------------------------------------------------------

def paired_ttest(d: list[float], alpha: float = 0.05) -> dict:
    """
    Paired t-test pada difference scores.
    N=10: df=9, t-critical=2.262. N=3: df=2, t-critical=4.303.

    BUG-N2 FIX: Jika SD=0 (degenerate), t = ±inf, p = 0.0 yang muncul
    sebagai sig=True padahal tidak valid. Dideteksi dan di-override ke None.
    """
    n = len(d)
    if n < 2:
        return {
            "t_stat": None, "p": None, "df": None,
            "significant": None, "direction": None,
            "note": f"N={n} tidak cukup untuk t-test"
        }

    mean_d = sum(d) / n
    var_d  = sum((v - mean_d) ** 2 for v in d) / (n - 1) if n > 1 else 0.0
    sd_d   = var_d ** 0.5

    # BUG-N2 FIX: Degenerate case — SD=0 menyebabkan t=±inf, p=0.0 palsu
    if sd_d == 0:
        return {
            "t_stat":      None,
            "p":           None,
            "df":          n - 1,
            "significant": None,
            "direction":   None,
            "note":        (f"SD(d) = 0 — semua difference scores identik "
                            f"(d_i = {mean_d:.4f}). t-test tidak valid (degenerasi). "
                            f"Gunakan Cohen's d = 0 (trivial) sebagai interpretasi."),
        }

    t_stat, p   = scipy_stats.ttest_1samp(d, 0.0)
    df          = n - 1

    # Guard NaN/Inf dari scipy (seharusnya tidak terjadi setelah SD>0 guard)
    if math.isnan(float(p)) or math.isinf(float(t_stat)):
        return {
            "t_stat": None, "p": None, "df": df,
            "significant": None, "direction": None,
            "note": "t-test return nan/inf — hasil tidak valid"
        }

    significant = bool(float(p) < alpha)

    # Override jika N terlalu kecil untuk dianggap valid
    if n < MIN_CONFIRMATORY_N and significant:
        note_suffix = (f" [PERINGATAN: N={n} < {MIN_CONFIRMATORY_N} — "
                       f"signifikansi tidak reliabel, interpretasi hanya exploratory]")
    else:
        note_suffix = ""

    if significant:
        direction = "REST_lebih_tinggi" if mean_d > 0 else "tRPC_lebih_tinggi"
    else:
        direction = "tidak_signifikan"

    note = (f"t({df}) = {t_stat:.4f}, p = {p:.4f} → "
            f"{'SIGNIFIKAN' if significant else 'tidak signifikan'}{note_suffix}")
    if n == 3:
        note += " [N=3: referensi saja, power sangat rendah]"

    return {
        "t_stat":      float(t_stat),
        "p":           float(p),
        "df":          df,
        "significant": significant,
        "direction":   direction,
        "note":        note,
    }


# ---------------------------------------------------------------------------
# 4. WILCOXON SIGNED-RANK TEST
# ---------------------------------------------------------------------------

def wilcoxon_test(d: list[float], alpha: float = 0.05) -> dict:
    """
    Wilcoxon Signed-Rank Test — fallback non-parametrik jika tidak normal.

    ISS-06 FIX: p_min check menggunakan n_nonzero (setelah ties di-remove),
    bukan n original. Ini penting karena dengan banyak ties, Wilcoxon pada
    n_nonzero kecil mungkin tidak bisa mencapai signifikansi meski n=10.
    """
    n         = len(d)
    d_nonzero = [v for v in d if v != 0]
    n_nonzero = len(d_nonzero)

    # ISS-06 FIX: Gunakan n_nonzero untuk p_min check
    p_min_theoretical = 2 / (2 ** n_nonzero) if n_nonzero <= 20 else 0.0

    if p_min_theoretical >= alpha:
        return {
            "w_stat": None, "p": None,
            "rank_biserial_r": None,
            "significant": False,
            "direction":   "tidak_signifikan",
            "note": (
                f"N={n} (n_nonzero={n_nonzero}): p_min={p_min_theoretical:.3f} ≥ α={alpha} "
                f"— Wilcoxon tidak bisa mencapai signifikansi setelah ties di-remove. "
                f"Gunakan Cohen's d sebagai ukuran utama."
            ),
        }

    if n_nonzero < 2:
        return {
            "w_stat": None, "p": None,
            "rank_biserial_r": None,
            "significant": False,
            "direction":   "tidak_signifikan",
            "note": "Terlalu banyak ties (d_i = 0), Wilcoxon tidak bisa dijalankan."
        }

    try:
        w_stat, p = scipy_stats.wilcoxon(d_nonzero, alternative="two-sided")

        # Guard NaN
        if math.isnan(float(p)):
            return {
                "w_stat": None, "p": None,
                "rank_biserial_r": None,
                "significant": False,
                "direction": "tidak_signifikan",
                "note": "Wilcoxon return NaN — hasil tidak valid"
            }

        # Fix #3: rank-biserial r langsung dari W statistic (formula exact),
        # bukan dari z_approx yang hanya valid untuk large-N normal approximation.
        #
        # Formula: r = (W_max - 2*W) / W_max, dimana W_max = n*(n+1)/2
        # scipy.wilcoxon returns min(W+, W-) = W.
        # Sign dari r: positif jika mean(d_nonzero) > 0 (REST lebih besar), negatif sebaliknya.
        # Gunakan mean(d_nonzero) bukan mean(d) — nol tidak ikut Wilcoxon,
        # menggunakannya dalam sign computation bias hasilnya jika banyak ties.
        mean_d_nonzero = sum(d_nonzero) / len(d_nonzero)
        w_max  = n_nonzero * (n_nonzero + 1) / 2
        r_abs  = (w_max - 2 * w_stat) / w_max if w_max > 0 else 0.0
        sign   = 1 if mean_d_nonzero > 0 else (-1 if mean_d_nonzero < 0 else 0)
        r      = r_abs * sign

        significant = bool(float(p) < alpha)
        direction   = ("REST_lebih_tinggi" if mean_d_nonzero > 0 else "tRPC_lebih_tinggi") if significant else "tidak_signifikan"
        note        = (f"W = {w_stat:.1f}, p = {p:.4f} → "
                       f"{'SIGNIFIKAN' if significant else 'tidak signifikan'}, "
                       f"r = {r:.3f} (n={n}, n_nonzero={n_nonzero})")

        return {
            "w_stat":          float(w_stat),
            "p":               float(p),
            "rank_biserial_r": float(r),
            "significant":     significant,
            "direction":       direction,
            "note":            note,
        }
    except Exception as e:
        return {
            "w_stat": None, "p": None,
            "rank_biserial_r": None,
            "significant": False,
            "direction": "tidak_signifikan",
            "note": f"Wilcoxon error: {e}"
        }


# ---------------------------------------------------------------------------
# 5. COHEN'S D
# ---------------------------------------------------------------------------

def cohens_d(d: list[float]) -> dict:
    """
    Cohen's d = mean(d_i) / SD(d_i). Valid di N berapa pun.

    Interpretasi:
      |d| < 0.2  → trivial
      0.2–0.5    → kecil
      0.5–0.8    → sedang
      ≥ 0.8      → besar
    """
    n = len(d)
    if n < 2:
        return {
            "d": None, "magnitude": "tidak dapat dihitung",
            "note": f"N={n} tidak cukup untuk Cohen's d"
        }

    mean_d = sum(d) / n
    var_d  = sum((v - mean_d) ** 2 for v in d) / (n - 1)
    sd_d   = var_d ** 0.5

    if sd_d == 0:
        return {
            "d": 0.0, "magnitude": "trivial",
            "direction": "tidak_ada_perbedaan",
            "note": f"SD(d) = 0 — semua perbedaan identik (d_i = {mean_d:.4f})"
        }

    d_val = mean_d / sd_d
    abs_d = abs(d_val)

    # INFLATED-D GUARD: d > 10 hampir selalu berarti SD(d_i) mendekati nol
    # (metric terlalu konsisten antar run) → d meledak, tidak interpretatif.
    # Threshold 10: efek terbesar real di data adalah CPU d=-4.01, RAM d=-1.67.
    # Semua d>10 di data (payload=69/764, SLA=-21, checks=21, pg_tps=12.4)
    # berasal dari near-zero SD, bukan efek protokol yang sesungguhnya.
    if abs_d > 10:
        direction = "REST lebih tinggi" if d_val > 0 else "tRPC lebih tinggi"
        return {
            "d":         float(d_val),
            "magnitude": "tidak_interpretatif",
            "direction": direction,
            "note":      (
                f"d = {d_val:.1f} — TIDAK INTERPRETATIF: |d| > 10 menandakan SD(d_i) "
                f"mendekati 0 (SD={sd_d:.4f}). Metric sangat konsisten antar run. "
                f"Mean diff = {mean_d:.4f}. Gunakan \u0394 mean, bukan d."
            ),
        }

    if abs_d < 0.2:       magnitude = "trivial"
    elif abs_d < 0.5:     magnitude = "kecil"
    elif abs_d < 0.8:     magnitude = "sedang"
    else:                 magnitude = "besar"

    direction = "REST lebih tinggi" if d_val > 0 else "tRPC lebih tinggi"

    return {
        "d":         float(d_val),
        "magnitude": magnitude,
        "direction": direction,
        "note":      f"d = {d_val:.4f} \u2192 {magnitude} ({direction})",
    }


# ---------------------------------------------------------------------------
# 6. BOOTSTRAP 95% CI
# ---------------------------------------------------------------------------

def bootstrap_ci(
    d: list[float],
    n_resamples: int = 9999,
    alpha: float = 0.05,
    seed: int = 42,
) -> dict:
    """Bootstrap 95% CI untuk mean difference scores."""
    n = len(d)
    if n < 2:
        return {
            "ci_lower": None, "ci_upper": None,
            "covers_zero": None, "mean_d": None,
            "note": f"N={n} tidak cukup untuk Bootstrap CI"
        }

    rng        = random.Random(seed)
    boot_means = []
    for _ in range(n_resamples):
        resample = [rng.choice(d) for _ in range(n)]
        boot_means.append(sum(resample) / n)

    boot_means.sort()
    lower_idx   = int((alpha / 2) * n_resamples)
    upper_idx   = int((1 - alpha / 2) * n_resamples)
    ci_lower    = boot_means[lower_idx]
    ci_upper    = boot_means[upper_idx]
    mean_d      = sum(d) / n
    covers_zero = bool(ci_lower <= 0 <= ci_upper)

    note = (
        f"95% CI [{ci_lower:.3f}, {ci_upper:.3f}] → "
        f"{'mencakup 0 (tidak cukup bukti)' if covers_zero else 'tidak mencakup 0 (ada perbedaan bermakna)'}"
    )

    return {
        "ci_lower":    float(ci_lower),
        "ci_upper":    float(ci_upper),
        "covers_zero": covers_zero,
        "mean_d":      float(mean_d),
        "note":        note,
    }


# ---------------------------------------------------------------------------
# 7. ANALYZE LOAD — Full Pipeline
# ---------------------------------------------------------------------------

def analyze_load(
    rest_vals: list[float],
    trpc_vals: list[float],
    metric_name: str = "metric",
    alpha: float = 0.05,
) -> dict:
    """
    Full pipeline untuk load test (N=10).

    Steps: difference scores → Shapiro-Wilk → t-test/Wilcoxon → Cohen's d → Bootstrap CI

    Menyimpan rest_vals dan trpc_vals per-run di output dict
    sehingga chart functions bisa pakai nilai aktual (bukan approximation).
    """
    assert len(rest_vals) == len(trpc_vals) >= 2, \
        f"N harus >= 2, got REST={len(rest_vals)}, tRPC={len(trpc_vals)}"

    d  = difference_scores(rest_vals, trpc_vals)
    n  = len(d)

    desc_rest = descriptive(rest_vals)
    desc_trpc = descriptive(trpc_vals)
    desc_diff = descriptive(d)

    sw  = shapiro_wilk(d)
    cd  = cohens_d(d)
    bci = bootstrap_ci(d)

    n_warning = None
    if n < 10:
        n_warning = (
            f"PERINGATAN: N={n} < 10 — analisis ini INTERIM, "
            f"bukan confirmatory load test. Jalankan ulang setelah semua 10 run selesai."
        )

    use_wilcoxon = sw.get("normal") is False
    if use_wilcoxon:
        inferential = wilcoxon_test(d, alpha=alpha)
        test_used   = "wilcoxon"
    else:
        inferential = paired_ttest(d, alpha=alpha)
        test_used   = "paired_ttest"

    sig   = inferential.get("significant")
    d_val = cd.get("d")
    d_mag = cd.get("magnitude", "")

    if sig is None:
        conclusion = f"TIDAK DAPAT DINILAI — t-test degenerasi (SD=0 atau N={n} terlalu kecil)"
    elif d_mag == "tidak_interpretatif":
        # d meledak karena SD(d_i) mendekati 0 — metric terlalu konsisten.
        # Gunakan mean diff + sig status saja; tidak klaim magnitude.
        desc_diff_mean = desc_diff.get("mean", 0)
        sig_str = "signifikan" if sig else "tidak signifikan"
        conclusion = (
            f"TIDAK DAPAT DINILAI MAGNITUDE — d tidak interpretatif (SD d_i terlalu kecil). "
            f"Perbedaan {sig_str}. Gunakan mean diff: delta = {desc_diff_mean:.4f}."
        )
    elif sig and d_val is not None:
        conclusion = ("DIDUKUNG — signifikan statistik dan meaningful"
                      if abs(d_val) >= 0.2 else
                      "PERHATIAN — signifikan statistik tapi efek trivial")
    elif not sig and d_val is not None and abs(d_val) >= 0.5:
        conclusion = "TIDAK PASTI — efek sedang/besar tapi tidak signifikan (power kurang?)"
    else:
        conclusion = "TIDAK DIDUKUNG — tidak signifikan dan efek kecil"

    result = {
        "metric":            metric_name,
        "n":                 n,
        "rest_vals":         list(rest_vals),   # Actual per-run values untuk chart
        "trpc_vals":         list(trpc_vals),   # Actual per-run values untuk chart
        "descriptive":       {"rest": desc_rest, "trpc": desc_trpc, "diff": desc_diff},
        "difference_scores": d,
        "shapiro_wilk":      sw,
        "test_used":         test_used,
        "inferential":       inferential,
        "cohens_d":          cd,
        "bootstrap_ci":      bci,
        "conclusion":        conclusion,
    }
    if n_warning:
        result["n_warning"] = n_warning

    return result


# ---------------------------------------------------------------------------
# 8. ANALYZE EXPLORATORY
# ---------------------------------------------------------------------------

def analyze_exploratory(
    rest_vals: list[float],
    trpc_vals: list[float],
    metric_name: str = "metric",
) -> dict:
    """
    Pipeline eksploratif untuk stress/spike (N=3).
    T-test sebagai referensi saja, Cohen's d sebagai ukuran utama.
    """
    d = difference_scores(rest_vals, trpc_vals)

    return {
        "metric":            metric_name,
        "n":                 len(d),
        "rest_vals":         list(rest_vals),
        "trpc_vals":         list(trpc_vals),
        "framing":           "exploratory — N=3, Cohen's d sebagai ukuran utama",
        "descriptive":       {
            "rest": descriptive(rest_vals),
            "trpc": descriptive(trpc_vals),
            "diff": descriptive(d),
        },
        "difference_scores": d,
        "ttest_reference":   paired_ttest(d),
        "cohens_d":          cohens_d(d),
    }


# ---------------------------------------------------------------------------
# 9. DECOMPOSITION ANALYSIS (C3 & C4)
# ---------------------------------------------------------------------------

def analyze_decomposition_c3(
    c1_mean: float, c2_mean: float, c3_mean: float,
    metric_name: str = "metric",
) -> dict:
    """
    Decomposition C3: estimasi auth overhead contribution.
    auth_contribution_pct = (gap_C1C2 - gap_C1C3) / gap_C1C2 * 100
    """
    gap_c1c2 = c2_mean - c1_mean
    gap_c1c3 = c3_mean - c1_mean

    if gap_c1c2 == 0:
        return {
            "metric": metric_name, "framing": "decomposition_c3",
            "c1_mean": float(c1_mean), "c2_mean": float(c2_mean), "c3_mean": float(c3_mean),
            "gap_c1c2": 0.0, "gap_c1c3": float(gap_c1c3),
            "auth_contribution_pct": 0.0, "remaining_gap_pct": None,
            "note": "Gap C1-C2 = 0, tidak bisa hitung kontribusi auth",
        }

    auth_contribution_pct = (gap_c1c2 - gap_c1c3) / gap_c1c2 * 100
    remaining_pct         = 100 - auth_contribution_pct

    # Issue #2 Fix: Flag anomali jika kontribusi >100% atau <0%
    # Ini terjadi ketika C3 (tRPC+auth equalized) justru lebih cepat/lambat
    # dari REST baseline — hampir selalu karena single run noise (N=1).
    anomaly_flag = False
    anomaly_note = ""
    if auth_contribution_pct > 100:
        anomaly_flag = True
        anomaly_note = (
            f"⚠ ANOMALY >100%: Auth contrib {auth_contribution_pct:.1f}% — "
            f"C3 (tRPC+auth) justru lebih cepat dari REST baseline (C1). "
            f"Kemungkinan penyebab: (1) single run noise N=1, "
            f"(2) kondisi server berbeda saat C3 dijalankan, "
            f"(3) warmup effect. Interpretasi hanya indikatif, bukan konfirmatori."
        )
    elif auth_contribution_pct < 0:
        anomaly_flag = True
        anomaly_note = (
            f"⚠ ANOMALY <0%: Auth contrib {auth_contribution_pct:.1f}% — "
            f"C3 justru lebih lambat dari C2 base. "
            f"Auth equalization tidak menurunkan gap — faktor lain mendominasi. "
            f"Single run noise (N=1) mungkin berperan."
        )

    return {
        "metric":                metric_name,
        "framing":               "decomposition_c3",
        "c1_mean":               float(c1_mean),
        "c2_mean":               float(c2_mean),
        "c3_mean":               float(c3_mean),
        "gap_c1c2":              float(gap_c1c2),
        "gap_c1c3":              float(gap_c1c3),
        "auth_contribution_pct": float(auth_contribution_pct),
        "remaining_gap_pct":     float(remaining_pct),
        "anomaly_flag":          anomaly_flag,
        "anomaly_note":          anomaly_note,
        "note": (
            f"Auth contribution: {auth_contribution_pct:.1f}% dari total gap. "
            f"Sisa (protokol/lain): {remaining_pct:.1f}%."
            + (f" {anomaly_note}" if anomaly_note else "")
        ),
    }


def analyze_decomposition_c4(
    c2_http_count: float, c4_http_count: float,
    c2_p95: float,        c4_p95: float,
    c2_throughput: float, c4_throughput: float,
    scenario: str = "unknown",
) -> dict:
    """
    Decomposition C4: estimasi batching benefit.
    """
    def safe_pct(new_val, old_val, invert=False):
        if old_val == 0:
            return None
        return (1 - new_val / old_val) * 100 if invert else (new_val - old_val) / old_val * 100

    http_red  = safe_pct(c4_http_count, c2_http_count, invert=True)
    lat_red   = safe_pct(c4_p95, c2_p95, invert=True)
    tput_gain = safe_pct(c4_throughput, c2_throughput, invert=False)

    notes = []
    if http_red  is not None: notes.append(f"HTTP reduction: {http_red:.1f}%")
    if lat_red   is not None: notes.append(f"P95 reduction: {lat_red:.1f}%")
    if tput_gain is not None:
        dir_ = "gain" if (tput_gain or 0) >= 0 else "loss"
        notes.append(f"Throughput {dir_}: {abs(tput_gain or 0):.1f}%")

    return {
        "scenario":              scenario,
        "framing":               "decomposition_c4",
        "c2_http_count":         float(c2_http_count),
        "c4_http_count":         float(c4_http_count),
        "c2_p95":                float(c2_p95),
        "c4_p95":                float(c4_p95),
        "c2_throughput":         float(c2_throughput),
        "c4_throughput":         float(c4_throughput),
        "http_reduction_pct":    float(http_red) if http_red is not None else None,
        "latency_reduction_pct": float(lat_red)  if lat_red  is not None else None,
        "throughput_gain_pct":   float(tput_gain) if tput_gain is not None else None,
        "note":                  " | ".join(notes),
    }


# ---------------------------------------------------------------------------
# SELF-TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== STATS SELF-TEST ===\n")

    # Test 1: Data tidak normal (outlier) → seharusnya Wilcoxon
    rest_v = [37.0, 38.0, 36.5, 39.0, 37.5, 38.5, 200.0, 37.8, 38.2, 37.1]
    trpc_v = [42.0, 43.0, 41.5, 44.0, 42.5, 43.5, 205.0, 42.8, 43.2, 42.1]
    r      = analyze_load(rest_v, trpc_v, metric_name="p95_outlier")
    print(f"Test 1 (outlier) → test: {r['test_used']} (expected: wilcoxon)")
    print(f"  SW normal type: {type(r['shapiro_wilk']['normal']).__name__} = {r['shapiro_wilk']['normal']}")
    print(f"  has rest_vals: {bool(r.get('rest_vals'))}")

    # Test 2: Data normal → seharusnya t-test
    rest2 = [37.0, 38.0, 36.5, 39.0, 37.5, 38.5, 37.3, 37.8, 38.2, 37.1]
    trpc2 = [42.0, 43.0, 41.5, 44.0, 42.5, 43.5, 42.3, 42.8, 43.2, 42.1]
    r2    = analyze_load(rest2, trpc2, metric_name="p95_normal")
    print(f"\nTest 2 (normal) → test: {r2['test_used']} (expected: paired_ttest)")

    # Test 3: All-zero differences (BUG-03 guard)
    rest3 = [40.0] * 10
    trpc3 = [40.0] * 10
    r3    = analyze_load(rest3, trpc3, metric_name="zero_diff")
    print(f"\nTest 3 (all-zero) → SW note: {r3['shapiro_wilk']['note'][:50]}")
    print(f"  t-test sig: {r3['inferential'].get('significant')}")

    # Test 4: N=2 degenerate (BUG-N2 guard)
    rest4 = [100.0, 100.0]
    trpc4 = [150.0, 150.0]
    r4    = analyze_load(rest4, trpc4, metric_name="n2_degenerate")
    print(f"\nTest 4 (N=2, SD=0) → sig: {r4['inferential'].get('significant')} (expected: None)")

    # Test 5: Wilcoxon n_nonzero check (ISS-06)
    d_with_ties = [5.0, 0.0, 0.0, 0.0, 0.0, 3.0, 4.0, 0.0, 0.0, 2.0]
    n_nz = sum(1 for v in d_with_ties if v != 0)
    print(f"\nTest 5 (Wilcoxon n_nonzero) → n={len(d_with_ties)}, n_nonzero={n_nz}")
    wt = wilcoxon_test(d_with_ties)
    print(f"  note: {wt['note'][:80]}")