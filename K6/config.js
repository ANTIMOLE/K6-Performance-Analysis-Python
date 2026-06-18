// // =============================================================
// // helpers/config.js — Shared configuration for all k6 scenarios
// //
// // FIX: Hapus error_rate dari THRESHOLDS_BASE karena skenario sekarang
// // pakai functional_error_rate dan sla_breach_rate yang terpisah.
// // error_rate lama yang mencampur 4xx/5xx dengan latency breach sudah deprecated.
// // =============================================================

// export const API_TYPE = __ENV.API || "rest"; // "rest" | "trpc"
// export const TEST_TYPE = __ENV.TEST_TYPE || "load"; // "load" | "stress" | "spike" | "soak"

// // Base URLs — update ke VPS IP sebelum run
// export const BASE_URL = {
//   rest: __ENV.REST_URL || "http://localhost:4000/api/v1",
//   trpc: __ENV.TRPC_URL || "http://localhost:4001/trpc",
// };
// export const BASE = BASE_URL[API_TYPE];

// export const HEALTH_URL =
//   API_TYPE === "trpc"
//     ? BASE.replace("/trpc", "") + "/health/trpc"
//     : BASE.replace("/api/v1", "") + "/health";
// // =============================================================
// // STAGE DEFINITIONS
// // =============================================================

// export const STAGES = {
//   // Load Test — 3 tahap (50→100→200 VU), 15 menit steady each
//   load: [
//     { duration: "2m", target: 50 },
//     { duration: "15m", target: 50 },
//     { duration: "1m", target: 100 },
//     { duration: "15m", target: 100 },
//     { duration: "1m", target: 200 },
//     { duration: "15m", target: 200 },
//     { duration: "2m", target: 0 },
//   ],

//   // Stress Test — start 200 VU, +50 tiap 5 menit
//   stress: [
//     { duration: "2m", target: 200 },
//     { duration: "5m", target: 200 },
//     { duration: "1m", target: 250 },
//     { duration: "5m", target: 250 },
//     { duration: "1m", target: 300 },
//     { duration: "5m", target: 300 },
//     { duration: "1m", target: 350 },
//     { duration: "5m", target: 350 },
//     { duration: "1m", target: 400 },
//     { duration: "5m", target: 400 },
//     { duration: "1m", target: 450 },
//     { duration: "5m", target: 450 },
//     { duration: "1m", target: 500 },
//     { duration: "5m", target: 500 },
//     { duration: "2m", target: 0 },
//   ],

//   // Spike Test — 50 baseline → 500 spike (2m) → back to 50
//   spike: [
//     { duration: "5m", target: 50 },
//     { duration: "10s", target: 500 },
//     { duration: "2m", target: 500 },
//     { duration: "10s", target: 50 },
//     { duration: "5m", target: 50 },
//     { duration: "30s", target: 0 },
//   ],

//   // Soak Test — 150 VU for 4h minimum
//   soak: [
//     { duration: "5m", target: 150 },
//     { duration: "4h", target: 150 }, // ubah ke "8h" untuk full soak
//     { duration: "5m", target: 0 },
//   ],

//   // Auth-specific stages
//   load_auth: [
//     { duration: "2m", target: 50 },
//     { duration: "15m", target: 50 },
//     { duration: "1m", target: 100 },
//     { duration: "15m", target: 100 },
//     { duration: "2m", target: 0 },
//   ],
//   stress_auth: [
//     { duration: "2m", target: 100 },
//     { duration: "5m", target: 100 },
//     { duration: "1m", target: 150 },
//     { duration: "5m", target: 150 },
//     { duration: "1m", target: 200 },
//     { duration: "5m", target: 200 },
//     { duration: "1m", target: 300 },
//     { duration: "5m", target: 300 },
//     { duration: "2m", target: 0 },
//   ],
//   spike_auth: [
//     { duration: "5m", target: 50 },
//     { duration: "10s", target: 300 },
//     { duration: "2m", target: 300 },
//     { duration: "10s", target: 50 },
//     { duration: "5m", target: 50 },
//     { duration: "30s", target: 0 },
//   ],

