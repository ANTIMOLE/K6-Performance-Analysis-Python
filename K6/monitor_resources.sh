#!/usr/bin/env bash
# =============================================================
# monitor_resources.sh — Catat CPU, RAM, dan Postgres metrics
#
# FIX:
#  - cpu_total_pct: dari /proc/stat delta (bukan "us" dari top)
#    /proc/stat mengukur total CPU utilization semua core, lebih akurat
#  - target_pid_cpu_pct: dari /proc/<pid>/stat delta (bukan ps %cpu)
#    ps %cpu adalah cumulative average sejak process start, bukan per-interval
#  - target_pid_mem_mb: dari /proc/<pid>/status VmRSS (resident memory)
#  - Tambah konfound warning + instruksi stop backend lain
#
# PENTING — Untuk resource comparison yang bersih:
#   REST test  → pm2 stop backend-trpc sebelum run
#   tRPC test  → pm2 stop backend-rest sebelum run
#   Kalau keduanya jalan, CPU total mencakup keduanya dan tidak bisa
#   diklaim sebagai penggunaan resource satu backend saja.
#
# Cara jalankan:
#   chmod +x monitor_resources.sh
#   pm2 stop backend-trpc               ← stop backend lain dulu
#   ./monitor_resources.sh rest load s01 5 &
#   MONITOR_PID=$!
#   k6 run --env API=rest ...
#   kill $MONITOR_PID
#   pm2 start backend-trpc              ← kembalikan setelahnya
#
# Output: results/resource_<api>_<type>_<scenario>_<timestamp>.csv
# =============================================================

set -euo pipefail

API="${1:-rest}"
TEST_TYPE="${2:-load}"
SCENARIO="${3:-s01}"
INTERVAL="${4:-5}"

TIMESTAMP=$(date +%s)
OUTFILE="results/resource_${API}_${TEST_TYPE}_${SCENARIO}_${TIMESTAMP}.csv"

mkdir -p results

PG_HOST="${PGHOST:-localhost}"
PG_PORT="${PGPORT:-5432}"
PG_USER="${PGUSER:-zenit}"
PG_DB="${PGDATABASE:-ecommerce_db}"
export PGPASSWORD="${PGPASSWORD:-zenit123}"

# Header CSV
echo "timestamp,cpu_total_pct,mem_used_mb,mem_total_mb,pg_active,pg_idle,pg_cache_hit_ratio,pg_tps_delta,target_pid_cpu_pct,target_pid_mem_mb" > "$OUTFILE"

echo "=== Resource Monitor ==="
echo "API: $API | Test: $TEST_TYPE | Scenario: $SCENARIO | Interval: ${INTERVAL}s"
echo "Output: $OUTFILE"

if [ "$API" = "rest" ]; then TARGET_PORT=4000; else TARGET_PORT=4001; fi
TARGET_PID=$(pgrep -f "node.*$TARGET_PORT" 2>/dev/null | head -1 || echo "")
[ -z "$TARGET_PID" ] && echo "⚠️  Target backend (port $TARGET_PORT) tidak ditemukan — PID metrics akan 0"
[ -n "$TARGET_PID" ] && echo "✓ Target PID: $TARGET_PID (port $TARGET_PORT)"

# Deteksi backend lain yang masih jalan (konfound warning)
if [ "$API" = "rest" ]; then OTHER_PORT=4001; OTHER_NAME="backend-trpc"
else                          OTHER_PORT=4000; OTHER_NAME="backend-rest"; fi
OTHER_PID=$(pgrep -f "node.*$OTHER_PORT" 2>/dev/null | head -1 || echo "")
if [ -n "$OTHER_PID" ]; then
  echo ""
  echo "⚠️  KONFOUND TERDETEKSI: $OTHER_NAME (PID $OTHER_PID, port $OTHER_PORT) masih berjalan!"
  echo "   CPU total akan mencakup proses ini → resource data TIDAK VALID untuk perbandingan."
  echo ""
  if [ "${ALLOW_CONFOUND:-0}" != "1" ]; then
    echo "❌ Script berhenti. Jalankan salah satu:"
    echo "   1. pm2 stop $OTHER_NAME  (direkomendasikan — resource measurement bersih)"
    echo "   2. ALLOW_CONFOUND=1 ./monitor_resources.sh $*  (lanjut tapi catat sebagai terkontaminasi)"
    exit 1
  else
    echo "⚠️  ALLOW_CONFOUND=1 diset — lanjut dengan konfound. CATAT ini di laporan/bab keterbatasan!"
    echo "   Output akan diberi suffix _confound pada nama file CSV."
    OUTFILE="results/resource_${API}_${TEST_TYPE}_${SCENARIO}_${TIMESTAMP}_confound.csv"
    echo "   Output: $OUTFILE"
    sleep 3
  fi
fi
echo "========================"

# ── Helper: baca CPU aggregate dari /proc/stat ─────────────────
read_cpu_stat() {
  awk '/^cpu / {print $2+$3+$4+$5+$6+$7+$8, $5}' /proc/stat
}

