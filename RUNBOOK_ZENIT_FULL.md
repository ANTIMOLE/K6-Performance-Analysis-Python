# RUNBOOK FINAL — REST vs tRPC Performance Testing
**Project:** Zenit E-Commerce | Tugas Akhir Informatika UAJY
**VPS:** `139.59.98.14` | 2 vCPU, 8 GB RAM
**REST:** port 4000 | **tRPC:** port 4001 | **DB:** ecommerce_db / zenit
**k6-performance dir:** `/home/zenit/e-commerce/k6-performance`

> **[A]** = Terminal A — VPS (monitor, pgstats, network)
> **[B]** = Terminal B — Laptop Git Bash (k6, record)
> **[C]** = Terminal C — VPS (reset DB, pm2, health check)

---

## 🚨 DARURAT — Kill Monitor Nyangkut
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor stopped"
```
Kalau masih jalan:
```bash
ps -ef | grep monitor_resources | grep -v grep
```
Lihat PID → `kill PID`.

---

## 🔑 SSH
```bash
ssh zenit-vps echo "✓ SSH OK"
```

---

## 🚀 PRE-FLIGHT — Wajib Setiap Sesi

**[C] Health check + pm2:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 status && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**[C] Cek pg_stat_statements:**
```bash
export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -c "SELECT count(*) FROM pg_stat_statements LIMIT 1;" 2>&1 | grep -q "count" && echo "✓ pg_stat OK" || echo "⚠️ pg_stat MISSING"
```

Kalau MISSING:
```bash
sudo -u postgres psql -c "ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';" && sudo systemctl restart postgresql && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" && echo "✓ Fixed"
```

**[A] Cek network interface (sekali saja):**
```bash
curl -s "http://localhost:9090/api/v1/query_range" \
  --data-urlencode "query=rate(node_network_receive_bytes_total{device!="lo"}[30s]) / 1024" \
  --data-urlencode "start=$(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --data-urlencode "end=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --data-urlencode "step=30" \
  | python3 -c "
import json,sys
r = json.load(sys.stdin)
for s in r['data']['result']:
    print(s['metric'].get('device','?'), '—', len(s['values']), 'points')
"
```
> Catat nama interface aktif. Ganti `eth0` di semua network command kalau bukan `eth0`.

---

## 📋 URUTAN RUN & COUNTERBALANCING

- **Run ganjil (1,3,5,7,9):** REST → tRPC
- **Run genap (2,4,6,8,10):** ⚠️ tRPC → REST
- **Soak (N=1):** selalu REST → tRPC
- Setiap API test wajib didahului `reset_db` + `pg_stat_statements_reset()`

---
---

# S01 BROWSE — `s01_browse.js`

## S01 BROWSE / LOAD — N=10

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s01_browse --test-type load --order rest-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s01_browse --test-type load --order trpc-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s01_browse --test-type load --order rest-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 4 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 4:**
```bash
./record_run.sh --run 4 --scenario s01_browse --test-type load --order trpc-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 5 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 5:**
```bash
./record_run.sh --run 5 --scenario s01_browse --test-type load --order rest-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 6 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 6:**
```bash
./record_run.sh --run 6 --scenario s01_browse --test-type load --order trpc-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 7 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 7:**
```bash
./record_run.sh --run 7 --scenario s01_browse --test-type load --order rest-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 8 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 8:**
```bash
./record_run.sh --run 8 --scenario s01_browse --test-type load --order trpc-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 9 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 9:**
```bash
./record_run.sh --run 9 --scenario s01_browse --test-type load --order rest-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 10 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 10:**
```bash
./record_run.sh --run 10 --scenario s01_browse --test-type load --order trpc-first --rest s01_browse_rest_load_${REST_TS}.json --trpc s01_browse_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s01_${REST_TS}.csv --trpc-resource resource_trpc_load_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S01 BROWSE / STRESS — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s01_browse --test-type stress --order rest-first --rest s01_browse_rest_stress_${REST_TS}.json --trpc s01_browse_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s01_${REST_TS}.csv --trpc-resource resource_trpc_stress_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s01_browse --test-type stress --order trpc-first --rest s01_browse_rest_stress_${REST_TS}.json --trpc s01_browse_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s01_${REST_TS}.csv --trpc-resource resource_trpc_stress_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s01_browse --test-type stress --order rest-first --rest s01_browse_rest_stress_${REST_TS}.json --trpc s01_browse_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s01_${REST_TS}.csv --trpc-resource resource_trpc_stress_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S01 BROWSE / SPIKE — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s01_browse --test-type spike --order rest-first --rest s01_browse_rest_spike_${REST_TS}.json --trpc s01_browse_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s01_${REST_TS}.csv --trpc-resource resource_trpc_spike_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s01_browse --test-type spike --order trpc-first --rest s01_browse_rest_spike_${REST_TS}.json --trpc s01_browse_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s01_${REST_TS}.csv --trpc-resource resource_trpc_spike_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s01_browse --test-type spike --order rest-first --rest s01_browse_rest_spike_${REST_TS}.json --trpc s01_browse_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s01_${REST_TS}.csv --trpc-resource resource_trpc_spike_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S01 BROWSE / SOAK — N=1

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest soak s01 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=soak --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_soak_s01_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_soak_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc soak s01 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=soak --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s01_browse.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_soak_s01_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_soak_s01_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s01_browse --test-type soak --order rest-first --rest s01_browse_rest_soak_${REST_TS}.json --trpc s01_browse_trpc_soak_${TRPC_TS}.json --rest-resource resource_rest_soak_s01_${REST_TS}.csv --trpc-resource resource_trpc_soak_s01_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

