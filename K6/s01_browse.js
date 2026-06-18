// =============================================================
// s01_browse.js — S-01: Browse Flow
// Scenario: Guest/user browses product catalog (read-only)
// Operations: GET products, search, filter, detail, categories
//
// FIX:
//  - Pisah functionalErrorRate dari slaBreachRate
//  - Fail-fast di setup jika PRODUCT_SLUGS kosong
//
// Run:
//   k6 run --env API=rest --env TEST_TYPE=load   s01_browse.js
//   k6 run --env API=trpc --env TEST_TYPE=load   s01_browse.js
//   k6 run --env API=rest --env TEST_TYPE=stress s01_browse.js
//   k6 run --env API=rest --env TEST_TYPE=spike  s01_browse.js
//   k6 run --env API=rest --env TEST_TYPE=soak   s01_browse.js
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
  THRESHOLDS_READ,
  JSON_HEADERS,
  thinkTime,
} from "./config.js";
import { trpcQuery, restGet, parseResponse, checkAndRecord } from "./http.js";
import {
  randomItem,
  randomInt,
  SEARCH_KEYWORDS,
  SORT_OPTIONS,
  CATEGORY_IDS,
  PRODUCT_SLUGS,
} from "./seed.js";

// Custom metrics — FIX: pisah functional error dari SLA breach
const functionalErrorRate = new Rate("functional_error_rate");
const slaBreachRate = new Rate("sla_breach_rate");
const errorCounter = new Counter("request_errors");
const latencyList = new Trend("latency_product_list", true);
const latencyDetail = new Trend("latency_product_detail", true);
const latencySearch = new Trend("latency_product_search", true);
const latencyFilter = new Trend("latency_product_filter", true);
const payloadBytes = new Trend("payload_size_bytes", true);

const BROWSE_STAGES = {
  load: STAGES.load,
  stress: STAGES.stress,
  spike: STAGES.spike,
  soak: STAGES.soak,
};

