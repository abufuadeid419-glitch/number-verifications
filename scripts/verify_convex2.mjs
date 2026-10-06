import { ConvexHttpClient } from "convex/browser";
import { makeFunctionReference } from "convex/server";

const URL = process.env.CONVEX_URL;
const SEC = process.env.MIGRATION_SECRET || "smartsystem-migrate-2026";
const c = new ConvexHttpClient(URL);
const ref = (n) => makeFunctionReference(n);
const mut = (n, a) => c.mutation(ref(n), a);
const qry = (n, a) => c.query(ref(n), a);

let pass = 0,
  fail = 0;
const ok = (cond, label) => {
  if (cond) {
    pass++;
    console.log(`  ✓ ${label}`);
  } else {
    fail++;
    console.log(`  ✗ ${label}`);
  }
};

const FUTURE = "2030-01-01T00:00:00+00:00";
const seed = async () => {
  const tables = [
    "organizations",
    "users",
    "user_sessions",
    "products",
    "customers",
    "distributor_inventory",
    "deliveries",
    "warehouse_returns",
    "sales",
    "sales_returns",
    "stock_requests",
    "invitations",
    "counters",
    "stock_movements",
    "agent_locations",
    "collections",
  ];
  for (const t of tables) await mut("migrate:clearTable", { secret: SEC, table: t });
  await mut("migrate:importBatch", {
    secret: SEC,
    table: "organizations",
    docs: [{ id: "org_v2", name: "اختبار v2", owner_id: "u_owner", owner_email: "o@t.com", status: "ACTIVE", plan: "LICENSE", max_employees: 10, expires_at: FUTURE, created_at: "2026-01-01T00:00:00+00:00" }],
  });
  await mut("migrate:importBatch", {
    secret: SEC,
    table: "users",
    docs: [
      { user_id: "u_owner", email: "o@t.com", name: "المالك", role: "OWNER", employee_type: null, org_id: "org_v2" },
      { user_id: "u_agent", email: "a@t.com", name: "الموزع", role: "EMPLOYEE", employee_type: "FIELD_AGENT", org_id: "org_v2" },
      { user_id: "u_new", email: "n@t.com", name: "جديد", role: null, employee_type: null, org_id: null },
    ],
  });
  await mut("migrate:importBatch", {
    secret: SEC,
    table: "user_sessions",
    docs: [
      { session_token: "t_owner", user_id: "u_owner", expires_at: FUTURE },
      { session_token: "t_agent", user_id: "u_agent", expires_at: FUTURE },
      { session_token: "t_new", user_id: "u_new", expires_at: FUTURE },
    ],
  });
  await mut("migrate:importBatch", {
    secret: SEC,
    table: "products",
    docs: [{ id: "prod_v2", org_id: "org_v2", name: "منتج", category: "عام", unit: "قطعة", cost_price: 6, sale_price: 10, stock: 100, min_stock: 5 }],
  });
  await mut("migrate:importBatch", {
    secret: SEC,
    table: "customers",
    docs: [{ id: "cust_v2", org_id: "org_v2", name: "عميل", phone: "0555", address: "x", type_id: null, lat: 24.7, lng: 46.7, balance: 0, distributor_id: "u_agent", distributor_name: "الموزع", created_by: "u_agent" }],
  });
};

const prodStock = async () => (await qry("products:list", { token: "t_owner" })).find((p) => p.id === "prod_v2").stock;
const invQty = async () => {
  const inv = await qry("deliveries:myInventory", { token: "t_agent" });
  const row = inv.find((i) => i.product_id === "prod_v2");
  return row ? row.quantity : 0;
};
const custBalance = async () => (await qry("customers:list", { token: "t_agent" })).find((x) => x.id === "cust_v2").balance;