---
---

# S02 SHOPPING — `s02_shopping.js`

## S02 SHOPPING / LOAD — N=10

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s02_shopping --test-type load --order rest-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s02_shopping --test-type load --order trpc-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s02_shopping --test-type load --order rest-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 4 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 4:**
```bash
./record_run.sh --run 4 --scenario s02_shopping --test-type load --order trpc-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 5 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 5:**
```bash
./record_run.sh --run 5 --scenario s02_shopping --test-type load --order rest-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 6 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 6:**
```bash
./record_run.sh --run 6 --scenario s02_shopping --test-type load --order trpc-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 7 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 7:**
```bash
./record_run.sh --run 7 --scenario s02_shopping --test-type load --order rest-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 8 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 8:**
```bash
./record_run.sh --run 8 --scenario s02_shopping --test-type load --order trpc-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 9 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 9:**
```bash
./record_run.sh --run 9 --scenario s02_shopping --test-type load --order rest-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 10 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 10:**
```bash
./record_run.sh --run 10 --scenario s02_shopping --test-type load --order trpc-first --rest s02_shopping_rest_load_${REST_TS}.json --trpc s02_shopping_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s02_${REST_TS}.csv --trpc-resource resource_trpc_load_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S02 SHOPPING / STRESS — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s02_shopping --test-type stress --order rest-first --rest s02_shopping_rest_stress_${REST_TS}.json --trpc s02_shopping_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s02_${REST_TS}.csv --trpc-resource resource_trpc_stress_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s02_shopping --test-type stress --order trpc-first --rest s02_shopping_rest_stress_${REST_TS}.json --trpc s02_shopping_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s02_${REST_TS}.csv --trpc-resource resource_trpc_stress_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s02_shopping --test-type stress --order rest-first --rest s02_shopping_rest_stress_${REST_TS}.json --trpc s02_shopping_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s02_${REST_TS}.csv --trpc-resource resource_trpc_stress_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S02 SHOPPING / SPIKE — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s02_shopping --test-type spike --order rest-first --rest s02_shopping_rest_spike_${REST_TS}.json --trpc s02_shopping_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s02_${REST_TS}.csv --trpc-resource resource_trpc_spike_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s02_shopping --test-type spike --order trpc-first --rest s02_shopping_rest_spike_${REST_TS}.json --trpc s02_shopping_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s02_${REST_TS}.csv --trpc-resource resource_trpc_spike_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s02_shopping --test-type spike --order rest-first --rest s02_shopping_rest_spike_${REST_TS}.json --trpc s02_shopping_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s02_${REST_TS}.csv --trpc-resource resource_trpc_spike_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S02 SHOPPING / SOAK — N=1

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest soak s02 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=soak --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_soak_s02_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_soak_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc soak s02 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=soak --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_soak_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_soak_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s02_shopping --test-type soak --order rest-first --rest s02_shopping_rest_soak_${REST_TS}.json --trpc s02_shopping_trpc_soak_${TRPC_TS}.json --rest-resource resource_rest_soak_s02_${REST_TS}.csv --trpc-resource resource_trpc_soak_s02_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

---
---

# S03 CHECKOUT — `s03_checkout.js`

## S03 CHECKOUT / LOAD — N=10

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s03_checkout --test-type load --order rest-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s03_checkout --test-type load --order trpc-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s03_checkout --test-type load --order rest-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 4 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 4:**
```bash
./record_run.sh --run 4 --scenario s03_checkout --test-type load --order trpc-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 5 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 5:**
```bash
./record_run.sh --run 5 --scenario s03_checkout --test-type load --order rest-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 6 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 6:**
```bash
./record_run.sh --run 6 --scenario s03_checkout --test-type load --order trpc-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 7 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 7:**
```bash
./record_run.sh --run 7 --scenario s03_checkout --test-type load --order rest-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 8 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 8:**
```bash
./record_run.sh --run 8 --scenario s03_checkout --test-type load --order trpc-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 9 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 9:**
```bash
./record_run.sh --run 9 --scenario s03_checkout --test-type load --order rest-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 10 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 10:**
```bash
./record_run.sh --run 10 --scenario s03_checkout --test-type load --order trpc-first --rest s03_checkout_rest_load_${REST_TS}.json --trpc s03_checkout_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s03_${REST_TS}.csv --trpc-resource resource_trpc_load_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S03 CHECKOUT / STRESS — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s03_checkout --test-type stress --order rest-first --rest s03_checkout_rest_stress_${REST_TS}.json --trpc s03_checkout_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s03_${REST_TS}.csv --trpc-resource resource_trpc_stress_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s03_checkout --test-type stress --order trpc-first --rest s03_checkout_rest_stress_${REST_TS}.json --trpc s03_checkout_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s03_${REST_TS}.csv --trpc-resource resource_trpc_stress_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s03_checkout --test-type stress --order rest-first --rest s03_checkout_rest_stress_${REST_TS}.json --trpc s03_checkout_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s03_${REST_TS}.csv --trpc-resource resource_trpc_stress_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S03 CHECKOUT / SPIKE — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s03_checkout --test-type spike --order rest-first --rest s03_checkout_rest_spike_${REST_TS}.json --trpc s03_checkout_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s03_${REST_TS}.csv --trpc-resource resource_trpc_spike_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s03_checkout --test-type spike --order trpc-first --rest s03_checkout_rest_spike_${REST_TS}.json --trpc s03_checkout_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s03_${REST_TS}.csv --trpc-resource resource_trpc_spike_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s03_checkout --test-type spike --order rest-first --rest s03_checkout_rest_spike_${REST_TS}.json --trpc s03_checkout_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s03_${REST_TS}.csv --trpc-resource resource_trpc_spike_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S03 CHECKOUT / SOAK — N=1

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest soak s03 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=soak --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_soak_s03_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_soak_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc soak s03 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=soak --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_soak_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_soak_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s03_checkout --test-type soak --order rest-first --rest s03_checkout_rest_soak_${REST_TS}.json --trpc s03_checkout_trpc_soak_${TRPC_TS}.json --rest-resource resource_rest_soak_s03_${REST_TS}.csv --trpc-resource resource_trpc_soak_s03_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

