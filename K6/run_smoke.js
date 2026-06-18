// =============================================================
// run_smoke.js — Smoke test semua endpoint (1 VU, 2 menit)
// Jalankan ini SEBELUM setiap load test run untuk validasi
//
// FIX:
//  - Pisah sesi user dan admin (tidak campur role dalam satu sesi)
//  - Fail-fast jika seed data belum diisi
//  - Pisah metrik functional_error dari sla_breach
//
// Run:
//   k6 run --env API=rest run_smoke.js
//   k6 run --env API=trpc run_smoke.js
// =============================================================

import http from "k6/http";
import { check, sleep } from "k6";
import { API_TYPE, BASE, HEALTH_URL, JSON_HEADERS } from "./config.js";
import {
  login,
  logout,
  ADMIN_USERS,
  ADMIN_PASSWORD,
  TEST_USERS,
  TEST_PASSWORD,
} from "./auth.js";
import {
  PRODUCT_SLUGS,
  PRODUCT_IDS_FOR_CART,
  CATEGORY_IDS,
  SEARCH_KEYWORDS,
} from "./seed.js";
import { trpcQuery, trpcMutation, restGet, restPost } from "./http.js";

export const options = {
  vus: 1,
  duration: "2m",
  thresholds: {
    http_req_failed: ["rate<0.05"],
  },
};

// =============================================================
// SETUP — fail-fast jika seed data atau server belum siap
// =============================================================

export function setup() {
  const res = http.get(HEALTH_URL);
  if (res.status !== 200)
    throw new Error(`[SMOKE FAIL] Server tidak ready: ${res.status}`);

  console.log(`\n${"=".repeat(60)}`);
  console.log(`Smoke Test | API: ${API_TYPE.toUpperCase()} | Base: ${BASE}`);
  console.log(`${"=".repeat(60)}`);

  // Fail-fast untuk seed data kritis
  if (PRODUCT_SLUGS.length === 0) {
    throw new Error(
      "[SMOKE FAIL] PRODUCT_SLUGS kosong.\n" +
        "Isi seed.js sebelum run:\n" +
        '  psql $DATABASE_URL -c "SELECT slug FROM products WHERE is_active=true AND stock>100 ORDER BY random() LIMIT 100;" -t -A',
    );
  }
  if (PRODUCT_IDS_FOR_CART.length < 3) {
    throw new Error(
      `[SMOKE FAIL] PRODUCT_IDS_FOR_CART hanya ${PRODUCT_IDS_FOR_CART.length} item, minimal 3.\n` +
        '  psql $DATABASE_URL -c "SELECT id FROM products WHERE is_active=true AND stock>500 ORDER BY random() LIMIT 30;" -t -A',
    );
  }
  if (CATEGORY_IDS.length === 0) {
    throw new Error(
      "[SMOKE FAIL] CATEGORY_IDS kosong.\n" +
        '  psql $DATABASE_URL -c "SELECT id FROM categories ORDER BY name;" -t -A',
    );
  }
  if (TEST_USERS.length === 0 || TEST_USERS[0] === "user1@example.com") {
    throw new Error(
      "[SMOKE FAIL] TEST_USERS masih placeholder.\n" +
        "  psql $DATABASE_URL -c \"SELECT email FROM users WHERE role='USER' ORDER BY created_at LIMIT 500;\" -t -A",
    );
  }

  console.log(`✓ PRODUCT_SLUGS: ${PRODUCT_SLUGS.length}`);
  console.log(`✓ PRODUCT_IDS_FOR_CART: ${PRODUCT_IDS_FOR_CART.length}`);
  console.log(`✓ CATEGORY_IDS: ${CATEGORY_IDS.length}`);
  console.log(`✓ TEST_USERS: ${TEST_USERS.length}`);
  console.log(`✓ SEARCH_KEYWORDS: ${SEARCH_KEYWORDS.length}`);
  console.log("Server ready, seed data OK. Starting smoke test...\n");

  return {};
}

// =============================================================
// HELPER: check wrapper ringkas untuk smoke
// =============================================================

