// =============================================================
// s04_auth.js — Skenario S-04: Authentication Flow
//
// FIX B-01: File ini sebelumnya adalah duplikat identik dari auth.js
// (helper saja, tidak ada export default / options).
// File ini sekarang adalah skenario k6 yang valid dan bisa dijalankan.
//
// FIX REPEATABILITY: Register endpoint TIDAK dimasukkan ke dalam load loop.
// Register membuat user permanen di DB (bcrypt, INSERT) tanpa cleanup otomatis.
// Untuk benchmark berulang, ini akan terus menambah ukuran tabel users dan
// memengaruhi repeatability. Register diuji di smoke/functional test saja.
//
// Endpoint yang diuji (per VU per iterasi):
//   REST : POST /auth/login, GET /auth/me, POST /auth/refresh, POST /auth/logout
//   tRPC : auth.login, auth.me, auth.refresh, auth.logout
//
// Negative path (30% VU): login dengan credential salah → harus return 4xx
//
// Run examples:
//   k6 run s04_auth.js
//   k6 run -e API=trpc -e TEST_TYPE=stress s04_auth.js
//   k6 run -e API=rest  -e TEST_TYPE=spike  s04_auth.js
// =============================================================

import { sleep, check, group } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import {
  API_TYPE,
  TEST_TYPE,
  BASE_URL,
  STAGES,
  THRESHOLDS_WRITE,
  thinkTime,
} from "./config.js";
import { loginREST, loginTRPC, pickUser } from "./auth.js";
import {
  restGet,
  restPost,
  trpcQuery,
  trpcMutation,
  checkAndRecord,
  parseResponse,
} from "./http.js";

// =============================================================
// BASE URL
// =============================================================

const BASE = BASE_URL[API_TYPE];

// =============================================================
// CUSTOM METRICS
// =============================================================

const loginFuncErr = new Rate("s04_login_func_err_rate");
const loginSlaBreach = new Rate("s04_login_sla_breach_rate");
const loginTrend = new Trend("s04_login_duration", true);
const loginCounter = new Counter("s04_login_errors");

const meFuncErr = new Rate("s04_me_func_err_rate");
const meSlaBreach = new Rate("s04_me_sla_breach_rate");
const meTrend = new Trend("s04_me_duration", true);

const refreshFuncErr = new Rate("s04_refresh_func_err_rate");
const refreshSlaBreach = new Rate("s04_refresh_sla_breach_rate");
const refreshTrend = new Trend("s04_refresh_duration", true);
const refreshCounter = new Counter("s04_refresh_errors");

const logoutFuncErr = new Rate("s04_logout_func_err_rate");
const logoutTrend = new Trend("s04_logout_duration", true);

// Negative path: invalid login harus selalu return 4xx (rate ≈ 1.0 = benar)
const invalidLoginCorrect = new Rate("s04_invalid_login_correct_rate");

// =============================================================
// OPTIONS
// =============================================================

const stageKey = `${TEST_TYPE}_auth`;
const stages = STAGES[stageKey] || STAGES["load_auth"];

export const options = {
  scenarios: {
    auth_flow: {
      executor: "ramping-vus",
      startVUs: 0,
      stages,
      gracefulRampDown: "30s",
    },
  },
  thresholds: {
    ...THRESHOLDS_WRITE,
    s04_login_duration: ["p(95)<1500", "p(99)<3000"],
    s04_me_duration: ["p(95)<500", "p(99)<1000"],
    s04_refresh_duration: ["p(95)<1000", "p(99)<2000"],
    // Error rate
    s04_login_func_err_rate: ["rate<0.02"],
    s04_refresh_func_err_rate: ["rate<0.02"],
    s04_invalid_login_correct_rate: ["rate>0.98"], 
  },
  summaryTrendStats: ["avg", "p(50)", "p(90)", "p(95)", "p(99)", "max"],
};

// =============================================================
// SETUP — verifikasi server up sebelum mulai VU
// =============================================================