---
---

# S04 AUTH — `s04_auth.js`

## S04 AUTH / LOAD — N=10

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s04_auth --test-type load --order rest-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s04_auth --test-type load --order trpc-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s04_auth --test-type load --order rest-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 4 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 4:**
```bash
./record_run.sh --run 4 --scenario s04_auth --test-type load --order trpc-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 5 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 5:**
```bash
./record_run.sh --run 5 --scenario s04_auth --test-type load --order rest-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 6 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 6:**
```bash
./record_run.sh --run 6 --scenario s04_auth --test-type load --order trpc-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 7 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 7:**
```bash
./record_run.sh --run 7 --scenario s04_auth --test-type load --order rest-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 8 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 8:**
```bash
./record_run.sh --run 8 --scenario s04_auth --test-type load --order trpc-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 9 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 9:**
```bash
./record_run.sh --run 9 --scenario s04_auth --test-type load --order rest-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 10 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 10:**
```bash
./record_run.sh --run 10 --scenario s04_auth --test-type load --order trpc-first --rest s04_auth_rest_load_${REST_TS}.json --trpc s04_auth_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s04_${REST_TS}.csv --trpc-resource resource_trpc_load_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S04 AUTH / STRESS — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s04_auth --test-type stress --order rest-first --rest s04_auth_rest_stress_${REST_TS}.json --trpc s04_auth_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s04_${REST_TS}.csv --trpc-resource resource_trpc_stress_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s04_auth --test-type stress --order trpc-first --rest s04_auth_rest_stress_${REST_TS}.json --trpc s04_auth_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s04_${REST_TS}.csv --trpc-resource resource_trpc_stress_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s04_auth --test-type stress --order rest-first --rest s04_auth_rest_stress_${REST_TS}.json --trpc s04_auth_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s04_${REST_TS}.csv --trpc-resource resource_trpc_stress_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S04 AUTH / SPIKE — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s04_auth --test-type spike --order rest-first --rest s04_auth_rest_spike_${REST_TS}.json --trpc s04_auth_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s04_${REST_TS}.csv --trpc-resource resource_trpc_spike_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s04_auth --test-type spike --order trpc-first --rest s04_auth_rest_spike_${REST_TS}.json --trpc s04_auth_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s04_${REST_TS}.csv --trpc-resource resource_trpc_spike_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s04_auth --test-type spike --order rest-first --rest s04_auth_rest_spike_${REST_TS}.json --trpc s04_auth_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s04_${REST_TS}.csv --trpc-resource resource_trpc_spike_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S04 AUTH / SOAK — N=1

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest soak s04 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=soak --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_soak_s04_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_soak_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc soak s04 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=soak --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_soak_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_soak_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s04_auth --test-type soak --order rest-first --rest s04_auth_rest_soak_${REST_TS}.json --trpc s04_auth_trpc_soak_${TRPC_TS}.json --rest-resource resource_rest_soak_s04_${REST_TS}.csv --trpc-resource resource_trpc_soak_s04_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

---
---

# S05 ADMIN — `s05_admin.js`

## S05 ADMIN / LOAD — N=10

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s05_admin --test-type load --order rest-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s05_admin --test-type load --order trpc-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s05_admin --test-type load --order rest-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 4 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 4:**
```bash
./record_run.sh --run 4 --scenario s05_admin --test-type load --order trpc-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 5 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 5:**
```bash
./record_run.sh --run 5 --scenario s05_admin --test-type load --order rest-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 6 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 6:**
```bash
./record_run.sh --run 6 --scenario s05_admin --test-type load --order trpc-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 7 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 7:**
```bash
./record_run.sh --run 7 --scenario s05_admin --test-type load --order rest-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 8 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 8:**
```bash
./record_run.sh --run 8 --scenario s05_admin --test-type load --order trpc-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 9 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 9:**
```bash
./record_run.sh --run 9 --scenario s05_admin --test-type load --order rest-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 10 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest load s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_load_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 10:**
```bash
./record_run.sh --run 10 --scenario s05_admin --test-type load --order trpc-first --rest s05_admin_rest_load_${REST_TS}.json --trpc s05_admin_trpc_load_${TRPC_TS}.json --rest-resource resource_rest_load_s05_${REST_TS}.csv --trpc-resource resource_trpc_load_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S05 ADMIN / STRESS — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s05_admin --test-type stress --order rest-first --rest s05_admin_rest_stress_${REST_TS}.json --trpc s05_admin_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s05_${REST_TS}.csv --trpc-resource resource_trpc_stress_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s05_admin --test-type stress --order trpc-first --rest s05_admin_rest_stress_${REST_TS}.json --trpc s05_admin_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s05_${REST_TS}.csv --trpc-resource resource_trpc_stress_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest stress s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=stress --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_stress_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_stress_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc stress s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=stress --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_stress_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_stress_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s05_admin --test-type stress --order rest-first --rest s05_admin_rest_stress_${REST_TS}.json --trpc s05_admin_trpc_stress_${TRPC_TS}.json --rest-resource resource_rest_stress_s05_${REST_TS}.csv --trpc-resource resource_trpc_stress_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S05 ADMIN / SPIKE — N=3

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s05_admin --test-type spike --order rest-first --rest s05_admin_rest_spike_${REST_TS}.json --trpc s05_admin_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s05_${REST_TS}.csv --trpc-resource resource_trpc_spike_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 2 (⚠️ tRPC → REST)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— tRPC DULU —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[C] Reset antara tRPC dan REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[B] Record Run 2:**
```bash
./record_run.sh --run 2 --scenario s05_admin --test-type spike --order trpc-first --rest s05_admin_rest_spike_${REST_TS}.json --trpc s05_admin_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s05_${REST_TS}.csv --trpc-resource resource_trpc_spike_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

### Run 3 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest spike s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=spike --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_spike_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_spike_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc spike s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=spike --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_spike_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_spike_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 3:**
```bash
./record_run.sh --run 3 --scenario s05_admin --test-type spike --order rest-first --rest s05_admin_rest_spike_${REST_TS}.json --trpc s05_admin_trpc_spike_${TRPC_TS}.json --rest-resource resource_rest_spike_s05_${REST_TS}.csv --trpc-resource resource_trpc_spike_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