//   // Admin-specific stages (traffic rendah, realistis)
//   load_admin: [
//     { duration: "2m", target: 10 },
//     { duration: "15m", target: 10 },
//     { duration: "1m", target: 20 },
//     { duration: "15m", target: 20 },
//     { duration: "1m", target: 30 },
//     { duration: "15m", target: 30 },
//     { duration: "2m", target: 0 },
//   ],
//   stress_admin: [
//     { duration: "2m", target: 30 },
//     { duration: "5m", target: 30 },
//     { duration: "1m", target: 40 },
//     { duration: "5m", target: 40 },
//     { duration: "1m", target: 50 },
//     { duration: "5m", target: 50 },
//     { duration: "1m", target: 75 },
//     { duration: "5m", target: 75 },
//     { duration: "2m", target: 0 },
//   ],
// };

// // =============================================================
// // THRESHOLDS
// //
// // FIX: Hapus error_rate dari base threshold — skenario sekarang pakai
// // functional_error_rate (4xx/5xx) dan sla_breach_rate (2xx tapi lambat)
// // secara terpisah. error_rate lama yang mencampur keduanya sudah deprecated
// // dan tidak dipakai di skenario manapun.
// //
// // Untuk threshold per-skenario, tambahkan di masing-masing options:
// //   functional_error_rate: ["rate<0.01"],
// //   sla_breach_rate:       ["rate<0.05"],
// // =============================================================

// export const THRESHOLDS_BASE = {
//   // FIX: error_rate dihapus dari sini — sudah diganti dua metrik terpisah
//   // Hanya threshold latency dan http_req_failed (k6 built-in) yang dipertahankan
//   http_req_duration: ["p(95)<1000", "p(99)<2000"],
//   http_req_failed: ["rate<0.01"], // k6 built-in: network-level failure (bukan app error)
// };

// export const THRESHOLDS_READ = {
//   ...THRESHOLDS_BASE,
//   http_req_duration: ["p(50)<300", "p(95)<500", "p(99)<1000"],
// };

// export const THRESHOLDS_WRITE = {
//   ...THRESHOLDS_BASE,
//   http_req_duration: ["p(50)<500", "p(95)<1000", "p(99)<3000"], // FIX: 2000→3000
// };

// // =============================================================
// // COMMON HEADERS
// // =============================================================

// export const JSON_HEADERS = { "Content-Type": "application/json" };

// // =============================================================
// // THINK TIME — Box-Muller transform, μ=2s, σ=0.5s
// // =============================================================

// export function thinkTime(mu, sigma) {
//   const m = mu || 2;
//   const s = sigma || 0.5;
//   const u1 = Math.random();
//   const u2 = Math.random();
//   const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
//   const t = m + s * z;
//   return Math.max(0.3, t);
// }

// // =============================================================
// // VU COUNT PER TEST TYPE — dipakai untuk validasi user pool
// // Nilai ini harus >= TEST_USERS.length di auth.js
// // =============================================================

// export const MAX_VU = {
//   load: 200,
//   stress: 500,
//   spike: 500,
//   soak: 150,
//   load_auth: 100,
//   stress_auth: 300,
//   spike_auth: 300,
//   load_admin: 30,
//   stress_admin: 75,
// };


// =============================================================
// helpers/config.js — Shared configuration for all k6 scenarios
//
// REVISION: Upgraded to 4 vCPU (DigitalOcean $64/mo)
//  - Load  : 3 tiers (100→200→300 VU), 10 min steady each
//  - Stress: ceiling naik ke 700 VU, 5 min per tier
//  - Spike : peak 700 VU
//  - Soak  : 200 VU
//  - Auth  : load 150 VU, stress 400 VU
//  - Admin : load 40 VU,  stress 100 VU
//
// Durasi per tier dikurangi (15→10 menit) karena VU lebih tinggi
// = sample accumulation lebih cepat. Sample count per tier di 300 VU
// dengan think time ~2s ≈ 90.000 requests → p99 sudah sangat stabil.
// =============================================================