export function setup() {
  const firstUser = pickUser(0);
  let up = false;

  if (API_TYPE === "rest") {
    up = loginREST(BASE, firstUser.email, firstUser.password);
    if (up) restPost(`${BASE}/auth/logout`, {});
  } else {
    up = loginTRPC(BASE, firstUser.email, firstUser.password);
    if (up) trpcMutation(BASE, "auth.logout", {});
  }

  if (!up) {
    console.error(
      `[setup] Server ${API_TYPE.toUpperCase()} tidak merespons login. Abort.`,
    );
  } else {
    console.log(
      `[setup] Server ${API_TYPE.toUpperCase()} @ ${BASE} siap. S-04 Auth dimulai.`,
    );
    console.log(
      `[setup] NOTE: Register tidak dijalankan di load loop untuk menjaga repeatability DB.`,
    );
  }

  return { apiType: API_TYPE };
}

// =============================================================
// DEFAULT FUNCTION — dijalankan tiap VU per iterasi
//
// Flow per iterasi: login → me → refresh → (30% negative path) → logout
// =============================================================

export default function () {
  const user = pickUser(__VU);
  const tag = { scenario: "s04_auth", api: API_TYPE };

  // ── 1. Login ─────────────────────────────────────────────
  group("Login", function () {
    let loginRes;

    if (API_TYPE === "rest") {
      loginRes = restPost(
        `${BASE}/auth/login`,
        { email: user.email, password: user.password },
        tag,
      );
    } else {
      loginRes = trpcMutation(
        BASE,
        "auth.login",
        { email: user.email, password: user.password },
        tag,
      );
    }

    checkAndRecord(
      loginRes,
      "s04_login",
      loginTrend,
      loginFuncErr,
      loginSlaBreach,
      loginCounter,
      1500,
    );

    sleep(thinkTime(0.5, 0.2));
  });

  // ── 2. /auth/me ──────────────────────────────────────────
  group("Get Me", function () {
    let meRes;

    if (API_TYPE === "rest") {
      meRes = restGet(`${BASE}/auth/me`, null, tag);
    } else {
      meRes = trpcQuery(BASE, "auth.me", {}, tag);
    }

    checkAndRecord(meRes, "s04_me", meTrend, meFuncErr, meSlaBreach, null, 500);

    // Verify shape — user harus punya id + email + role
    const data = parseResponse(API_TYPE, meRes);
    check(data, {
      "s04_me: id ada": (d) => d?.id != null,
      "s04_me: email ada": (d) => d?.email != null,
      "s04_me: role ada": (d) => d?.role != null,
    });

    sleep(thinkTime(0.3, 0.1));
  });

  // ── 3. Refresh Token ─────────────────────────────────────
  group("Refresh Token", function () {
    let refreshRes;

    if (API_TYPE === "rest") {
      refreshRes = restPost(`${BASE}/auth/refresh`, {}, tag);
    } else {
      refreshRes = trpcMutation(BASE, "auth.refresh", {}, tag);
    }

    checkAndRecord(
      refreshRes,
      "s04_refresh",
      refreshTrend,
      refreshFuncErr,
      refreshSlaBreach,
      refreshCounter,
      1000,
    );

    sleep(thinkTime(0.5, 0.2));
  });

  // ── 4. Negative Path — invalid credentials (30% VU) ──────
  // Server HARUS selalu return 4xx untuk credential salah.
  // Kalau 2xx berarti auth endpoint rusak serius.
  if (Math.random() < 0.3) {
    group("Invalid Login (Negative Path)", function () {
      let badRes;

      if (API_TYPE === "rest") {
        badRes = restPost(
          `${BASE}/auth/login`,
          { email: user.email, password: "WrongPassword_NotReal_k6!" },
          tag,
        );
      } else {
        badRes = trpcMutation(
          BASE,
          "auth.login",
          { email: user.email, password: "WrongPassword_NotReal_k6!" },
          tag,
        );
      }

      const correctlyRejected = check(badRes, {
        "s04_invalid_login: return 4xx": (r) =>
          r.status >= 400 && r.status < 500,
      });
      invalidLoginCorrect.add(correctlyRejected ? 1 : 0);

      sleep(thinkTime(0.2, 0.1));
    });
  }

  // ── 5. Logout ─────────────────────────────────────────────
  group("Logout", function () {
    let logoutRes;

    if (API_TYPE === "rest") {
      logoutRes = restPost(`${BASE}/auth/logout`, {}, tag);
    } else {
      logoutRes = trpcMutation(BASE, "auth.logout", {}, tag);
    }

    checkAndRecord(
      logoutRes,
      "s04_logout",
      logoutTrend,
      logoutFuncErr,
      null,
      null,
      1000,
    );

    sleep(thinkTime(1.0, 0.3));
  });
}