## S05 ADMIN / SOAK — N=1

---

### Run 1 (REST → tRPC)

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**— REST —**

**[B] Set REST_TS:**
```bash
REST_TS=$(date +%s) && echo ">>> PASTE KE [A]: REST_TS=${REST_TS}"
```

**[A] Paste REST_TS:**
```bash
REST_TS=XXXXXXXXXX
```

**[A] Stop tRPC + mulai monitor REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-trpc && ./monitor_resources.sh rest soak s05 5 $REST_TS &
```

**[B] k6 REST:**
```bash
k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=soak --env REST_URL=https://139.59.98.14/api/v1 --env TS=${REST_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor REST stopped"
```

**[A] pgstats REST:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_rest_soak_s05_${REST_TS}.csv 2>/dev/null && echo "✓ pgstats REST saved" || echo "⚠️ pgstats gagal"
```

**[A] Network REST:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${REST_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_rest_soak_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start tRPC kembali:**
```bash
pm2 start backend-trpc && sleep 5
```

**[C] Reset antara REST dan tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();"
```

**— tRPC —**

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc soak s05 5 $TRPC_TS &
```

**[B] k6 tRPC:**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=soak --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor tRPC stopped"
```

**[A] pgstats tRPC:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_trpc_soak_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats tRPC saved" || echo "⚠️ pgstats gagal"
```

**[A] Network tRPC:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_trpc_soak_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record Run 1:**
```bash
./record_run.sh --run 1 --scenario s05_admin --test-type soak --order rest-first --rest s05_admin_rest_soak_${REST_TS}.json --trpc s05_admin_trpc_soak_${TRPC_TS}.json --rest-resource resource_rest_soak_s05_${REST_TS}.csv --trpc-resource resource_trpc_soak_s05_${TRPC_TS}.csv
```

**[B] Cooldown:**
```bash
echo "Cooldown 60s..." && sleep 60
```

---

---
---

---
---

# DECOMPOSITION RUNS — C3 & C4

> C3 dan C4 **tidak** memerlukan run REST baru.
> Di-pair dengan data C2 yang sudah ada dari main runs.
> Masing-masing N=1, framing: estimasi magnitude bukan hypothesis testing.

```
Total Gap  =  Protocol Gap  +  Auth Gap           +  Batching Benefit
                 C1 vs C2       C2 vs C3 (auth)       C2 vs C4 (batch)
```

---

## C3 — tRPC Auth-Equalized (AUTH_DB_VALIDATION=true)
> Skenario: S02, S03, S04, S05 — Load only


### C3 S02_SHOPPING — tRPC Auth-Equalized (AUTH_DB_VALIDATION=true)

> Paired dengan data C2 yang sudah ada dari main runs. Tidak perlu REST baru.

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC C3 (AUTH_DB_VALIDATION=true):**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} --env AUTH_DB_VALIDATION=true s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor C3 stopped"
```

**[A] pgstats C3:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_c3_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats C3 saved" || echo "⚠️ gagal"
```