export const API_TYPE  = __ENV.API       || "rest";  // "rest" | "trpc"
export const TEST_TYPE = __ENV.TEST_TYPE || "load";  // "load" | "stress" | "spike" | "soak"

export const BASE_URL = {
  rest: __ENV.REST_URL  || "http://localhost:4000/api/v1",
  trpc: __ENV.TRPC_URL  || "http://localhost:4001/trpc",
};
export const BASE = BASE_URL[API_TYPE];

export const HEALTH_URL =
  API_TYPE === "trpc"
    ? BASE.replace("/trpc", "") + "/health/trpc"
    : BASE.replace("/api/v1", "") + "/health";

// =============================================================
// STAGE DEFINITIONS
// =============================================================

export const STAGES = {

  // ------------------------------------------------------------------
  // Load Test — 3 tiers (100→200→300 VU), 10 min steady each
  // Prev: 50→100→200 VU, 15 min. Rationale: 4 vCPU bisa handle 300 VU
  // tanpa contention. Sample count @ 300 VU / 10 min >> 50 VU / 15 min.
  // ------------------------------------------------------------------
  load: [
    { duration: "2m",  target: 100 },
    { duration: "10m", target: 100 },
    { duration: "1m",  target: 200 },
    { duration: "10m", target: 200 },
    { duration: "1m",  target: 300 },
    { duration: "10m", target: 300 },
    { duration: "2m",  target: 0   },
  ],

  // ------------------------------------------------------------------
  // Stress Test — start 300 VU, +50 tiap 5 menit, ceiling 700 VU
  // Prev ceiling: 500 VU. 4 vCPU realistis handle 700 tanpa OS-level
  // scheduling noise yang significant.
  // ------------------------------------------------------------------
  stress: [
  { duration: "2m", target: 300 },
  { duration: "3m", target: 300 },  // baseline stress
  { duration: "1m", target: 400 },
  { duration: "3m", target: 400 },
  { duration: "1m", target: 500 },
  { duration: "3m", target: 500 },
  { duration: "1m", target: 600 },
  { duration: "3m", target: 600 },
  { duration: "1m", target: 700 },
  { duration: "3m", target: 700 },
  { duration: "2m", target: 0   },
],

  // ------------------------------------------------------------------
  // Spike Test — 100 baseline → 700 spike (2m) → back to 100
  // Prev: 50→500. Baseline naik ke 100 supaya ramp-up lebih realistis.
  // ------------------------------------------------------------------
  spike: [
    { duration: "3m",  target: 100 },
    { duration: "10s", target: 1500 },
    { duration: "2m",  target: 1500 },
    { duration: "10s", target: 100 },
    { duration: "3m",  target: 100 },
    { duration: "30s", target: 0   },
  ],

  // ------------------------------------------------------------------
  // Soak Test — 200 VU for 4–8h
  // Prev: 150 VU. 4 vCPU bisa sustain 200 VU tanpa thermal throttling.
  // ------------------------------------------------------------------
  soak: [
    { duration: "5m", target: 200 },
    { duration: "4h", target: 200 }, // ubah ke "8h" untuk full soak
    { duration: "5m", target: 0   },
  ],

  // ------------------------------------------------------------------
  // Auth-specific — bcrypt CPU-bound, ceiling lebih konservatif
  // Prev load: 100 VU. Naik ke 150 — node.js masih single thread
  // untuk bcrypt, tapi 4 vCPU kasih lebih banyak ruang untuk PostgreSQL
  // session management supaya nggak jadi bottleneck sekunder.
  // ------------------------------------------------------------------
  load_auth: [
    { duration: "2m",  target: 75  },
    { duration: "10m", target: 75  },
    { duration: "1m",  target: 115 },
    { duration: "10m", target: 115 },
    { duration: "1m",  target: 150 },
    { duration: "10m", target: 150 },
    { duration: "2m",  target: 0   },
  ],
  stress_auth: [
    { duration: "2m", target: 150 },
    { duration: "3m", target: 150 },
    { duration: "1m", target: 200 },
    { duration: "3m", target: 200 },
    { duration: "1m", target: 250 },
    { duration: "3m", target: 250 },
    { duration: "1m", target: 300 },
    { duration: "3m", target: 300 },
    { duration: "1m", target: 350 },
    { duration: "3m", target: 350 },
    { duration: "2m", target: 0   },
  ],
  spike_auth: [
    { duration: "3m",  target: 75  },
    { duration: "10s", target: 750 },
    { duration: "2m",  target: 750 },
    { duration: "10s", target: 75  },
    { duration: "3m",  target: 75  },
    { duration: "30s", target: 0   },
  ],

  soak_auth: [
    { duration: "5m", target: 75 },
    { duration: "4h", target: 75 }, 
    { duration: "5m", target: 0   },
  ],

  // ------------------------------------------------------------------
  // Admin-specific — traffic realistis rendah, naik sedikit
  // Prev load: 30 VU. Naik ke 40. Admin dashboard (7 parallel queries)
  // justru yang paling untung dari 4 vCPU PostgreSQL headroom.
  // ------------------------------------------------------------------
  load_admin: [
    { duration: "2m",  target: 15 },
    { duration: "10m", target: 15 },
    { duration: "1m",  target: 28 },
    { duration: "10m", target: 28 },
    { duration: "1m",  target: 40 },
    { duration: "10m", target: 40 },
    { duration: "2m",  target: 0  },
  ],
  stress_admin: [
    { duration: "2m", target: 40  },
    { duration: "3m", target: 40  },
    { duration: "1m", target: 55  },
    { duration: "3m", target: 55  },
    { duration: "1m", target: 70  },
    { duration: "3m", target: 70  },
    { duration: "1m", target: 85  },
    { duration: "3m", target: 85  },
    { duration: "1m", target: 100 },
    { duration: "3m", target: 100 },
    { duration: "2m", target: 0   },
  ],

  spike_admin:[
    { duration: "3m",  target: 15  },
    { duration: "10s", target: 215 },
    { duration: "2m",  target: 215 },
    { duration: "10s", target: 15  },
    { duration: "3m",  target: 15  },
    { duration: "30s", target: 0   },
  ],

  soak_admin: [
    { duration: "5m", target: 28 },
    { duration: "4h", target: 28 },
    { duration: "5m", target: 0   },
  ],
};

  

