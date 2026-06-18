// =============================================================
// s02_shopping.js — S-02: Shopping Flow
// Scenario: Authenticated user browses and manages cart
// Operations: login, browse products, add/update/remove cart items
//
// FIX (round 1):
//  - Pisah functionalErrorRate dari slaBreachRate (tidak boleh dicampur)
//  - Fail-fast jika PRODUCT_IDS_FOR_CART < 3 (bukan silent skip)
//  - Unique user per VU: __VU % TEST_USERS.length
//
// FIX (round 2 — threshold & metric):
//  - latency_login: metric dideklarasi DAN diisi
//  - checkAndRecord SLA diselaraskan: cart_add 1000ms, cart_remove 800ms
//  - http_req_duration p(99) override ke 3000ms
//
// FIX (round 3 — VU session caching):
//  - vuLoggedIn flag per VU — login SEKALI di iterasi pertama
//  - latency_login diukur hanya di iterasi pertama
//
// FIX (round 4 — cookie persistence):
//  - Root cause round 3 gagal: k6 cookie jar tidak reliable lintas iterasi
//    karena cookie mungkin di-treat sebagai session cookie (tanpa Max-Age).
//  - Fix: setelah login, extract Set-Cookie dari response secara explicit
//    dan inject ke JSON_HEADERS['Cookie'] (module-level, persist per VU).
//  - JSON_HEADERS adalah object reference yang sama di seluruh module dalam
//    satu VU isolate — mutation di sini otomatis berlaku untuk semua helpers
//    di http.js yang pakai JSON_HEADERS.
//  - Pakai loginAndCapture() (ditambah di auth.js) sebagai ganti login().
//
// Run:
//   k6 run --insecure-skip-tls-verify --env API=rest --env TEST_TYPE=load \
//          --env REST_URL=https://<VPS_IP>/api/v1 --env TS=$(date +%s) s02_shopping.js
// =============================================================

import http from "k6/http";
import { check, sleep, group } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

import {
  API_TYPE,
  TEST_TYPE,
  BASE,
  HEALTH_URL,
  STAGES,
  THRESHOLDS_WRITE,
  JSON_HEADERS,
  thinkTime,
  MAX_VU,
} from "./config.js";
import { loginAndCapture, pickUser, TEST_USERS } from "./auth.js";
import {
  trpcQuery,
  trpcMutation,
  trpcBatchQuery,
  restGet,
  restPost,
  restPatch,
  restDelete,
  parseResponse,
  checkAndRecord,
} from "./http.js";
import {
  randomItem,
  randomInt,
  PRODUCT_SLUGS,
  PRODUCT_IDS_FOR_CART,
} from "./seed.js";

// Custom metrics
// =============================================================
// C4 FLAG — dibaca sekali saat VU init, tidak berubah selama test.
// true  → __ENV.BATCH=true  → pakai trpcBatchQuery (C4, tRPC only)
// false → default           → kode path lama tidak berubah sama sekali (C2)
// =============================================================
const USE_BATCH = __ENV.BATCH === "true";

const functionalErrorRate = new Rate("functional_error_rate"); // 4xx/5xx
const slaBreachRate = new Rate("sla_breach_rate");             // 2xx tapi lambat
const errorCounter = new Counter("request_errors");
const latencyLogin = new Trend("latency_login", true);         // cold start per VU
const latencyBrowse = new Trend("latency_browse", true);
const latencyCartGet = new Trend("latency_cart_get", true);
const latencyCartAdd = new Trend("latency_cart_add", true);
const latencyCartUpdate = new Trend("latency_cart_update", true);
const latencyCartRemove = new Trend("latency_cart_remove", true);
const payloadBytes = new Trend("payload_size_bytes", true);