**[A] Network C3:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_c3_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record C3:**
```bash
./record_run.sh --run 1 --scenario s02_shopping --test-type load --condition C3 --trpc s02_shopping_c3_load_${TRPC_TS}.json --trpc-resource resource_c3_load_s02_${TRPC_TS}.csv
```

---

### C3 S03_CHECKOUT — tRPC Auth-Equalized (AUTH_DB_VALIDATION=true)

> Paired dengan data C2 yang sudah ada dari main runs. Tidak perlu REST baru.

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s03 5 $TRPC_TS &
```

**[B] k6 tRPC C3 (AUTH_DB_VALIDATION=true):**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} --env AUTH_DB_VALIDATION=true s03_checkout.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor C3 stopped"
```

**[A] pgstats C3:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_c3_load_s03_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats C3 saved" || echo "⚠️ gagal"
```

**[A] Network C3:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_c3_load_s03_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record C3:**
```bash
./record_run.sh --run 1 --scenario s03_checkout --test-type load --condition C3 --trpc s03_checkout_c3_load_${TRPC_TS}.json --trpc-resource resource_c3_load_s03_${TRPC_TS}.csv
```

---

### C3 S04_AUTH — tRPC Auth-Equalized (AUTH_DB_VALIDATION=true)

> Paired dengan data C2 yang sudah ada dari main runs. Tidak perlu REST baru.

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s04 5 $TRPC_TS &
```