// =============================================================
// THRESHOLDS — tidak berubah dari revision sebelumnya
// =============================================================

export const THRESHOLDS_BASE = {
  http_req_duration: ["p(95)<1000", "p(99)<2000"],
  http_req_failed:   ["rate<0.01"],
};

export const THRESHOLDS_READ = {
  ...THRESHOLDS_BASE,
  http_req_duration: ["p(50)<300", "p(95)<500", "p(99)<1000"],
};

export const THRESHOLDS_WRITE = {
  ...THRESHOLDS_BASE,
  http_req_duration: ["p(50)<500", "p(95)<1000", "p(99)<3000"],
};

// =============================================================
// COMMON HEADERS
// =============================================================

export const JSON_HEADERS = { "Content-Type": "application/json" };

// =============================================================
// THINK TIME — Box-Muller transform
// =============================================================

export function thinkTime(mu, sigma) {
  const m = mu  || 2;
  const s = sigma || 0.5;
  const u1 = Math.random();
  const u2 = Math.random();
  const z  = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  return Math.max(0.3, m + s * z);
}

// =============================================================
// MAX VU PER TEST TYPE — untuk validasi user pool di setup()
// Nilai ini harus <= TEST_USERS.length di auth.js
// =============================================================

export const MAX_VU = {
  load:         300,
  stress:       700,
  spike:        1500,
  soak:         200,
  load_auth:    150,
  stress_auth:  400,
  spike_auth:   400,
  soak_auth:    75,
  load_admin:   40,
  stress_admin: 100,
  spike_admin:  215,
  soak_admin: 28,
};