async function main() {
  console.log(`verify_convex2 -> ${URL}\n`);
  await seed();

  console.log("Deliveries + top-up:");
  const del = await mut("deliveries:create", { token: "t_owner", distributor_id: "u_agent", items: [{ product_id: "prod_v2", quantity: 10 }], notes: "" });
  ok(del.status === "PENDING" && del.items[0].quantity === 10, "owner creates delivery (PENDING, 10)");
  ok((await prodStock()) === 90, "warehouse stock 100 -> 90 after delivery");
  try {
    await mut("deliveries:create", { token: "t_agent", distributor_id: "u_agent", items: [{ product_id: "prod_v2", quantity: 1 }] });
    ok(false, "agent is blocked from creating delivery");
  } catch {
    ok(true, "agent is blocked from creating delivery (ليس لديك صلاحية)");
  }
  await mut("deliveries:confirm", { token: "t_agent", id: del.id });
  ok((await invQty()) === 10, "distributor inventory 0 -> 10 after confirm");
  const distInv = await qry("deliveries:distributorsInventory", { token: "t_owner" });
  ok(distInv.some((r) => r.product_id === "prod_v2" && r.quantity === 10), "owner sees distributor inventory (10)");

  console.log("\nSale consumes inventory:");
  await mut("sales:create", { token: "t_agent", customer_id: "cust_v2", items: [{ product_id: "prod_v2", quantity: 3 }], paid_amount: 0, lat: 24.71, lng: 46.71 });
  ok((await invQty()) === 7, "inventory 10 -> 7 after selling 3");
  ok((await custBalance()) === 30, "customer debt 0 -> 30 after credit sale");

  console.log("\nSales return (back into distributor stock):");
  const ret = await mut("returns:createSales", { token: "t_agent", customer_id: "cust_v2", items: [{ product_id: "prod_v2", quantity: 1 }], reason: "تالف" });
  ok(ret.total === 10, "sales-return total = 10");
  ok((await invQty()) === 8, "inventory 7 -> 8 after return");
  ok((await custBalance()) === 20, "customer debt 30 -> 20 after return");

  console.log("\nWarehouse return (distributor -> warehouse, owner accepts):");
  const wret = await mut("returns:createWarehouse", { token: "t_agent", items: [{ product_id: "prod_v2", quantity: 2 }], notes: "" });
  ok(wret.status === "PENDING", "warehouse-return created PENDING");
  ok((await invQty()) === 6, "inventory 8 -> 6 after warehouse-return out");
  await mut("returns:acceptWarehouse", { token: "t_owner", id: wret.id });
  ok((await prodStock()) === 92, "warehouse stock 90 -> 92 after accept");

  console.log("\nStock request fulfilment creates a delivery:");
  const req = await mut("deliveries:createRequest", { token: "t_agent", items: [{ product_id: "prod_v2", quantity: 5 }], note: "" });
  ok(req.status === "PENDING", "stock request PENDING");
  const fr = await mut("deliveries:fulfillRequest", { token: "t_owner", id: req.id });
  ok(fr.status === "FULFILLED" && fr.delivery_id, "request FULFILLED with delivery_id");
  ok((await prodStock()) === 87, "warehouse stock 92 -> 87 after fulfilment delivery");

  console.log("\nEmployees: invite + activate:");
  const inv = await mut("employees:invite", { token: "t_owner", name: "موزع جديد", employee_type: "FIELD_AGENT" });
  ok(inv.code.startsWith("EMP-"), `invite code generated (${inv.code})`);
  const elist = await qry("employees:list", { token: "t_owner" });
  ok(elist.invitations.some((i) => i.code === inv.code), "invite appears in employees list");
  const act = await mut("employees:activate", { token: "t_new", code: inv.code });
  ok(act.user.role === "EMPLOYEE" && act.user.employee_type === "FIELD_AGENT" && act.user.org_id === "org_v2", "new user activated as FIELD_AGENT in org");
  try {
    await mut("employees:activate", { token: "t_new", code: inv.code });
    ok(false, "already-activated user cannot re-activate");
  } catch {
    ok(true, "already-activated user cannot re-activate (الحساب مفعل مسبقاً)");
  }

  console.log("\nLive agent map (GPS tracking):");
  await mut("tracking:postLocation", { token: "t_agent", lat: 24.72, lng: 46.72, accuracy: 5 });
  const ta = await qry("tracking:agents", { token: "t_owner" });
  const me = ta.find((x) => x.user_id === "u_agent");
  ok(!!me && me.last_location && me.last_location.lat === 24.72, "owner sees agent last_location");
  ok(me.today_visits.length >= 1, "owner sees today's sale visit on the map");
  const trail = await qry("tracking:trail", { token: "t_owner", user_id: "u_agent" });
  ok(trail.points.length >= 1 && trail.visits.length >= 1, "agent trail has GPS points + visits");

  console.log(`\n${pass} passed, ${fail} failed.`);
  // cleanup
  for (const t of ["organizations", "users", "user_sessions", "products", "customers", "distributor_inventory", "deliveries", "warehouse_returns", "sales", "sales_returns", "stock_requests", "invitations", "counters", "stock_movements", "agent_locations", "collections"])
    await mut("migrate:clearTable", { secret: SEC, table: t });
  console.log("cleaned up test data.");
  process.exit(fail ? 1 : 0);
}
main().catch((e) => {
  console.error("FATAL", e.message);
  process.exit(1);
});