function smokeCheck(res, name) {
  const ok = check(res, {
    [`${name}: status 2xx`]: (r) => r.status >= 200 && r.status < 300,
    [`${name}: duration < 3000ms`]: (r) => r.timings.duration < 3000,
  });
  if (!ok) {
    console.error(
      `[FAIL] ${name} | status=${res.status} | ${res.timings.duration.toFixed(0)}ms`,
    );
  }
  return ok;
}

// =============================================================
// DEFAULT — satu iterasi lengkap dengan session terpisah
// FIX: user session dan admin session tidak dicampur
// =============================================================

export default function () {
  let res;

  // ──────────────────────────────────────────────────────────
  // BLOCK A: Public endpoints (tanpa auth)
  // ──────────────────────────────────────────────────────────

  // A1. Product list
  if (API_TYPE === "rest") {
    res = restGet(
      `${BASE}/products`,
      { page: 1, limit: 5 },
      { scenario: "smoke_public" },
    );
  } else {
    res = trpcQuery(
      BASE,
      "product.getAll",
      { page: 1, limit: 5 },
      { scenario: "smoke_public" },
    );
  }
  smokeCheck(res, "A1 products.list");
  sleep(0.3);

  // A2. Product search
  const keyword = SEARCH_KEYWORDS.length > 0 ? SEARCH_KEYWORDS[0] : "a";
  if (API_TYPE === "rest") {
    res = restGet(
      `${BASE}/products/search`,
      { q: keyword, limit: 5 },
      { scenario: "smoke_public" },
    );
  } else {
    res = trpcQuery(
      BASE,
      "product.search",
      { q: keyword, limit: 5 },
      { scenario: "smoke_public" },
    );
  }
  smokeCheck(res, "A2 products.search");
  sleep(0.3);

  // A3. Product detail
  if (PRODUCT_SLUGS.length > 0) {
    if (API_TYPE === "rest") {
      res = http.get(`${BASE}/products/${PRODUCT_SLUGS[0]}`, {
        headers: JSON_HEADERS,
      });
    } else {
      res = trpcQuery(
        BASE,
        "product.getBySlug",
        { slug: PRODUCT_SLUGS[0] },
        {},
      );
    }
    smokeCheck(res, "A3 products.detail");
    sleep(0.3);
  }

  // A4. Categories
  if (API_TYPE === "rest") {
    res = http.get(`${BASE}/categories`, { headers: JSON_HEADERS });
  } else {
    res = trpcQuery(BASE, "category.getAll", null, {});
  }
  smokeCheck(res, "A4 categories.list");
  sleep(0.3);

  // ──────────────────────────────────────────────────────────
  // BLOCK B: USER session — role USER (bukan admin)
  // FIX: sesi ini hanya pakai user biasa, tidak ada admin endpoint
  // ──────────────────────────────────────────────────────────

  const userEmail = TEST_USERS[0];
  const userPassword = TEST_PASSWORD;
  const userLoggedIn = login(API_TYPE, BASE, userEmail, userPassword);

  check(
    { userLoggedIn },
    { "B0 user.login: success": (d) => d.userLoggedIn === true },
  );
  sleep(0.3);

  if (userLoggedIn) {
    // B1. Auth/me
    if (API_TYPE === "rest") {
      res = http.get(`${BASE}/auth/me`, { headers: JSON_HEADERS });
    } else {
      res = trpcQuery(BASE, "auth.me", null, {});
    }
    smokeCheck(res, "B1 auth.me");
    sleep(0.3);

    // B2. Profile
    if (API_TYPE === "rest") {
      res = http.get(`${BASE}/profile`, { headers: JSON_HEADERS });
    } else {
      res = trpcQuery(BASE, "profile.get", null, {});
    }
    smokeCheck(res, "B2 profile.get");
    sleep(0.3);

    // B3. Cart get
    if (API_TYPE === "rest") {
      res = http.get(`${BASE}/cart`, { headers: JSON_HEADERS });
    } else {
      res = trpcQuery(BASE, "cart.get", null, {});
    }
    smokeCheck(res, "B3 cart.get");
    sleep(0.3);

    // B4. Cart add item (smoke — 1 item, 1 quantity)
    if (PRODUCT_IDS_FOR_CART.length > 0) {
      if (API_TYPE === "rest") {
        res = restPost(
          `${BASE}/cart`,
          { productId: PRODUCT_IDS_FOR_CART[0], quantity: 1 },
          {},
        );
      } else {
        res = trpcMutation(
          BASE,
          "cart.addItem",
          { productId: PRODUCT_IDS_FOR_CART[0], quantity: 1 },
          {},
        );
      }
      smokeCheck(res, "B4 cart.addItem");
      sleep(0.3);
    }

    // B5. Addresses
    if (API_TYPE === "rest") {
      res = http.get(`${BASE}/profile/addresses`, { headers: JSON_HEADERS });
    } else {
      res = trpcQuery(BASE, "profile.getAddresses", null, {});
    }
    smokeCheck(res, "B5 profile.getAddresses");
    sleep(0.3);

    // B6. Orders list
    if (API_TYPE === "rest") {
      res = restGet(`${BASE}/orders`, { page: 1 }, {});
    } else {
      res = trpcQuery(BASE, "order.getAll", { page: 1 }, {});
    }
    smokeCheck(res, "B6 orders.list");
    sleep(0.3);

    // B7. Refresh token
    if (API_TYPE === "rest") {
      res = restPost(`${BASE}/auth/refresh`, {}, {});
    } else {
      res = trpcMutation(BASE, "auth.refresh", {}, {});
    }
    smokeCheck(res, "B7 auth.refresh");
    sleep(0.3);

    // B8. Clear cart (cleanup)
    if (API_TYPE === "rest") {
      http.del(`${BASE}/cart`, null, { headers: JSON_HEADERS });
    } else {
      trpcMutation(BASE, "cart.clear", {}, {});
    }
    sleep(0.3);

    // B9. Logout user
    logout(API_TYPE, BASE);
    sleep(0.3);
  }

  // ──────────────────────────────────────────────────────────
  // BLOCK C: ADMIN session — role ADMIN saja
  // FIX: sesi ini terpisah dari user session di atas
  // ──────────────────────────────────────────────────────────

  const adminEmail = ADMIN_USERS[0];
  const adminLoggedIn = login(API_TYPE, BASE, adminEmail, ADMIN_PASSWORD);

  check(
    { adminLoggedIn },
    { "C0 admin.login: success": (d) => d.adminLoggedIn === true },
  );
  sleep(0.3);

  if (adminLoggedIn) {
    // C1. Admin dashboard
    if (API_TYPE === "rest") {
      res = http.get(`${BASE}/admin/dashboard`, { headers: JSON_HEADERS });
    } else {
      res = trpcQuery(BASE, "admin.getDashboard", null, {});
    }
    smokeCheck(res, "C1 admin.dashboard");
    sleep(0.3);

    // C2. Admin products list
    if (API_TYPE === "rest") {
      res = restGet(`${BASE}/admin/products`, { page: 1, limit: 5 }, {});
    } else {
      res = trpcQuery(BASE, "admin.getProducts", { page: 1, limit: 5 }, {});
    }
    smokeCheck(res, "C2 admin.products.list");
    sleep(0.3);

    // C3. Admin orders list
    if (API_TYPE === "rest") {
      res = restGet(`${BASE}/admin/orders`, { page: 1, limit: 5 }, {});
    } else {
      res = trpcQuery(BASE, "admin.getOrders", { page: 1, limit: 5 }, {});
    }
    smokeCheck(res, "C3 admin.orders.list");
    sleep(0.3);

    // C4. Admin users list
    if (API_TYPE === "rest") {
      res = restGet(`${BASE}/admin/users`, { page: 1, limit: 5 }, {});
    } else {
      res = trpcQuery(BASE, "admin.getUsers", { page: 1, limit: 5 }, {});
    }
    smokeCheck(res, "C4 admin.users.list");
    sleep(0.3);

    // C5. Logout admin
    logout(API_TYPE, BASE);
    sleep(0.3);
  }

  console.log("Smoke iteration complete ✓");
  sleep(1);
}