export const options = {
  stages: BROWSE_STAGES[TEST_TYPE] || STAGES.load,
  thresholds: {
    ...THRESHOLDS_READ,
    functional_error_rate: ["rate<0.01"],
    sla_breach_rate: ["rate<0.05"],
    latency_product_list: ["p(95)<500"],
    latency_product_detail: ["p(95)<400"],
    latency_product_search: ["p(95)<1000"],
    latency_product_filter: ["p(95)<600"],
  },
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

// =============================================================
// REQUEST HELPERS
// =============================================================

function getProductList(page, limit) {
  const tag = { endpoint: "product_list", api: API_TYPE, scenario: "s01" };
  let res;
  if (API_TYPE === "rest") {
    res = restGet(`${BASE}/products`, { page, limit }, tag);
  } else {
    res = trpcQuery(BASE, "product.getAll", { page, limit }, tag);
  }
  latencyList.add(res.timings.duration);
  payloadBytes.add(res.body ? res.body.length : 0);
  checkAndRecord(
    res,
    "product_list",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    500,
  );
  return res;
}

function getProductDetail(slug) {
  const tag = { endpoint: "product_detail", api: API_TYPE, scenario: "s01" };
  let res;
  if (API_TYPE === "rest") {
    res = http.get(`${BASE}/products/${slug}`, {
      headers: JSON_HEADERS,
      tags: tag,
    });
  } else {
    res = trpcQuery(BASE, "product.getBySlug", { slug }, tag);
  }
  latencyDetail.add(res.timings.duration);
  payloadBytes.add(res.body ? res.body.length : 0);
  checkAndRecord(
    res,
    "product_detail",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    400,
  );
  return res;
}

function searchProducts(keyword) {
  const tag = { endpoint: "product_search", api: API_TYPE, scenario: "s01" };
  let res;
  if (API_TYPE === "rest") {
    res = restGet(`${BASE}/products/search`, { q: keyword, limit: 12 }, tag);
  } else {
    res = trpcQuery(BASE, "product.search", { q: keyword, limit: 12 }, tag);
  }
  latencySearch.add(res.timings.duration);
  payloadBytes.add(res.body ? res.body.length : 0);
  checkAndRecord(
    res,
    "product_search",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    1000,
  );
  return res;
}

function filterProducts(categoryId, sortBy, sortOrder, minPrice, maxPrice) {
  const tag = { endpoint: "product_filter", api: API_TYPE, scenario: "s01" };
  const params = {
    limit: 12,
    categoryId: categoryId || undefined,
    sortBy,
    sortOrder,
    minPrice: minPrice || undefined,
    maxPrice: maxPrice || undefined,
  };
  let res;
  if (API_TYPE === "rest") {
    res = restGet(`${BASE}/products`, params, tag);
  } else {
    res = trpcQuery(BASE, "product.getAll", params, tag);
  }
  latencyFilter.add(res.timings.duration);
  payloadBytes.add(res.body ? res.body.length : 0);
  checkAndRecord(
    res,
    "product_filter",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    600,
  );
  return res;
}

function getCategories() {
  const tag = { endpoint: "categories", api: API_TYPE, scenario: "s01" };
  let res;
  if (API_TYPE === "rest") {
    res = http.get(`${BASE}/categories`, { headers: JSON_HEADERS, tags: tag });
  } else {
    res = trpcQuery(BASE, "category.getAll", null, tag);
  }
  checkAndRecord(
    res,
    "categories",
    null,
    functionalErrorRate,
    slaBreachRate,
    errorCounter,
    400,
  );
  return res;
}

// =============================================================
// WORKLOAD MIX: 40% list | 30% detail | 20% search | 10% filter
// =============================================================

function doProductList() {
  group("product_list", () => {
    getProductList(randomInt(1, 10), 12);
    sleep(thinkTime(2, 0.5));
  });
}

function doProductDetail() {
  group("product_detail", () => {
    const count = randomInt(1, 3);
    for (let i = 0; i < count; i++) {
      if (PRODUCT_SLUGS.length > 0) {
        getProductDetail(randomItem(PRODUCT_SLUGS));
      } else {
        getProductList(1, 12);
      }
      sleep(thinkTime(2, 0.5));
    }
  });
}

function doSearch() {
  group("product_search", () => {
    searchProducts(randomItem(SEARCH_KEYWORDS));
    sleep(thinkTime(2, 0.5));
  });
}

function doFilter() {
  group("product_filter", () => {
    const sort = randomItem(SORT_OPTIONS);
    const catId =
      CATEGORY_IDS.length > 0 ? randomItem(CATEGORY_IDS) : undefined;
    const minPrice = randomInt(10000, 200000);
    const maxPrice = randomInt(200001, 5000000);
    filterProducts(catId, sort.sortBy, sort.sortOrder, minPrice, maxPrice);
    sleep(thinkTime(2, 0.5));
  });
}

// =============================================================
// SETUP — fail-fast jika seed data kosong
// =============================================================

export function setup() {
  console.log(`\n${"=".repeat(60)}`);
  console.log(
    `S-01 Browse Flow | API: ${API_TYPE.toUpperCase()} | Test: ${TEST_TYPE.toUpperCase()}`,
  );
  console.log(`Base URL: ${BASE}`);
  console.log(`${"=".repeat(60)}\n`);

  const res = http.get(HEALTH_URL);
  if (res.status !== 200)
    throw new Error(`Health check failed: ${res.status} — is server running?`);

  // FIX: fail-fast untuk seed data penting
  if (PRODUCT_SLUGS.length === 0) {
    throw new Error(
      "[SETUP FAIL] PRODUCT_SLUGS kosong. Isi seed.js dulu.\n" +
        "Query: SELECT slug FROM products WHERE is_active=true AND stock>100 ORDER BY random() LIMIT 100;",
    );
  }
  if (SEARCH_KEYWORDS.length === 0) {
    throw new Error("[SETUP FAIL] SEARCH_KEYWORDS kosong di seed.js.");
  }
  if (SORT_OPTIONS.length === 0) {
    throw new Error("[SETUP FAIL] SORT_OPTIONS kosong di seed.js.");
  }

  console.log(`✓ PRODUCT_SLUGS: ${PRODUCT_SLUGS.length}`);
  console.log(`✓ CATEGORY_IDS: ${CATEGORY_IDS.length}`);
  console.log(`✓ SEARCH_KEYWORDS: ${SEARCH_KEYWORDS.length}`);

  // Warm up categories (tidak dihitung ke metrik test)
  getCategories();

  console.log("Server ready. Starting test...");
  return {
    apiType: API_TYPE,
    testType: TEST_TYPE,
    startTime: new Date().toISOString(),
  };
}

// =============================================================
// VU FUNCTION
// =============================================================

export default function () {
  const rand = Math.random() * 100;
  if (rand < 40) doProductList();
  else if (rand < 70) doProductDetail();
  else if (rand < 90) doSearch();
  else doFilter();
}

// =============================================================
// HANDLE SUMMARY
// =============================================================

export function handleSummary(data) {
  const ts = __ENV.TS || Date.now();
  const filename = `results/s01_browse_${API_TYPE}_${TEST_TYPE}_${ts}.json`;
  return {
    [filename]: JSON.stringify(data, null, 2),
    stdout: buildSummary(data),
  };
}

function buildSummary(data) {
  const m = data.metrics;
  const dur = m.http_req_duration?.values;
  const fmt = (v) => (v != null ? v.toFixed(1) : "N/A");
  const pct = (v) => (v != null ? (v * 100).toFixed(2) : "N/A");

  return `
${"=".repeat(62)}
  S-01 BROWSE FLOW | ${API_TYPE.toUpperCase()} | ${TEST_TYPE.toUpperCase()}
${"=".repeat(62)}
  Response Time (ms)
    Avg:    ${fmt(dur?.avg)}       P95: ${fmt(dur?.["p(95)"])}
    Median: ${fmt(dur?.med)}       P99: ${fmt(dur?.["p(99)"])}
  Throughput:  ${fmt(m.http_reqs?.values?.rate)} req/s
  Total Reqs:  ${m.http_reqs?.values?.count || 0}

  Error Rates (PISAH saat analisis)
    Functional Error (4xx/5xx): ${pct(m.functional_error_rate?.values?.rate)}%
    SLA Breach (2xx tapi lambat): ${pct(m.sla_breach_rate?.values?.rate)}%

  Per-Endpoint P95 (ms)
    Product List:   ${fmt(m.latency_product_list?.values?.["p(95)"])}
    Product Detail: ${fmt(m.latency_product_detail?.values?.["p(95)"])}
    Search:         ${fmt(m.latency_product_search?.values?.["p(95)"])}
    Filter:         ${fmt(m.latency_product_filter?.values?.["p(95)"])}
  Avg Payload:     ${fmt(m.payload_size_bytes?.values?.avg)} bytes
${"=".repeat(62)}\n`;
}