export const options = {
  stages: STAGES[TEST_TYPE] || STAGES.load,
  thresholds: {
    ...THRESHOLDS_WRITE,
    http_req_duration: ["p(50)<500", "p(95)<1000", "p(99)<3000"],
    http_req_failed: ["rate<0.01"],

    functional_error_rate: ["rate<0.01"],
    sla_breach_rate: ["rate<0.05"],

    // Read ops
    latency_browse: ["p(95)<600"],
    latency_cart_get: ["p(95)<600"],

    // Write ops
    latency_login: ["p(95)<3000"],   // cold start cost, bukan per-iteration
    latency_cart_add: ["p(95)<1000"],
    latency_cart_update: ["p(95)<800"],
    latency_cart_remove: ["p(95)<800"],
  },
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

// =============================================================
// VU-LEVEL SESSION STATE
//
// vuLoggedIn: true setelah login sukses di iterasi pertama.
// JSON_HEADERS di-mutate setelah login untuk inject Cookie header
// secara explicit — ini yang buat auth persist tanpa bergantung
// pada k6 cookie jar (yang ternyata tidak reliable lintas iterasi
// untuk session cookies tanpa Max-Age).
// =============================================================

let vuLoggedIn = false;

// =============================================================
// REQUEST HELPERS
// =============================================================

function getCart() {
  const tag = { endpoint: "cart_get", api: API_TYPE, scenario: "s02" };
  let res;
  if (API_TYPE === "rest") {
    res = http.get(`${BASE}/cart`, { headers: JSON_HEADERS, tags: tag });
  } else {
    res = trpcQuery(BASE, "cart.get", null, tag);
  }
  latencyCartGet.add(res.timings.duration);
  checkAndRecord(res, "cart_get", null, functionalErrorRate, slaBreachRate, errorCounter, 600);
  return parseResponse(API_TYPE, res);
}

function addToCart(productId, quantity) {
  const tag = { endpoint: "cart_add", api: API_TYPE, scenario: "s02" };
  let res;
  if (API_TYPE === "rest") {
    res = restPost(`${BASE}/cart`, { productId, quantity }, tag);
  } else {
    res = trpcMutation(BASE, "cart.addItem", { productId, quantity }, tag);
  }
  latencyCartAdd.add(res.timings.duration);
  checkAndRecord(res, "cart_add", null, functionalErrorRate, slaBreachRate, errorCounter, 1000);
  return parseResponse(API_TYPE, res);
}

function updateCartItem(itemId, quantity) {
  const tag = { endpoint: "cart_update", api: API_TYPE, scenario: "s02" };
  let res;
  if (API_TYPE === "rest") {
    res = restPatch(`${BASE}/cart/${itemId}`, { quantity }, tag);
  } else {
    res = trpcMutation(BASE, "cart.updateItem", { itemId, quantity }, tag);
  }
  latencyCartUpdate.add(res.timings.duration);
  checkAndRecord(res, "cart_update", null, functionalErrorRate, slaBreachRate, errorCounter, 800);
  return res;
}

function removeCartItem(itemId) {
  const tag = { endpoint: "cart_remove", api: API_TYPE, scenario: "s02" };
  let res;
  if (API_TYPE === "rest") {
    res = restDelete(`${BASE}/cart/${itemId}`, tag);
  } else {
    res = trpcMutation(BASE, "cart.removeItem", { itemId }, tag);
  }
  latencyCartRemove.add(res.timings.duration);
  checkAndRecord(res, "cart_remove", null, functionalErrorRate, slaBreachRate, errorCounter, 800);
  return res;
}

function clearCart() {
  const tag = { endpoint: "cart_clear", api: API_TYPE, scenario: "s02" };
  let res;
  if (API_TYPE === "rest") {
    res = restDelete(`${BASE}/cart`, tag);
  } else {
    res = trpcMutation(BASE, "cart.clear", {}, tag);
  }
  checkAndRecord(res, "cart_clear", null, functionalErrorRate, slaBreachRate, errorCounter, 600);
  return res;
}

function browseProducts() {
  const tag = { endpoint: "browse", api: API_TYPE, scenario: "s02" };
  let res;
  if (API_TYPE === "rest") {
    res = restGet(`${BASE}/products`, { page: randomInt(1, 5), limit: 12 }, tag);
  } else {
    res = trpcQuery(BASE, "product.getAll", { page: randomInt(1, 5), limit: 12 }, tag);
  }
  latencyBrowse.add(res.timings.duration);
  payloadBytes.add(res.body ? res.body.length : 0);
  checkAndRecord(res, "browse", null, functionalErrorRate, slaBreachRate, errorCounter, 600);
  return res;
}

function getProductDetail(slug) {
  const tag = { endpoint: "product_detail", api: API_TYPE, scenario: "s02" };
  let res;
  if (API_TYPE === "rest") {
    res = http.get(`${BASE}/products/${slug}`, { headers: JSON_HEADERS, tags: tag });
  } else {
    res = trpcQuery(BASE, "product.getBySlug", { slug }, tag);
  }
  checkAndRecord(res, "product_detail", null, functionalErrorRate, slaBreachRate, errorCounter, 600);
  return res;
}

// =============================================================
// VU FUNCTION
// =============================================================

export default function () {
  const { email, password } = pickUser(__VU);

  // Step 1: Login — HANYA di iterasi pertama per VU.
  //
  // FIX round 4: loginAndCapture() mengganti login(). Selain mengecek
  // status 200, ia juga extract Set-Cookie dari response dan inject ke
  // JSON_HEADERS['Cookie'] secara explicit. Ini jamin cookie dikirim di
  // semua iterasi berikutnya tanpa bergantung pada k6 cookie jar.
  if (!vuLoggedIn) {
    const loginStart = Date.now();
    const loggedIn = loginAndCapture(API_TYPE, BASE, email, password);
    latencyLogin.add(Date.now() - loginStart);

    if (!loggedIn) {
      sleep(2);
      return;
    }
    vuLoggedIn = true;
  }

  sleep(thinkTime(1, 0.3));

  // Step 2: Browse products
  //
  // C4 (USE_BATCH=true, API=trpc): batch 3 GET queries → 1 HTTP request
  //   product.getAll + product.getBySlug + product.getBySlug
  //   Tujuan: ukur HTTP request count reduction (H1h) dan latency batch.
  //
  // C2 (USE_BATCH=false): kode path lama, tidak ada perubahan satu baris pun.
  group("browse", () => {
    if (USE_BATCH && API_TYPE === "trpc" && PRODUCT_SLUGS.length > 0) {
      // ── C4 path — 3 queries → 1 HTTP request ──────────────
      const slug1 = randomItem(PRODUCT_SLUGS);
      const slug2 = randomItem(PRODUCT_SLUGS);
      const batchTag = { endpoint: "browse_batch", api: API_TYPE, scenario: "s02" };

      const batchRes = trpcBatchQuery(BASE, [
        { procedure: "product.getAll",    input: { page: randomInt(1, 5), limit: 12 } },
        { procedure: "product.getBySlug", input: { slug: slug1 } },
        { procedure: "product.getBySlug", input: { slug: slug2 } },
      ], batchTag);

      latencyBrowse.add(batchRes.timings.duration);
      payloadBytes.add(batchRes.body ? batchRes.body.length : 0);
      checkAndRecord(batchRes, "browse_batch", null, functionalErrorRate, slaBreachRate, errorCounter, 600);

      // Validasi response batch — log di iterasi pertama per VU saja
      if (__ITER === 0) {
        if (batchRes.status === 200) {
          try {
            const arr = JSON.parse(batchRes.body);
            if (Array.isArray(arr) && arr.length === 3) {
              console.log(`[C4][VU${__VU}] ✓ Batch OK — ${arr.length} results dalam 1 request (${batchRes.timings.duration.toFixed(0)}ms)`);
            } else {
              console.warn(`[C4][VU${__VU}] ⚠️  Batch response bukan array[3]: ${batchRes.body.substring(0, 120)}`);
            }
          } catch (e) {
            console.error(`[C4][VU${__VU}] ✗ Batch parse error: ${e.message} | body: ${batchRes.body.substring(0, 120)}`);
          }
        } else {
          console.error(`[C4][VU${__VU}] ✗ Batch request gagal — status=${batchRes.status} | body: ${batchRes.body.substring(0, 200)}`);
        }
      }

      sleep(thinkTime(1.5, 0.3));
    } else {
      // ── C2 path — original, tidak berubah ─────────────────
      browseProducts();
      sleep(thinkTime(1.5, 0.3));

      if (PRODUCT_SLUGS.length > 0) {
        getProductDetail(randomItem(PRODUCT_SLUGS));
        sleep(thinkTime(1.5, 0.3));
        getProductDetail(randomItem(PRODUCT_SLUGS));
        sleep(thinkTime(1.5, 0.3));
      }
    }
  });

  // Step 3: Clear previous cart
  clearCart();
  sleep(thinkTime(0.5, 0.1));

  // Step 4: Cart operations
  const addedItemIds = [];
  group("cart_operations", () => {
    const base = __VU % Math.max(PRODUCT_IDS_FOR_CART.length, 1);
    const productsToAdd =
      PRODUCT_IDS_FOR_CART.length >= 3
        ? [
            PRODUCT_IDS_FOR_CART[base % PRODUCT_IDS_FOR_CART.length],
            PRODUCT_IDS_FOR_CART[(base + 1) % PRODUCT_IDS_FOR_CART.length],
            PRODUCT_IDS_FOR_CART[(base + 2) % PRODUCT_IDS_FOR_CART.length],
          ]
        : [];

    for (const productId of productsToAdd) {
      const cartData = addToCart(productId, randomInt(1, 3));
      sleep(thinkTime(1, 0.2));

      if (cartData?.items) {
        cartData.items.forEach((item) => {
          if (item.productId === productId) addedItemIds.push(item.id);
        });
      }
    }

    getCart();
    sleep(thinkTime(1.5, 0.3));

    if (addedItemIds.length > 0) {
      updateCartItem(addedItemIds[0], randomInt(1, 5));
      sleep(thinkTime(1, 0.2));
    }

    if (addedItemIds.length > 1) {
      removeCartItem(addedItemIds[addedItemIds.length - 1]);
      sleep(thinkTime(1, 0.2));
    }

    getCart();
    sleep(thinkTime(1, 0.2));
  });
}

// =============================================================
// SETUP
// =============================================================

export function setup() {
  console.log(`\n${"=".repeat(60)}`);
  console.log(
    `S-02 Shopping Flow | API: ${API_TYPE.toUpperCase()} | Test: ${TEST_TYPE.toUpperCase()}`,
  );
  console.log(`${"=".repeat(60)}\n`);

  const res = http.get(HEALTH_URL);
  if (res.status !== 200) throw new Error(`Health check failed: ${res.status}`);

  if (PRODUCT_SLUGS.length === 0) {
    throw new Error(
      "[SETUP FAIL] PRODUCT_SLUGS kosong. Isi seed.js dulu sebelum run.\n" +
        "Query: SELECT slug FROM products WHERE is_active=true AND stock>100 ORDER BY random() LIMIT 100;",
    );
  }

  if (PRODUCT_IDS_FOR_CART.length < 3) {
    throw new Error(
      `[SETUP FAIL] PRODUCT_IDS_FOR_CART hanya ${PRODUCT_IDS_FOR_CART.length} item, minimal 3 dibutuhkan.\n` +
        "Query: SELECT id FROM products WHERE is_active=true AND stock>500 ORDER BY random() LIMIT 30;",
    );
  }

  const requiredUsers = MAX_VU[TEST_TYPE] || MAX_VU.load;
  if (TEST_USERS.length < requiredUsers) {
    throw new Error(
      `[SETUP FAIL] TEST_USERS hanya ${TEST_USERS.length} user, butuh minimal ${requiredUsers} ` +
        `(= max VU untuk TEST_TYPE=${TEST_TYPE}).\n` +
        "Untuk shopping flow, tiap VU WAJIB punya user unik supaya tidak ada cart contention.\n" +
        "Query: SELECT email FROM users WHERE role='USER' ORDER BY created_at LIMIT 500;\n" +
        "Paste hasilnya ke TEST_USERS di auth.js",
    );
  }

  console.log(`✓ PRODUCT_SLUGS: ${PRODUCT_SLUGS.length} slugs`);
  console.log(`✓ PRODUCT_IDS_FOR_CART: ${PRODUCT_IDS_FOR_CART.length} products`);
  console.log(`✓ TEST_USERS: ${TEST_USERS.length} users`);
  console.log(`✓ Session caching: ON (loginAndCapture — cookie explicit injection)`);

  // C4 batch mode indicator
  if (USE_BATCH && API_TYPE === "trpc") {
    console.log("━".repeat(60));
    console.log("  MODE: C4 — tRPC BATCH ENABLED (--env BATCH=true)");
    console.log("  Browse phase: 3 GET queries → 1 HTTP request");
    console.log("  URL format: /trpc/proc1,proc2,proc3?batch=1&input={...}");
    console.log("  Bandingkan http_reqs.count dengan C2 untuk H1h.");
    console.log("━".repeat(60));
  } else if (USE_BATCH && API_TYPE !== "trpc") {
    console.warn("⚠️  BATCH=true tapi API bukan trpc — batch diabaikan, jalan sebagai C2.");
  } else {
    console.log(`✓ Mode: C2 — standard (no batch)`);
  }

  return {
    apiType: API_TYPE,
    testType: TEST_TYPE,
    startTime: new Date().toISOString(),
  };
}

// =============================================================
// HANDLE SUMMARY
// =============================================================

export function handleSummary(data) {
  const ts = __ENV.TS || Date.now();
  const condition = USE_BATCH && API_TYPE === "trpc" ? "C4" : "C2";
  const filename = `results/s02_shopping_${API_TYPE}_${TEST_TYPE}_${ts}.json`;
  const m = data.metrics;
  const fmt = (v) => (v != null ? v.toFixed(1) : "N/A");
  const pct = (v) => (v != null ? (v * 100).toFixed(2) : "N/A");

  const summary = `
${"=".repeat(62)}
  S-02 SHOPPING FLOW | ${API_TYPE.toUpperCase()} | ${TEST_TYPE.toUpperCase()} | ${condition}
${"=".repeat(62)}
  Response Time (ms)
    Avg:    ${fmt(m.http_req_duration?.values?.avg)}    P95: ${fmt(m.http_req_duration?.values?.["p(95)"])}
    Median: ${fmt(m.http_req_duration?.values?.med)}    P99: ${fmt(m.http_req_duration?.values?.["p(99)"])}
  Throughput:  ${fmt(m.http_reqs?.values?.rate)} req/s
  Total Reqs:  ${m.http_reqs?.values?.count || 0}${condition === "C4" ? " ← HTTP request count (C4 batch)" : ""}

  Error Rates (PISAH — jangan campur saat analisis)
    Functional Error (4xx/5xx): ${pct(m.functional_error_rate?.values?.rate)}%
    SLA Breach (2xx tapi lambat): ${pct(m.sla_breach_rate?.values?.rate)}%

  Per-Endpoint P95 (ms)
    Login [cold start]:  ${fmt(m.latency_login?.values?.["p(95)"])}
    Browse:              ${fmt(m.latency_browse?.values?.["p(95)"])}${condition === "C4" ? " (batch 3 query)" : ""}
    Cart Get:            ${fmt(m.latency_cart_get?.values?.["p(95)"])}
    Cart Add:            ${fmt(m.latency_cart_add?.values?.["p(95)"])}
    Cart Update:         ${fmt(m.latency_cart_update?.values?.["p(95)"])}
    Cart Remove:         ${fmt(m.latency_cart_remove?.values?.["p(95)"])}

  Note: latency_login = cold start cost (1x per VU). Session via explicit Cookie header.
${"=".repeat(62)}\n`;

  return { [filename]: JSON.stringify(data, null, 2), stdout: summary };
}