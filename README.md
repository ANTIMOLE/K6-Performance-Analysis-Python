# K6-Performance - Analysis Python

k6 load testing scripts dan Python analysis pipeline untuk proyek benchmark [Zenit](https://github.com/ANTIMOLE/ZENIT_VPS_TA).

> **Undergraduate Thesis (Skripsi) — Universitas Atma Jaya Yogyakarta**  
> Program Studi Informatika · NIM 220711833

---

## Structure

```
K6-Performance/
├── scenarios/           # k6 test scripts per scenario
│   ├── s01_auth/
│   ├── s02_product/
│   ├── s03_cart/
│   ├── s04_order/
│   └── s05_mixed/
├── results/             # Raw k6 JSON output & resource CSVs
├── analysis/            # Python analysis pipeline
│   ├── main.py
│   ├── parse_k6.py
│   ├── analyze.py
│   ├── interpret.py
│   └── report.py
└── runbook/             # Test execution scripts
```

---

## Test Design

### Scenarios

| ID | Endpoint Group |
|---|---|
| S01 | Auth (register, login) |
| S02 | Product (listing, search, detail) |
| S03 | Cart (add, update, remove) |
| S04 | Order (checkout, history) |
| S05 | Mixed (concurrent multi-endpoint) |

### Test Types

| Type | N runs |
|---|---|
| Load | 10 |
| Stress | 3 |
| Spike | 3 |
| Soak | 1 |

### Conditions

| Code | Description |
|---|---|
| C1 | REST API (baseline) |
| C2 | tRPC — no cache |
| C3 | tRPC — with Redis cache |
| C4 | tRPC — with composite DB index |

---

## Analysis Pipeline

| Script | Role |
|---|---|
| `parse_k6.py` | Parse k6 JSON summary → DataFrame |
| `analyze.py` | Paired t-test, Wilcoxon, Cohen's d |
| `interpret.py` | Auto-generate narrative per RQ |
| `report.py` | Aggregate final report tables |
| `main.py` | Orchestrator |

---

## Usage

```bash
# Run a test
k6 run --out json=results/s01_load_c1.json scenarios/s01_auth/load.js

# Run full analysis
cd analysis
python main.py
```

---

## Related

- **Zenit VPS** (sandbox): [ANTIMOLE/ZENIT_VPS_TA](https://github.com/ANTIMOLE/ZENIT_VPS_TA)

---

## Author

**Angello Khara Sitanggang** · NIM 220711833  
Informatika — Universitas Atma Jaya Yogyakarta · [@ANTIMOLE](https://github.com/ANTIMOLE)