**[B] k6 tRPC C3 (AUTH_DB_VALIDATION=true):**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} --env AUTH_DB_VALIDATION=true s04_auth.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor C3 stopped"
```

**[A] pgstats C3:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_c3_load_s04_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats C3 saved" || echo "⚠️ gagal"
```

**[A] Network C3:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_c3_load_s04_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record C3:**
```bash
./record_run.sh --run 1 --scenario s04_auth --test-type load --condition C3 --trpc s04_auth_c3_load_${TRPC_TS}.json --trpc-resource resource_c3_load_s04_${TRPC_TS}.csv
```

---

### C3 S05_ADMIN — tRPC Auth-Equalized (AUTH_DB_VALIDATION=true)

> Paired dengan data C2 yang sudah ada dari main runs. Tidak perlu REST baru.

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s05 5 $TRPC_TS &
```

**[B] k6 tRPC C3 (AUTH_DB_VALIDATION=true):**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} --env AUTH_DB_VALIDATION=true s05_admin.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor C3 stopped"
```

**[A] pgstats C3:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_c3_load_s05_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats C3 saved" || echo "⚠️ gagal"
```

**[A] Network C3:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_c3_load_s05_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record C3:**
```bash
./record_run.sh --run 1 --scenario s05_admin --test-type load --condition C3 --trpc s05_admin_c3_load_${TRPC_TS}.json --trpc-resource resource_c3_load_s05_${TRPC_TS}.csv
```

---

---

## C4 — tRPC Batch-Enabled (httpBatchLink)
> Skenario: S02 — Load only


### C4 S02_SHOPPING — tRPC Batch-Enabled (httpBatchLink)

> Paired dengan data C2 yang sudah ada dari main runs. Tidak perlu REST baru.

**[C] Reset DB + pg_stat_reset:**
```bash
cd /home/zenit/e-commerce/k6-performance && ./reset_db.sh && pm2 restart backend-rest backend-trpc && sleep 10 && sudo -u postgres psql -d ecommerce_db -c "SELECT pg_stat_statements_reset();" && curl -sf http://localhost:4000/health && echo "REST OK" && curl -sf http://localhost:4001/health && echo "tRPC OK"
```

**[B] Set TRPC_TS:**
```bash
TRPC_TS=$(date +%s) && echo ">>> PASTE KE [A]: TRPC_TS=${TRPC_TS}"
```

**[A] Paste TRPC_TS:**
```bash
TRPC_TS=XXXXXXXXXX
```

**[A] Stop REST + mulai monitor:**
```bash
cd /home/zenit/e-commerce/k6-performance && pm2 stop backend-rest && ./monitor_resources.sh trpc load s02 5 $TRPC_TS &
```

**[B] k6 tRPC C4 (BATCH=true):**
```bash
k6 run --insecure-skip-tls-verify --env API=trpc --env TEST_TYPE=load --env TRPC_URL=https://139.59.98.14/trpc --env TS=${TRPC_TS} --env BATCH=true s02_shopping.js
```

**[A] Kill monitor:**
```bash
pkill -f monitor_resources.sh; echo "✓ Monitor C4 stopped"
```

**[A] pgstats C4:**
```bash
cd /home/zenit/e-commerce/k6-performance && export PGPASSWORD=220711833 && psql -h localhost -U zenit -d ecommerce_db -t -c "COPY (SELECT LEFT(query,120) AS query, calls, ROUND(mean_exec_time::numeric,2) AS avg_ms, ROUND(total_exec_time::numeric,2) AS total_ms, rows FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50) TO STDOUT WITH CSV HEADER" > results/pgstats_c4_load_s02_${TRPC_TS}.csv 2>/dev/null && echo "✓ pgstats C4 saved" || echo "⚠️ gagal"
```

**[A] Network C4:**
```bash
python3 << PYEOF
import urllib.request, urllib.parse, json, csv, time
from datetime import datetime, timezone
PROM = 'http://localhost:9090/api/v1/query_range'
DEVICE = 'eth0'
TS = ${TRPC_TS}
START = datetime.fromtimestamp(TS, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
END = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def query(q):
    params = urllib.parse.urlencode({'query': q, 'start': START, 'end': END, 'step': '5'})
    with urllib.request.urlopen(f'{PROM}?{params}') as r:
        return json.load(r)['data']['result']
rx = {ts: float(v) for ts, v in query(f'rate(node_network_receive_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
tx = {ts: float(v) for ts, v in query(f'rate(node_network_transmit_bytes_total{{device="{DEVICE}"}}[30s]) / 1024')[0]['values']}
out = f'/home/zenit/e-commerce/k6-performance/results/network_c4_load_s02_{TS}.csv'
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp', 'net_rx_kb_s', 'net_tx_kb_s'])
    for ts in sorted(rx):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        w.writerow([dt, round(rx.get(ts,0),2), round(tx.get(ts,0),2)])
print(f'Saved: {out}')
PYEOF
```

**[A] Start REST kembali:**
```bash
pm2 start backend-rest && sleep 5
```

**[B] Record C4:**
```bash
./record_run.sh --run 1 --scenario s02_shopping --test-type load --condition C4 --trpc s02_shopping_c4_load_${TRPC_TS}.json --trpc-resource resource_c4_load_s02_${TRPC_TS}.csv
```

---

---
---

# ✅ VERIFIKASI MANIFEST

```bash
python3 -c "
import json
from collections import Counter
with open('results/run_manifest.json') as f: m = json.load(f)
runs = m.get('runs', m) if isinstance(m, dict) else m
print(f'Total entries: {len(runs)}')
counter = Counter((e['scenario'], e['test_type']) for e in runs)
for (sc, tt), n in sorted(counter.items()):
    expected = 1 if tt == 'soak' else (10 if tt == 'load' else 3)
    flag = '✅' if n >= expected else f'❌ hanya {n}x (butuh {expected})'
    print(f'  {flag}  {sc}/{tt}: {n} run')
"
```

---

# 📊 MASTER CHECKLIST

| Skenario | Load N=10 | Stress N=3 | Spike N=3 | Soak N=1 |
|----------|:---------:|:----------:|:---------:|:--------:|
| S01 Browse | ⬜ | ⬜ | ⬜ | ⬜ |
| S02 Shopping | ⬜ | ⬜ | ⬜ | ⬜ |
| S03 Checkout | ⬜ | ⬜ | ⬜ | ⬜ |
| S04 Auth | ⬜ | ⬜ | ⬜ | ⬜ |
| S05 Admin | ⬜ | ⬜ | ⬜ | ⬜ |
| **C3** | S02 ⬜ S03 ⬜ S04 ⬜ S05 ⬜ | — | — | — |
| **C4** | S02 ⬜ | — | — | — |

**Total main runs: 170 | Decomposition: 5 | Grand total: 175**

---

# 📌 ROOT CAUSE FIXES

| Bug | Fix |
|-----|-----|
| Monitor tidak terkill | Selalu pakai `pkill -f monitor_resources.sh` |
| pgstats tercampur antar API | `pg_stat_statements_reset()` sebelum setiap test |
| Health check tRPC salah URL | Pakai `http://localhost:4001/health` (bukan `/health/trpc`) |
| `handleSummary` filename unpredictable | Pakai `__ENV.TS` dengan fallback `Date.now()` |
| Order effect | Alternating: ganjil=REST-first, genap=tRPC-first |
| Monitor dir salah | Setiap command VPS mulai dengan `cd /home/zenit/e-commerce/k6-performance &&` |
