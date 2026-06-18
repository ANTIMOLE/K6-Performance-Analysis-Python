"""
parse_network.py — ZENIT Performance Testing
Baca file CSV network monitoring dan ekstrak metrik network I/O.

Kolom CSV (dari monitor_resources.sh):
  timestamp, net_rx_kb_s, net_tx_kb_s

Metrik yang diekstrak:
  M-13  Network I/O (KB/s)   → mean rx + tx per run

Catatan:
  rx = receive (server menerima request dari client)
  tx = transmit (server mengirim response ke client)
  Total I/O = rx + tx

Untuk load test: ambil mean dari seluruh time-series.
Untuk soak test: kembalikan seluruh time-series.
"""

import csv
import os


def parse_network(filepath: str, mode: str = "mean") -> dict:
    """
    Parse file CSV network monitoring.

    Args:
        filepath: path ke file CSV
        mode: 'mean'   → return mean per run (untuk load/stress/spike)
              'series' → return time-series penuh (untuk soak)

    Returns:
        dict berisi metrik network I/O.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File tidak ditemukan: {filepath}")

    rows = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        # utf-8-sig untuk handle BOM dari beberapa tools
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rx = float(row["net_rx_kb_s"])
                tx = float(row["net_tx_kb_s"])
                rows.append({
                    "timestamp":    row["timestamp"].strip(),
                    "net_rx_kb_s":  rx,
                    "net_tx_kb_s":  tx,
                    "net_total_kb_s": rx + tx,
                })
            except (ValueError, KeyError):
                continue

    if not rows:
        raise ValueError(f"Tidak ada data valid di file: {filepath}")

    n = len(rows)

    if mode == "series":
        return {
            "mode":       "series",
            "n_samples":  n,
            "timestamps": [r["timestamp"] for r in rows],
            "rx_kb_s":    [r["net_rx_kb_s"] for r in rows],
            "tx_kb_s":    [r["net_tx_kb_s"] for r in rows],
            "total_kb_s": [r["net_total_kb_s"] for r in rows],
        }

    # mode == "mean"
    def mean(vals):
        return sum(vals) / len(vals)

    def std(vals):
        m = mean(vals)
        return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5

    rx_vals    = [r["net_rx_kb_s"] for r in rows]
    tx_vals    = [r["net_tx_kb_s"] for r in rows]
    total_vals = [r["net_total_kb_s"] for r in rows]

    return {
        "mode":      "mean",
        "n_samples": n,

        # M-13: Network I/O (KB/s)
        "rx_mean_kb_s":    mean(rx_vals),
        "tx_mean_kb_s":    mean(tx_vals),
        "total_mean_kb_s": mean(total_vals),  # M-13 — nilai utama

        "rx_max_kb_s":     max(rx_vals),
        "tx_max_kb_s":     max(tx_vals),
        "total_max_kb_s":  max(total_vals),

        "rx_std":          std(rx_vals),
        "tx_std":          std(tx_vals),
    }


def parse_network_pair(
    rest_path: str,
    trpc_path: str,
    mode: str = "mean",
) -> tuple[dict, dict]:
    """
    Parse sepasang CSV network (REST dan tRPC) sekaligus.

    Returns:
        (rest_network, trpc_network)
    """
    rest = parse_network(rest_path, mode=mode)
    trpc = parse_network(trpc_path, mode=mode)
    return rest, trpc


if __name__ == "__main__":
    import sys

    results_dir = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"

    rest_path = os.path.join(results_dir, "network_rest_load_s01_1778479572.csv")
    trpc_path = os.path.join(results_dir, "network_trpc_load_s01_1778483339.csv")

    rest, trpc = parse_network_pair(rest_path, trpc_path, mode="mean")

    print("=== PARSE NETWORK — Run 1 S01 Browse ===\n")
    print(f"Samples REST: {rest['n_samples']}  |  tRPC: {trpc['n_samples']}\n")

    display = [
        ("RX mean (KB/s)",    "rx_mean_kb_s"),
        ("TX mean (KB/s)",    "tx_mean_kb_s"),
        ("Total mean (KB/s)", "total_mean_kb_s"),
        ("Total max (KB/s)",  "total_max_kb_s"),
    ]

    print(f"{'Metrik':<25} {'REST':>12} {'tRPC':>12} {'Δ':>12}")
    print("-" * 63)
    for label, key in display:
        rv = rest.get(key)
        tv = trpc.get(key)
        if rv is not None and tv is not None:
            print(f"{label:<25} {rv:>12.2f} {tv:>12.2f} {rv - tv:>+12.2f}")
