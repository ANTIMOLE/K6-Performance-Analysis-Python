// =============================================================
// s05_admin.js — S-05: Admin Dashboard Flow
// FIX:
//  - Pisah functionalErrorRate dari slaBreachRate
//  - Fail-fast di setup jika CATEGORY_IDS kosong
//  - Admin product create hanya dilakukan kalau CATEGORY_IDS tersedia
//    dan order update hanya kalau ada pending order — kedua kondisi ini
//    sekarang dicatat ke counter terpisah supaya bisa dihitung saat analisis
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
  JSON_HEADERS,
  thinkTime,
} from "./config.js";
import { login, pickAdmin } from "./auth.js";
import {
  trpcQuery,
  trpcMutation,
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
  CATEGORY_IDS,
  ADMIN_TEST_PRODUCT,
} from "./seed.js";

// Custom metrics — FIX: pisah functional error dari SLA breach
const functionalErrorRate = new Rate("functional_error_rate");
const slaBreachRate = new Rate("sla_breach_rate");
const errorCounter = new Counter("request_errors");
// Counter untuk operasi yang di-skip (bukan error, tapi perlu dicatat)
const productCreateSkip = new Counter("admin_product_create_skip"); // skip karena no category
const orderUpdateSkip = new Counter("admin_order_update_skip"); // skip karena no pending order
const latencyDashboard = new Trend("latency_dashboard", true);
const latencyProductsList = new Trend("latency_admin_products", true);
const latencyOrdersList = new Trend("latency_admin_orders", true);
const latencyUsersList = new Trend("latency_admin_users", true);
const latencyProductCreate = new Trend("latency_product_create", true);
const latencyProductUpdate = new Trend("latency_product_update", true);
const payloadBytes = new Trend("payload_size_bytes", true);

const ADMIN_STAGES = {
  load: STAGES.load_admin,
  stress: STAGES.stress_admin,
  soak:   STAGES.soak_admin, 
  spike: STAGES.spike_admin,
};

