#!/usr/bin/env bash
# =============================================================
# reset_db.sh — Reset database antara setiap run k6
#
# Usage:
#   ./reset_db.sh                     # pakai DATABASE_URL dari env
#   ./reset_db.sh "postgresql://..."  # override URL langsung
#
# Bekerja di: Windows (Git Bash), Linux/VPS
# Letakkan file ini di: k6-performance/
# =============================================================

# ── Resolve DB URL ─────────────────────────────────────────────
if [ -n "$1" ]; then
  DB="$1"
elif [ -n "$DATABASE_URL" ]; then
  DB="$DATABASE_URL"
else
  # Fallback lokal — ganti sesuai config kamu kalau berbeda
  DB="postgresql://zenit:zenit123@localhost:5432/ecommerce_db"
fi

echo ""
echo "🔄  Resetting database..."
echo "    DB: ${DB%%@*}@[hidden]"

psql "$DB" -q << 'SQL'
-- Hapus data transaksi (urutan penting karena FK constraint)
DELETE FROM order_items;
DELETE FROM orders;
DELETE FROM cart_items;

-- Reset cart status → 'active' supaya cart_add tidak 500 (S-03 fix)
UPDATE carts SET status = 'active';

-- Reset stok produk (dipakai S-03 checkout yang decrement stok)
UPDATE products SET stock = 999 WHERE stock < 999;

-- Hapus user test dari S-04 (register flow buat user baru tiap run)
DELETE FROM refresh_tokens
  WHERE user_id IN (SELECT id FROM users WHERE email LIKE '%@k6test.dev');
DELETE FROM carts
  WHERE user_id IN (SELECT id FROM users WHERE email LIKE '%@k6test.dev');
DELETE FROM users WHERE email LIKE '%@k6test.dev';

-- Hapus refresh token kadaluarsa (bersihkan table bloat)
DELETE FROM refresh_tokens
  WHERE created_at < NOW() - INTERVAL '10 minutes';
SQL

if [ $? -ne 0 ]; then
  echo "❌  Reset GAGAL. Cek koneksi DB atau string koneksi."
  exit 1
fi

echo "✅  Reset selesai."
echo ""