# ── Helper: baca jiffy count untuk PID dari /proc/<pid>/stat ──
read_proc_jiffies() {
  local pid="$1"
  [ -n "$pid" ] && [ -f "/proc/$pid/stat" ] \
    && awk '{print $14+$15+$16+$17}' "/proc/$pid/stat" 2>/dev/null \
    || echo "0"
}

# ── Helper: baca RSS memory dari /proc/<pid>/status ────────────
read_proc_mem_mb() {
  local pid="$1"
  [ -n "$pid" ] && [ -f "/proc/$pid/status" ] \
    && awk '/VmRSS/ {printf "%.1f", $2/1024}' "/proc/$pid/status" 2>/dev/null \
    || echo "0"
}

NCPU=$(nproc 2>/dev/null || echo "1")

# Init baseline readings
IFS=' ' read -r PREV_TOTAL PREV_IDLE <<< "$(read_cpu_stat)"
PREV_PROC_J=$(read_proc_jiffies "$TARGET_PID")
PREV_TPS=$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -t -c \
  "SELECT xact_commit+xact_rollback FROM pg_stat_database WHERE datname='$PG_DB';" \
  2>/dev/null | tr -d ' \n' || echo "0")

echo "Mulai sampling setiap ${INTERVAL}s... (Ctrl+C untuk stop)"

while true; do
  sleep "$INTERVAL"
  NOW=$(date +%Y-%m-%dT%H:%M:%S)

  # ── CPU total (delta dari /proc/stat) ─────────────────────
  IFS=' ' read -r CURR_TOTAL CURR_IDLE <<< "$(read_cpu_stat)"
  D_TOTAL=$(( CURR_TOTAL - PREV_TOTAL ))
  D_IDLE=$(( CURR_IDLE - PREV_IDLE ))
  if [ "$D_TOTAL" -gt 0 ]; then
    CPU_PCT=$(awk "BEGIN {printf \"%.1f\", (1 - $D_IDLE / $D_TOTAL) * 100}")
  else
    CPU_PCT="0.0"
  fi
  PREV_TOTAL="$CURR_TOTAL"
  PREV_IDLE="$CURR_IDLE"

  # ── Memory total ──────────────────────────────────────────
  MEM_TOTAL=$(free -m | awk 'NR==2{print $2}')
  MEM_USED=$(free -m  | awk 'NR==2{print $3}')

  # ── Per-process CPU (delta jiffies) ───────────────────────
  CURR_PROC_J=$(read_proc_jiffies "$TARGET_PID")
  D_PROC=$(( CURR_PROC_J - PREV_PROC_J ))
  # pct = (jiffy_used / (interval_jiffies * cores)) * 100
  PROC_CPU=$(awk "BEGIN {printf \"%.1f\", ($D_PROC / ($INTERVAL * 100 * $NCPU)) * 100}")
  PREV_PROC_J="$CURR_PROC_J"

  # ── Per-process RAM (RSS) ─────────────────────────────────
  PROC_MEM=$(read_proc_mem_mb "$TARGET_PID")

  # ── Postgres metrics ──────────────────────────────────────
  PG_CONNS=$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT COUNT(*) FILTER (WHERE state='active'), COUNT(*) FILTER (WHERE state='idle')
    FROM pg_stat_activity WHERE datname='$PG_DB' AND pid<>pg_backend_pid();
  " 2>/dev/null || echo "0 | 0")
  PG_ACTIVE=$(echo "$PG_CONNS" | awk -F'|' '{gsub(/ /,"",$1); print $1}')
  PG_IDLE=$(echo   "$PG_CONNS" | awk -F'|' '{gsub(/ /,"",$2); print $2}')

  PG_CACHE=$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT ROUND(blks_hit::numeric/NULLIF(blks_hit+blks_read,0)*100,2)
    FROM pg_stat_database WHERE datname='$PG_DB';
  " 2>/dev/null | tr -d ' \n' || echo "0")

  CURR_TPS=$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -t -c \
    "SELECT xact_commit+xact_rollback FROM pg_stat_database WHERE datname='$PG_DB';" \
    2>/dev/null | tr -d ' \n' || echo "0")
  TPS_DELTA=$(( (CURR_TPS - PREV_TPS) / INTERVAL ))
  PREV_TPS="$CURR_TPS"

  # ── Tulis ke CSV ──────────────────────────────────────────
  echo "$NOW,$CPU_PCT,$MEM_USED,$MEM_TOTAL,$PG_ACTIVE,$PG_IDLE,$PG_CACHE,$TPS_DELTA,$PROC_CPU,$PROC_MEM" >> "$OUTFILE"

  printf "[%s] CPU: %s%% | RAM: %s/%s MB | PID: CPU=%s%% MEM=%s MB | PG: %s active | TPS: %s/s\n" \
    "$NOW" "$CPU_PCT" "$MEM_USED" "$MEM_TOTAL" "$PROC_CPU" "$PROC_MEM" "$PG_ACTIVE" "$TPS_DELTA"
done