// =============================================================
// TEARDOWN
// =============================================================
export function teardown(data) {
  console.log(
    `[teardown] S-04 Auth selesai. API: ${data.apiType}.\n` +
      `Metrik penting: s04_login_func_err_rate, s04_refresh_func_err_rate,\n` +
      `s04_login_duration (p95/p99), s04_invalid_login_correct_rate.\n` +
      `NOTE: Tidak ada user baru dibuat selama load test ini — DB bersih.`,
  );
}

// =============================================================
// HANDLE SUMMARY
// =============================================================

export function handleSummary(data) {
  const ts = __ENV.TS || Date.now();
  const filename = `results/s04_auth_${API_TYPE}_${TEST_TYPE}_${ts}.json`;
  const m = data.metrics;
  const fmt = (v) => (v != null ? v.toFixed(1) : "N/A");
  const pct = (v) => (v != null ? (v * 100).toFixed(2) : "N/A");

  const summary = `
${"=".repeat(62)}
  S-04 AUTH FLOW | ${API_TYPE.toUpperCase()} | ${TEST_TYPE.toUpperCase()}
${"=".repeat(62)}
  Response Time (ms) — aggregate semua endpoint
    Avg:    ${fmt(m.http_req_duration?.values?.avg)}    P95: ${fmt(m.http_req_duration?.values?.["p(95)"])}
    Median: ${fmt(m.http_req_duration?.values?.["p(50)"])}    P99: ${fmt(m.http_req_duration?.values?.["p(99)"])}
  Throughput:  ${fmt(m.http_reqs?.values?.rate)} req/s
  Iterations:  ${m.iterations?.values?.count || 0}

  Per-Endpoint P95 (ms)
    Login:    ${fmt(m.s04_login_duration?.values?.["p(95)"])}
    Me:       ${fmt(m.s04_me_duration?.values?.["p(95)"])}
    Refresh:  ${fmt(m.s04_refresh_duration?.values?.["p(95)"])}
    Logout:   ${fmt(m.s04_logout_duration?.values?.["p(95)"])}

  Per-Endpoint P99 (ms)
    Login:    ${fmt(m.s04_login_duration?.values?.["p(99)"])}
    Me:       ${fmt(m.s04_me_duration?.values?.["p(99)"])}
    Refresh:  ${fmt(m.s04_refresh_duration?.values?.["p(99)"])}

  Error Rates
    Login func error:    ${pct(m.s04_login_func_err_rate?.values?.rate)}%
    Refresh func error:  ${pct(m.s04_refresh_func_err_rate?.values?.rate)}%
    Invalid login rejected correctly: ${pct(m.s04_invalid_login_correct_rate?.values?.rate)}%

  Thresholds
    Login SLA breach:    ${pct(m.s04_login_sla_breach_rate?.values?.rate)}%
    Refresh SLA breach:  ${pct(m.s04_refresh_sla_breach_rate?.values?.rate)}%
${"=".repeat(62)}\n`;

  return { [filename]: JSON.stringify(data, null, 2), stdout: summary };
}