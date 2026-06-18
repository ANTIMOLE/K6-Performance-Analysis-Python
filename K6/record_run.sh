#!/usr/bin/env bash
# =============================================================
# record_run.sh — Catat satu pasangan REST–tRPC run ke manifest
#
# Contoh penggunaan:
#   ./record_run.sh \
#     --run 1 \
#     --scenario s01_browse \
#     --test-type load \
#     --execution-order rest-first \
#     --rest             "S1/RUN 1 R-T/s01_browse_rest_load_1778550440.json" \
#     --trpc             "S1/RUN 1 R-T/s01_browse_trpc_load_1778553072.json" \
#     --rest-resource    "S1/RUN 1 R-T/resource_rest_load_s01_1778550473.csv" \
#     --trpc-resource    "S1/RUN 1 R-T/resource_trpc_load_s01_1778553123.csv" \
#     --rest-network     "S1/RUN 1 R-T/network_rest_load_s01_1778550440.csv" \
#     --trpc-network     "S1/RUN 1 R-T/network_trpc_load_s01_1778553072.csv" \
#     --rest-pgstats     "S1/RUN 1 R-T/pgstats_rest_load_s01_1778550440.csv" \
#     --trpc-pgstats     "S1/RUN 1 R-T/pgstats_trpc_load_s01_1778553072.csv"
#
# Path file adalah RELATIF terhadap folder results/.
# Output: results/run_manifest.json
# =============================================================

set -euo pipefail

RESULTS_DIR="$(dirname "$0")/results"
MANIFEST="$RESULTS_DIR/run_manifest.json"

# ── Cek Node.js tersedia ───────────────────────────────────────
if ! command -v node &>/dev/null; then
  echo "❌ Node.js tidak ditemukan. Install dari https://nodejs.org"
  exit 1
fi

# ── Parse args ────────────────────────────────────────────────
RUN=""
SCENARIO=""
TEST_TYPE=""
EXEC_ORDER=""
REST_FILE=""
TRPC_FILE=""
REST_RESOURCE=""
TRPC_RESOURCE=""
REST_NETWORK=""
TRPC_NETWORK=""
REST_PGSTATS=""
TRPC_PGSTATS=""
NOTE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run)              RUN="$2";            shift 2 ;;
    --scenario)         SCENARIO="$2";       shift 2 ;;
    --test-type)        TEST_TYPE="$2";      shift 2 ;;
    --execution-order)  EXEC_ORDER="$2";     shift 2 ;;
    --rest)             REST_FILE="$2";      shift 2 ;;
    --trpc)             TRPC_FILE="$2";      shift 2 ;;
    --rest-resource)    REST_RESOURCE="$2";  shift 2 ;;
    --trpc-resource)    TRPC_RESOURCE="$2";  shift 2 ;;
    --rest-network)     REST_NETWORK="$2";   shift 2 ;;
    --trpc-network)     TRPC_NETWORK="$2";   shift 2 ;;
    --rest-pgstats)     REST_PGSTATS="$2";   shift 2 ;;
    --trpc-pgstats)     TRPC_PGSTATS="$2";   shift 2 ;;
    --note)             NOTE="$2";           shift 2 ;;
    *) echo "❌ Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Validasi argumen wajib ─────────────────────────────────────
missing=()
[ -z "$RUN" ]        && missing+=("--run")
[ -z "$SCENARIO" ]   && missing+=("--scenario")
[ -z "$TEST_TYPE" ]  && missing+=("--test-type")
[ -z "$REST_FILE" ]  && missing+=("--rest")
[ -z "$TRPC_FILE" ]  && missing+=("--trpc")

if [ ${#missing[@]} -gt 0 ]; then
  echo "❌ Argumen wajib tidak lengkap: ${missing[*]}"
  exit 1
fi

# ── Cek keberadaan file (warning saja) ────────────────────────
for f in "$REST_FILE" "$TRPC_FILE" "$REST_RESOURCE" "$TRPC_RESOURCE" \
         "$REST_NETWORK" "$TRPC_NETWORK" "$REST_PGSTATS" "$TRPC_PGSTATS"; do
  [ -n "$f" ] && [ ! -f "$RESULTS_DIR/$f" ] && \
    echo "⚠️  File tidak ditemukan: $RESULTS_DIR/$f"
done

# ── Init manifest kalau belum ada ─────────────────────────────
mkdir -p "$RESULTS_DIR"
if [ ! -f "$MANIFEST" ]; then
  echo "[]" > "$MANIFEST"
  echo "📋 Manifest baru dibuat: $MANIFEST"
fi

# ── Jalankan logic lewat Node.js ──────────────────────────────
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

node - << JSEOF
const fs = require('fs');

const manifestPath = "$MANIFEST";
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

// Cek duplikat
const isDuplicate = manifest.some(
  m => m.run === $RUN && m.scenario === "$SCENARIO" && m.test_type === "$TEST_TYPE"
);

if (isDuplicate) {
  console.error("⚠️  Run $RUN untuk $SCENARIO/$TEST_TYPE sudah ada di manifest.");
  console.error("   Gunakan --run dengan nomor yang berbeda, atau hapus entry lama dari manifest.");
  process.exit(1);
}

const entry = {
  run:             $RUN,
  scenario:        "$SCENARIO",
  test_type:       "$TEST_TYPE",
  execution_order: "$EXEC_ORDER",
  recorded_at:     "$TIMESTAMP",
  rest:            "$REST_FILE",
  trpc:            "$TRPC_FILE",
  rest_resource:   "$REST_RESOURCE",
  trpc_resource:   "$TRPC_RESOURCE",
  rest_network:    "$REST_NETWORK",
  trpc_network:    "$TRPC_NETWORK",
  rest_pgstats:    "$REST_PGSTATS",
  trpc_pgstats:    "$TRPC_PGSTATS",
  note:            "$NOTE",
};

manifest.push(entry);
manifest.sort((a, b) =>
  a.scenario.localeCompare(b.scenario) ||
  a.test_type.localeCompare(b.test_type) ||
  a.run - b.run
);

fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8');

console.log(\`✅ Recorded: run=\${entry.run} | \${entry.scenario}/\${entry.test_type}\`);
console.log(\`   REST            : \${entry.rest}\`);
console.log(\`   tRPC            : \${entry.trpc}\`);
console.log(\`   REST resource   : \${entry.rest_resource}\`);
console.log(\`   tRPC resource   : \${entry.trpc_resource}\`);
console.log(\`   REST network    : \${entry.rest_network}\`);
console.log(\`   tRPC network    : \${entry.trpc_network}\`);
console.log(\`   REST pgstats    : \${entry.rest_pgstats}\`);
console.log(\`   tRPC pgstats    : \${entry.trpc_pgstats}\`);
console.log(\`   Execution order : \${entry.execution_order}\`);
if (entry.note) console.log(\`   Note            : \${entry.note}\`);
console.log(\`   Total entries di manifest: \${manifest.length}\`);
JSEOF