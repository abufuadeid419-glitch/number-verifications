#!/usr/bin/env bash
set -e
cd /app/frontend
export CONVEX_DEPLOY_KEY="$(grep -oE "dev:fearless-ostrich-878\|[^'\"]+" /app/backend/.env | head -1)"
export CONVEX_TMPDIR=/app/frontend/.convex-tmp
SEC="smartsystem-migrate-2026"
run() { npx convex run "$@" 2>/dev/null; }

echo "=== clearing tables ==="
for t in organizations users user_sessions products customers customer_types distributor_inventory sales collections sales_returns purchases payment_vouchers counters stock_movements price_history; do
  run migrate:clearTable "{\"secret\":\"$SEC\",\"table\":\"$t\"}" >/dev/null
done

echo "=== seeding ==="
run migrate:importBatch "{\"secret\":\"$SEC\",\"table\":\"organizations\",\"docs\":[{\"id\":\"org_test1\",\"name\":\"مؤسسة اختبار\",\"owner_id\":\"user_test_owner\",\"owner_email\":\"owner@test.com\",\"status\":\"ACTIVE\",\"plan\":\"LICENSE\",\"max_employees\":10,\"expires_at\":\"2030-01-01T00:00:00+00:00\",\"created_at\":\"2026-06-01T00:00:00+00:00\"}]}"
run migrate:importBatch "{\"secret\":\"$SEC\",\"table\":\"users\",\"docs\":[{\"user_id\":\"user_test_owner\",\"email\":\"owner@test.com\",\"name\":\"المالك\",\"role\":\"OWNER\",\"employee_type\":null,\"org_id\":\"org_test1\",\"created_at\":\"2026-06-01T00:00:00+00:00\"},{\"user_id\":\"user_test_agent\",\"email\":\"agent@test.com\",\"name\":\"الموزع\",\"role\":\"EMPLOYEE\",\"employee_type\":\"FIELD_AGENT\",\"org_id\":\"org_test1\",\"created_at\":\"2026-06-01T00:00:00+00:00\"}]}"
run migrate:importBatch "{\"secret\":\"$SEC\",\"table\":\"user_sessions\",\"docs\":[{\"session_token\":\"tok_owner_test\",\"user_id\":\"user_test_owner\",\"expires_at\":\"2030-01-01T00:00:00+00:00\",\"created_at\":\"2026-06-01T00:00:00+00:00\"},{\"session_token\":\"tok_agent_test\",\"user_id\":\"user_test_agent\",\"expires_at\":\"2030-01-01T00:00:00+00:00\",\"created_at\":\"2026-06-01T00:00:00+00:00\"}]}"
run migrate:importBatch "{\"secret\":\"$SEC\",\"table\":\"products\",\"docs\":[{\"id\":\"prod_test1\",\"org_id\":\"org_test1\",\"name\":\"منتج اختبار\",\"category\":\"عام\",\"unit\":\"قطعة\",\"cost_price\":6,\"sale_price\":10,\"stock\":100,\"min_stock\":5,\"created_at\":\"2026-06-01T00:00:00+00:00\"}]}"
run migrate:importBatch "{\"secret\":\"$SEC\",\"table\":\"customers\",\"docs\":[{\"id\":\"cust_test1\",\"org_id\":\"org_test1\",\"name\":\"عميل اختبار\",\"phone\":\"0555555555\",\"address\":\"العنوان\",\"location\":\"\",\"type_id\":null,\"lat\":24.7,\"lng\":46.7,\"balance\":0,\"distributor_id\":\"user_test_agent\",\"distributor_name\":\"الموزع\",\"created_by\":\"user_test_agent\",\"created_at\":\"2026-06-01T00:00:00+00:00\"}]}"
run migrate:importBatch "{\"secret\":\"$SEC\",\"table\":\"distributor_inventory\",\"docs\":[{\"org_id\":\"org_test1\",\"distributor_id\":\"user_test_agent\",\"product_id\":\"prod_test1\",\"product_name\":\"منتج اختبار\",\"quantity\":50}]}"

echo "=== sale (agent): 5 x 10, paid 30 -> total 50, remaining 20 ==="
run sales:create "{\"token\":\"tok_agent_test\",\"customer_id\":\"cust_test1\",\"items\":[{\"product_id\":\"prod_test1\",\"quantity\":5}],\"paid_amount\":30}"

echo "=== collection (agent): 10 -> customer balance 20-10=10 ==="
run collections:create "{\"token\":\"tok_agent_test\",\"customer_id\":\"cust_test1\",\"amount\":10}"

echo "=== overview (owner): expect sales_total 50, collections_total 10, debts_total 10, gross_profit 20, stock_value 600 ==="
run stats:overview "{\"token\":\"tok_owner_test\"}"

echo "=== leaderboard (owner): expect agent rank 1, sales_total 50, collections_total 10 ==="
run stats:leaderboard "{\"token\":\"tok_owner_test\"}"

echo "=== agent sales list ==="
run sales:list "{\"token\":\"tok_agent_test\"}"

echo "=== negative: agent cannot read leaderboard (STAFF only) ==="
run stats:leaderboard "{\"token\":\"tok_agent_test\"}" || echo "(rejected as expected)"
