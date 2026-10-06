/**
 * One-shot MongoDB -> Convex data migration for Smart System.
 *
 * Usage (from /app/scripts):
 *   npm install
 *   MONGO_URL="mongodb://localhost:27017" DB_NAME="test_database" \
 *   CONVEX_URL="https://fearless-ostrich-878.eu-west-1.convex.cloud" \
 *   MIGRATION_SECRET="smartsystem-migrate-2026" \
 *   node migrate_mongo_to_convex.mjs
 *
 * It reads every collection from Mongo and inserts the documents into the
 * matching Convex table via the `migrate:importBatch` mutation. Mongo `_id`
 * ObjectIds are dropped and all values are coerced to plain JSON (Date -> ISO
 * string) so they fit the Convex schema (which keeps the original string `id`s).
 *
 * Re-runnable: pass CLEAR=1 to wipe each Convex table before importing.
 */
import { MongoClient } from "mongodb";
import { ConvexHttpClient } from "convex/browser";
import { makeFunctionReference } from "convex/server";

const MONGO_URL = process.env.MONGO_URL || "mongodb://localhost:27017";
const DB_NAME = process.env.DB_NAME || "test_database";
const CONVEX_URL = process.env.CONVEX_URL || process.env.EXPO_PUBLIC_CONVEX_URL;
const SECRET = process.env.MIGRATION_SECRET || "smartsystem-migrate-2026";
const CLEAR = process.env.CLEAR === "1";
const BATCH = 200;

if (!CONVEX_URL) {
  console.error("Set CONVEX_URL (or EXPO_PUBLIC_CONVEX_URL) to your Convex deployment URL.");
  process.exit(1);
}

// All collections defined in convex/schema.ts.
const COLLECTIONS = [
  "users",
  "user_sessions",
  "organizations",
  "licenses",
  "invitations",
  "products",
  "customers",
  "customer_types",
  "sales",
  "collections",
  "sales_returns",
  "payment_vouchers",
  "purchases",
  "purchase_returns",
  "deliveries",
  "warehouse_returns",
  "distributor_inventory",
  "stock_movements",
  "stock_requests",
  "counters",
  "routes",
  "agent_locations",
  "plans",
  "app_settings",
  "upgrade_requests",
  "notifications",
  "deletion_requests",
  "app_versions",
  "price_history",
];

const importBatch = makeFunctionReference("migrate:importBatch");
const clearTable = makeFunctionReference("migrate:clearTable");
const countTable = makeFunctionReference("migrate:countTable");

const toPlainJson = (doc) => {
  const { _id, ...rest } = doc; // drop Mongo ObjectId
  return JSON.parse(JSON.stringify(rest)); // coerce Date/Decimal etc. to JSON
};

async function main() {
  const convex = new ConvexHttpClient(CONVEX_URL);
  const mongo = new MongoClient(MONGO_URL);
  await mongo.connect();
  const db = mongo.db(DB_NAME);
  console.log(`Migrating ${DB_NAME} -> ${CONVEX_URL}\n`);

  let grand = 0;
  const report = []; // { name, mongo, inserted }
  for (const name of COLLECTIONS) {
    const exists = await db.listCollections({ name }).hasNext();
    if (!exists) {
      console.log(`- ${name}: (no such collection, skipped)`);
      continue;
    }
    const mongoCount = await db.collection(name).countDocuments({});
    const docs = (await db.collection(name).find({}).toArray()).map(toPlainJson);
    if (CLEAR) await convex.mutation(clearTable, { secret: SECRET, table: name });
    let inserted = 0;
    for (let i = 0; i < docs.length; i += BATCH) {
      const res = await convex.mutation(importBatch, { secret: SECRET, table: name, docs: docs.slice(i, i + BATCH) });
      inserted += res.inserted;
    }
    grand += inserted;
    report.push({ name, mongo: mongoCount, inserted });
    console.log(`- ${name}: ${inserted} documents`);
  }

  // ---- record-count verification: Mongo vs Convex ----
  console.log(`\nVerifying record counts (Mongo vs Convex)…`);
  let mismatches = 0;
  for (const row of report) {
    const { count: convexCount } = await convex.query(countTable, { secret: SECRET, table: row.name });
    // When CLEAR=1, Convex holds exactly the migrated rows; otherwise it may hold
    // pre-existing rows too, so we check that Convex has AT LEAST the Mongo count.
    const ok = CLEAR ? convexCount === row.mongo : convexCount >= row.mongo;
    if (!ok) mismatches++;
    console.log(`  ${ok ? "✓" : "✗"} ${row.name}: mongo=${row.mongo} convex=${convexCount}`);
  }

  await mongo.close();
  console.log(`\nDone. ${grand} documents migrated to Convex.`);
  if (mismatches) {
    console.error(`\n${mismatches} table(s) failed the count check.`);
    process.exit(2);
  }
  console.log(`All ${report.length} migrated table(s) passed the count check.`);
}

main().catch((e) => {
  console.error("Migration failed:", e);
  process.exit(1);
});