export const options = {
  stages: ADMIN_STAGES[TEST_TYPE] || STAGES.load_admin,
  thresholds: {
    http_req_failed: ["rate<0.01"],
    functional_error_rate: ["rate<0.01"],
    sla_breach_rate: ["rate<0.05"],
    http_req_duration: ["p(95)<2000", "p(99)<5000"],
    latency_dashboard: ["p(95)<3000"],
    latency_admin_products: ["p(95)<1000"],
    latency_admin_orders: ["p(95)<1000"],
    latency_admin_users: ["p(95)<800"],
    latency_product_create: ["p(95)<1000"],
    latency_product_update: ["p(95)<800"],
  },
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

// =============================================================
// REQUEST HELPERS
// =============================================================

function getDashboard() {
  const tag = { endpoint: "admin_dashboard", api: API_TYPE, scenario: "s05" };
  let res;
  if (API_TYPE === "rest") {
    res = http.get(`${BASE}/admin/dashboard`, {
      headers: JSON_HEADERS,
      tags: tag,
    });
  } else {
    res = trpcQuery(BASE, "admin.getDashboard", null, tag);
  }
  latencyDashboard.add(res.timings.duration);
  payloadBytes.add(res.body ? res.body.length : 0);
  checkAndRecord(
    res,
    "admin_dashboard",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    3000,
  );
  return res;
}

function getAdminProducts(page, q) {
  const tag = { endpoint: "admin_products", api: API_TYPE, scenario: "s05" };
  const params = { page: page || 1, limit: 20, q: q || undefined };
  let res;
  if (API_TYPE === "rest") {
    res = restGet(`${BASE}/admin/products`, params, tag);
  } else {
    res = trpcQuery(BASE, "admin.getProducts", params, tag);
  }
  latencyProductsList.add(res.timings.duration);
  checkAndRecord(
    res,
    "admin_products",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    1000,
  );
  return parseResponse(API_TYPE, res);
}

function createProduct(productData) {
  const tag = {
    endpoint: "admin_product_create",
    api: API_TYPE,
    scenario: "s05",
  };
  let res;
  if (API_TYPE === "rest") {
    res = restPost(`${BASE}/admin/products`, productData, tag);
  } else {
    res = trpcMutation(BASE, "admin.createProduct", productData, tag);
  }
  latencyProductCreate.add(res.timings.duration);
  checkAndRecord(
    res,
    "admin_product_create",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    1000,
  );
  return parseResponse(API_TYPE, res);
}

function updateProduct(id, data) {
  const tag = {
    endpoint: "admin_product_update",
    api: API_TYPE,
    scenario: "s05",
  };
  let res;
  if (API_TYPE === "rest") {
    res = restPatch(`${BASE}/admin/products/${id}`, data, tag);
  } else {
    res = trpcMutation(BASE, "admin.updateProduct", { id, ...data }, tag);
  }
  latencyProductUpdate.add(res.timings.duration);
  checkAndRecord(
    res,
    "admin_product_update",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    800,
  );
  return res;
}

function deleteProduct(id) {
  const tag = {
    endpoint: "admin_product_delete",
    api: API_TYPE,
    scenario: "s05",
  };
  let res;
  if (API_TYPE === "rest") {
    res = restDelete(`${BASE}/admin/products/${id}`, tag);
  } else {
    res = trpcMutation(BASE, "admin.deleteProduct", { id }, tag);
  }
  checkAndRecord(
    res,
    "admin_product_delete",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    800,
  );
  return res;
}

function getAdminOrders(page, status) {
  const tag = { endpoint: "admin_orders", api: API_TYPE, scenario: "s05" };
  const params = { page: page || 1, limit: 20, status: status || undefined };
  let res;
  if (API_TYPE === "rest") {
    res = restGet(`${BASE}/admin/orders`, params, tag);
  } else {
    res = trpcQuery(BASE, "admin.getOrders", params, tag);
  }
  latencyOrdersList.add(res.timings.duration);
  checkAndRecord(
    res,
    "admin_orders",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    1000,
  );
  return parseResponse(API_TYPE, res);
}

function getAdminUsers(page) {
  const tag = { endpoint: "admin_users", api: API_TYPE, scenario: "s05" };
  const params = { page: page || 1, limit: 20 };
  let res;
  if (API_TYPE === "rest") {
    res = restGet(`${BASE}/admin/users`, params, tag);
  } else {
    res = trpcQuery(BASE, "admin.getUsers", params, tag);
  }
  latencyUsersList.add(res.timings.duration);
  checkAndRecord(
    res,
    "admin_users",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    800,
  );
  return res;
}

// =============================================================
// VU FUNCTION
// =============================================================

export default function () {
  const { email, password } = pickAdmin(__VU);

  const loggedIn = login(API_TYPE, BASE, email, password);
  if (!loggedIn) {
    sleep(2);
    return;
  }
  sleep(thinkTime(2, 0.5));

  const rand = Math.random() * 100;

  if (rand < 40) {
    group("admin_dashboard", () => {
      getDashboard();
      sleep(thinkTime(2, 0.5));
    });
  } else if (rand < 70) {
    group("admin_products", () => {
      getAdminProducts(1, undefined);
      sleep(thinkTime(2, 0.5));

      if (CATEGORY_IDS.length > 0) {
        const newProduct = createProduct({
          ...ADMIN_TEST_PRODUCT,
          name: `k6 Test Product ${__VU}_${Date.now()}`,
          categoryId: CATEGORY_IDS[__VU % CATEGORY_IDS.length],
        });
        sleep(thinkTime(1.5, 0.3));

        const productId = newProduct?.id || newProduct?.data?.id;
        if (productId) {
          updateProduct(productId, { stock: randomInt(100, 500) });
          sleep(thinkTime(1.5, 0.3));
          deleteProduct(productId);
          sleep(thinkTime(1, 0.2));
        }
      } else {
        // FIX: catat skip bukan diam-diam lewati
        productCreateSkip.add(1);
        console.warn(
          `[SKIP] VU ${__VU}: product create skip karena CATEGORY_IDS kosong`,
        );
      }
    });
  } else if (rand < 90) {
    group("admin_orders", () => {
      const statusOptions = [
        undefined,
        "pending_payment",
        "confirmed",
        "processing",
        "shipped",
      ];
      const status = statusOptions[randomInt(0, statusOptions.length - 1)];
      const ordersData = getAdminOrders(1, status);
      sleep(thinkTime(2, 0.5));

      const orders = ordersData?.data || ordersData?.orders || [];
      const pendingOrder = orders.find((o) => o.status === "pending_payment");

      if (pendingOrder) {
        const tag = {
          endpoint: "admin_order_status",
          api: API_TYPE,
          scenario: "s05",
        };
        if (API_TYPE === "rest") {
          restPatch(
            `${BASE}/admin/orders/${pendingOrder.id}/status`,
            { status: "confirmed" },
            tag,
          );
        } else {
          trpcMutation(BASE, "admin.updateOrderStatus", { orderId: pendingOrder.id, status: "confirmed" }, tag);
        }
        sleep(thinkTime(1, 0.2));
      } else {
        // FIX: catat skip
        orderUpdateSkip.add(1);
      }
    });
  } else {
    group("admin_users", () => {
      getAdminUsers(randomInt(1, 5));
      sleep(thinkTime(2, 0.5));
    });
  }
}

// =============================================================
// SETUP — fail-fast
// =============================================================

export function setup() {
  console.log(`\n${"=".repeat(60)}`);
  console.log(
    `S-05 Admin Flow | API: ${API_TYPE.toUpperCase()} | Test: ${TEST_TYPE.toUpperCase()}`,
  );
  console.log(
    "Dashboard: 7 parallel queries + 1 groupBy — expect higher latency",
  );
  console.log(`${"=".repeat(60)}\n`);

  const res = http.get(HEALTH_URL);
  if (res.status !== 200) throw new Error(`Health check failed: ${res.status}`);

  if (CATEGORY_IDS.length === 0) {
    throw new Error(
      "[SETUP FAIL] CATEGORY_IDS kosong. Product create flow tidak bisa jalan.\n" +
        "Query: SELECT id FROM categories ORDER BY name;",
    );
  }

  console.log(`✓ CATEGORY_IDS: ${CATEGORY_IDS.length} categories tersedia`);
  console.log(
    "⚠️  Pastikan ada order dengan status 'pending_payment' di DB saat test jalan.",
  );
  console.log("   Kalau tidak ada, admin_order_update_skip counter akan naik.");

  return { apiType: API_TYPE, startTime: new Date().toISOString() };
}

export function handleSummary(data) {
  const ts = __ENV.TS || Date.now();
  const filename = `results/s05_admin_${API_TYPE}_${TEST_TYPE}_${ts}.json`;
  const m = data.metrics;
  const fmt = (v) => (v != null ? v.toFixed(1) : "N/A");
  const pct = (v) => (v != null ? (v * 100).toFixed(2) : "N/A");

  const summary = `
${"=".repeat(62)}
  S-05 ADMIN FLOW | ${API_TYPE.toUpperCase()} | ${TEST_TYPE.toUpperCase()}
${"=".repeat(62)}
  Response Time (ms)
    Avg:    ${fmt(m.http_req_duration?.values?.avg)}    P95: ${fmt(m.http_req_duration?.values?.["p(95)"])}
    Median: ${fmt(m.http_req_duration?.values?.med)}    P99: ${fmt(m.http_req_duration?.values?.["p(99)"])}
  Throughput:  ${fmt(m.http_reqs?.values?.rate)} req/s

  Error Rates (PISAH saat analisis)
    Functional Error (4xx/5xx): ${pct(m.functional_error_rate?.values?.rate)}%
    SLA Breach (2xx tapi lambat): ${pct(m.sla_breach_rate?.values?.rate)}%

  Skip Counters (operasi yang tidak dieksekusi — bukan error)
    Product Create Skip: ${m.admin_product_create_skip?.values?.count || 0}
    Order Update Skip:   ${m.admin_order_update_skip?.values?.count || 0}

  Per-Endpoint P95 (ms)
    Dashboard:      ${fmt(m.latency_dashboard?.values?.["p(95)"])} (7 parallel queries)
    Admin Products: ${fmt(m.latency_admin_products?.values?.["p(95)"])}
    Admin Orders:   ${fmt(m.latency_admin_orders?.values?.["p(95)"])}
    Admin Users:    ${fmt(m.latency_admin_users?.values?.["p(95)"])}
    Create Product: ${fmt(m.latency_product_create?.values?.["p(95)"])}
    Update Product: ${fmt(m.latency_product_update?.values?.["p(95)"])}
${"=".repeat(62)}\n`;

  return { [filename]: JSON.stringify(data, null, 2), stdout: summary };
}
