# Smart System (النظام الذكي) — Mobile (Expo) PRD

## Original problem
"clone this mobile app https://github.com/abufuadeid419-glitch/Last-App-code.git"
Restore an existing Emergent-built app exactly as-is into this workspace and get it running.

User choices (this session):
- Restore exactly as-is first, then iterate on request.
- Developer (super-admin) Google email: abufuadeid419@gmail.com (set in backend DEVELOPER_EMAILS).
- Skip Emergent managed storage for now (no EMERGENT_LLM_KEY → logo upload disabled by design).
- Start with an empty database (no demo/seed data).

## What this app is
Arabic RTL sales & distribution ERP. Roles: DEVELOPER, OWNER, ACCOUNTANT, FIELD_AGENT (distributor).
Auth: Emergent Google sign-in (bearer session_token in db.user_sessions). Activation by license
code (LIC-), employee code (EMP-), or a 14-day self-service trial.

## Architecture
- Backend: /app/backend/server.py — FastAPI + Motor (MongoDB). All routes under /api. Role deps:
  OWNER / STAFF / AGENT / ANY_ORG / DEV. Startup creates indexes + runs a weekly debt-digest loop.
- Frontend: Expo Router. Root gate in app/_layout.tsx routes to login / activate / blocked / dev /
  owner / acct / agent areas. Role tab groups via src/RoleTabs.tsx (NativeTabs on iOS 26+, JS Tabs
  elsewhere). UI kit src/ui.tsx. Cairo font, moss-green theme, forced RTL.
- Env: backend/.env (MONGO_URL, DB_NAME, DEVELOPER_EMAILS). frontend/.env (EXPO_PUBLIC_BACKEND_URL +
  protected packager vars — left untouched). INTEGRATION_PROXY_URL injected by supervisor.

## Clone / restore done (2026-06)
- Copied full repo into /app (preserved protected .env, .git, .emergent); yarn install + venv already
  had required backend packages (emergentintegrations/litellm are unused by this app and skipped).
- Set DEVELOPER_EMAILS=abufuadeid419@gmail.com.
- Verified backend serves (GET /api/ → "Smart System API") and Expo bundles (1519 modules); Arabic
  login screen renders on web preview.
- Ran shipped pytest suite serially: 198 passed. Testing agent independently re-verified: 198 passed,
  role gating + core flows OK (report /app/test_reports/iteration_8.json). No code changes needed.
- Wiped DB to empty per user choice. First Google sign-in with the developer email bootstraps DEVELOPER.

## Feature set (inherited, all implemented in source)
- Developer: stats, create/delete licenses, manage orgs (extend/suspend/reactivate), plans, payment
  settings, upgrade approvals, app-version/update gate, monitoring.
- Owner: KPIs (sales/profit/stock value/debts), low-stock alerts, agent performance + leaderboard,
  products CRUD, purchases (stock in), deliveries to distributors, employees (invite by code),
  org profile/logo, GPS agent tracking + maps, route planner, customer price lists, reports.
- Distributor (field agent): own inventory, new sale (cash/credit, discounts), collections, sales
  returns, warehouse returns, payment vouchers, customers CRUD (own), today's route, offline-first
  sync (queue + dedupe), PDF/80mm Bluetooth receipts, WhatsApp debt reminders.
- Accountant: overview, invoices/collections/returns, debts with collect, customer statements.
- Blocked screen for suspended/expired orgs; terms/privacy consent gate; legal screens.

## Feature: Owner onboarding guide (2026-06)
- New component src/components/OwnerOnboarding.tsx: a first-run checklist card on the owner home
  (src/screens/Overview.tsx, OWNER only) with two steps — "أضف أول منتج للمخزون" and
  "ادعُ أول موزع ميداني". Progress auto-detects from live data (/stats/overview products count,
  /employees field-agents + pending FIELD_AGENT invites), shows a progress bar + strikethrough on
  done steps, and persists completion/dismissal in storage (key onboarding_owner_done_<org_id>).
- Tap-and-go deep links: "إضافة منتج" → /owner/products?new=1 (auto-opens the new-product sheet in
  src/screens/Products.tsx); "دعوة موزع" → /owner/more?invite=1 (jumps to the team tab and opens the
  invite sheet in src/screens/OwnerMore.tsx).
- Verified on web preview with a seeded owner token: card renders, progress is correct, both deep
  links open their sheets. Separate from the existing tab-explainer GuidedTour.

## Bug fix: distributor/field-agent role (2026-06)
- Reported: logging in as a field agent showed "Unmatched Route — Page could not be found".
- Cause: homeFor() routes field agents to /dist, but the app/dist/ route group was never committed
  in the source repo (the distributor screens existed in src/screens but had no route).
- Fix: added app/dist/_layout.tsx (RoleTabs, tourKey "dist") with 4 tabs — index→Overview,
  sale→NewSale, customers→Customers, ops→DistributorOps.
- Verified by testing agent (report iteration_9.json): all 4 tabs render, no Unmatched Route, 9/9
  agent endpoints 200, owner/acct/dev regression OK.

## Convex migration (2026-06, in progress — self-managed external)
- User requested migrating the DB from MongoDB to Convex. Per platform support, a full backend
  swap isn't Emergent-deployable, so Convex is set up as a user-managed external service alongside
  the unchanged FastAPI+Mongo stack. User provided deploy URL + deploy key (stored in backend/.env
  as CONVEX_DEPLOY_KEY; client URL EXPO_PUBLIC_CONVEX_URL in frontend .env/.env.local).
- Done & verified on deployment `fearless-ostrich-878`:
  - frontend/convex/schema.ts — all 29 Mongo collections as Convex tables + indexes (deployed).
  - frontend/convex/lib.ts — bearer-token auth + role gates (OWNER/STAFF/AGENT/ANY_ORG/DEV),
    counters, stock-movement/price-history logging, mirroring server.py.
  - frontend/convex/products.ts + customers.ts — CRUD mirroring FastAPI (verified via convex run:
    auth ok, agent→403, bad token→401, round-trips ok).
  - frontend/convex/sales.ts + collections.ts — sale/invoice (stock, discounts, balance, invoice
    numbering) and debt collection (balance, receipt numbering) mirroring server.py (verified:
    numbers match FastAPI exactly).
  - frontend/convex/stats.ts — overview + agents + leaderboard live Convex queries (verified).
  - frontend/convex/returns.ts — sales-returns + warehouse-returns (create/accept/reject) mirroring
    server.py (verified: stock + balances adjust correctly).
  - frontend/convex/deliveries.ts — deliveries (create/confirm/reject), stock-requests
    (create/fulfill/reject), myInventory + distributorsInventory (verified end-to-end).
  - frontend/convex/employees.ts — list/invite/deleteInvite/remove + activate (EMP-/license) + trial
    (verified: invite→activate promotes the user; re-activate blocked).
  - frontend/convex/tracking.ts — postLocation + agents + trail for the live owner agent map
    (verified: GPS ping shows on owner map + trail).
  - frontend/convex/auth.ts (syncSession) — mirrors the already-authenticated FastAPI session into
    Convex so the same bearer token authenticates Convex functions (does not change auth).
  - frontend/convex/migrate.ts + scripts/migrate_mongo_to_convex.mjs — Node Mongo→Convex importer
    with per-table record-count verification. Run against the live Mongo here: migrated the real
    data (2 users + "Anmira" org), all count checks passed.
  - Frontend: src/convex.ts client, ConvexProvider in app/_layout.tsx, token via useAuth(), and
    app/convex-check.tsx is now a LIVE Convex dashboard (overview + leaderboard + products +
    live agent map + distributor inventory + deliveries + employees via useQuery/useMutation;
    reachable from Owner → الإدارة → ملف المؤسسة → "فحص Convex (تجريبي)"). Verified in the web
    preview as the real owner: dashboard renders live, UI product add appears instantly.
  - Verification scripts: scripts/verify_convex.sh + scripts/verify_convex2.mjs (23/23 e2e assertions).
- Remaining for full cutover (see /app/CONVEX_MIGRATION.md): port the other ~50 endpoints (sales,
  collections, returns, deliveries, inventory, stats, routes, employees, dev/plans/upgrades, GPS,
  notifications, offline semantics) then flip ALL screens off FastAPI at once (piecemeal switching
  breaks cross-feature flows). Until then the app runs unchanged on FastAPI+MongoDB.

## Known constraints
- Login is Emergent Google OAuth — deep frontend flows cannot be automated; validated via backend
  suite + seeded tokens (see /app/memory/test_credentials.md) and login-screen render check.
- Logo upload needs EMERGENT_LLM_KEY (storage) — intentionally disabled this session.
- Bluetooth thermal printing & background GPS require a native build (not Expo Go / web preview).
- Maps render on native devices; web preview shows list fallbacks.

## Backlog / next (P1/P2)
- P1: enable Emergent storage (add EMERGENT_LLM_KEY) to turn on org logo on invoices.
- P1: seed a demo org + sample data on request for quick exploration.
- P2: push notifications (Emergent managed) — needs user's google-services.json + a native build.


---

## Convex Migration — Phase 2 (2026-06, this session)

**Goal:** Port 4 more domains from MongoDB to the external Convex deployment
(`fearless-ostrich-878`): routes + stop optimization, payment vouchers + purchases,
real-time notification bell, and a live agent-map screen.

**Architecture decision (non-breaking incremental migration):**
FastAPI + MongoDB stay the single source of truth. After every relevant write,
FastAPI mirrors the document into Convex through a secret-guarded write-through
bridge, so the ported screens READ live/real-time from Convex via `useQuery`
while writes keep flowing through the existing, consistent FastAPI paths. This
avoids the cross-feature split the earlier migration notes warned about.

**Implemented & verified (20/20 backend tests, `test_iteration10_convex_bridge.py`):**
- `backend/convex_bridge.py` — fire-and-forget `cx_upsert` / `cx_insert` / `cx_delete`
  calling Convex `bridge:*` over the HTTP mutation API. Failures are swallowed so the
  app never breaks if Convex is down.
- `frontend/convex/bridge.ts` — secret-guarded `upsert` / `insertRow` / `removeByKey`.
- Bridge hooked into server.py: `enrich` (users+orgs), `notify` (notifications),
  `post_location` (agent_locations + users.last_location), `create_sale`,
  `save_route` / `optimize_route` / `stop_status` / `delete_route`,
  `create_payment_voucher`, `create_purchase`, `create_purchase_return`.
- New Convex functions mirroring FastAPI: `routes.ts` (list/mine/save/remove/optimize/
  stopStatus/kpis), `vouchers.ts` (list/create), `purchases.ts` (list/create +
  returns), `notifications.ts` (list/readAll, soft-auth).
- Frontend: `NotificationBell` reads live from Convex (real-time badge);
  `ConvexSessionSync` mirrors the session app-wide; new `app/agents-map.tsx` live
  owner map (locations + today's routes + 7-day KPIs); `convex-check` dashboard now
  also surfaces routes/vouchers/purchases live.

**Still on FastAPI only (future full cutover):** customer-types/price-lists, dev
licenses/orgs/plans/upgrades, org profile/logo, reports/alerts/finance, void-sale,
warehouse/sales returns writes, consent/legal, deletion requests, app-version gate.

**Backlog / notes:**
- `convex/routes.ts::save` inserts rather than upserts on `id` (unreachable from the
  app today since FastAPI owns writes) — upsert long-term before a full cutover.
- Shared bridge secret `smartsystem-migrate-2026` lives in 3 places — centralise later.


---

## FULL CUTOVER TO CONVEX — MongoDB retired (2026-06, this session)

Convex is now the ONLY backend + database. The entire REST surface (`/api/...`)
is served by a Convex HTTP router; FastAPI + MongoDB are retired.

**Architecture**
- `convex/http.ts` — HTTP router mapping all ~104 `/api/...` routes (GET/POST/PUT/
  PATCH/DELETE, path params, query strings, bearer auth, CORS, Arabic error
  `{detail}` with 400/401/403/404) to Convex queries/mutations/actions.
- `convex/extra.ts` — ported remaining domains: auth me/logout/consent/delete,
  void-sale, stock-movements, price-history, customer-types/price-lists, debts
  stale/digest, org profile/currency, plans, settings, upgrade-requests,
  deletion-requests, app-version, backup, stats alerts/finance/reports, and the
  full developer console (stats/licenses/orgs/plans/settings/upgrade-review/
  monitoring/versions/deletion-review).
- `convex/edge.ts` — Google OAuth session exchange (`exchangeSession` action +
  `createSession` mutation, keeps the first-user-is-developer bootstrap) and logo
  upload/serve using Convex file storage (`ctx.storage`), replacing Emergent
  Object Storage. Same `{data_uri}` contract for the app.
- Existing domain functions reused: products, customers, sales, collections,
  returns, deliveries + inventory + stock-requests, employees + activate/trial,
  vouchers, purchases, routes, tracking, stats, notifications.
- Frontend unchanged: `EXPO_PUBLIC_BACKEND_URL` now points to the Convex `.site`
  domain, so every existing `api()/useApi/useMutate` call hits Convex.
- `backend/server.py` is now a DB-less stub (no motor/Mongo client); MongoDB is
  no longer connected anywhere. The write-through bridge is obsolete.

**Verified:** 63/63 backend tests (`test_iteration11_convex_rest.py`) — auth,
role gates, 22 reads, full write chain (product→purchase→deliver→confirm→sell→
collect→route→invite), dev console, CORS, and that the FastAPI stub has no Mongo.

**Cleanup backlog (non-blocking):** drop unused `motor`/`pymongo` pins from
`backend/requirements.txt`; the `convex/bridge.ts` + `backend/convex_bridge.py`
write-through bridge is now unused and can be removed.

---

## Re-clone from 1-convex-data (2026-06, this session)
- Original request: "clone this mobile app: https://github.com/abufuadeid419-glitch/1-convex-data.git"
- User choices: restore as-is with Convex (deployment fearless-ostrich-878); developer email must not be
  in the codebase; make sure every app action is connected to the Convex database.
- Done:
  - Repo copied into /app; yarn install. Convex functions deployed (`npx convex dev --once`).
  - Env: frontend/.env EXPO_PUBLIC_CONVEX_URL + EXPO_PUBLIC_CONVEX_SITE_URL; backend/.env CONVEX_URL +
    CONVEX_DEPLOY_KEY (secret, server-side only). src/api.ts now calls EXPO_PUBLIC_CONVEX_SITE_URL/api
    so ALL REST calls go to the Convex HTTP router (verified every frontend path maps to convex/http.ts).
  - Developer account: Convex deployment env var DEVELOPER_EMAILS (set via `npx convex env set`), checked
    in convex/edge.ts createSession on each Google sign-in. Removed "first user becomes developer".
  - Security: edge:createSession, migrate:*, bridge:* made internal (not publicly callable).
  - Restored missing app/dist/ distributor route group (was dropped because .gitignore ignored `dist/`);
    added gitignore negations so it is committed now.
- Testing: iteration_12 — 74/74 backend (Convex REST + security), owner/acct/dev screens OK; agent
  route fixed after report and verified by screenshot.
- Backlog: drop unused motor/pymongo pins; remove obsolete bridge files; resizeMode deprecation warning.

## Features batch (2026-06): logo, weekly digest, Pro upsell, sync badge
- Logo on invoices: already implemented via Convex file storage (edge.ts uploadLogo/getLogo); receipts
  (invoice/statement/collection/payment/warehouse-return) embed it. Verified end-to-end.
- Weekly debt digest: convex/crons.ts runs internal extra:weeklyDebtDigestAll every Sunday 06:00 UTC for
  all active orgs (shared runDigest helper; /debts/digest still available to staff).
- Pro upsell: TRIAL orgs get daily reports only (week/month return {locked:true}); src/components/ProUpsell.tsx
  shown on /reports and owner team tab; invite-limit error mentions Pro; "Pro" plan (25 USD/30d, 15 staff)
  created in Convex plans; uses existing manual-payment upgrade request + developer approval flow.
- Live connection badge: src/components/SyncBadge.tsx in AccountButton header (all roles) — Convex websocket
  state + NetInfo + offline queue (synced / syncing N / offline).
- Testing: iteration_13 — 87 backend pass; frontend verified (TRIAL branch by code review).

## Features batch (2026-06): yearly Pro, trial countdown, sales targets
- Yearly Pro: Pro plan yearly_price 250 (vs 25/mo). /upgrade defaults to yearly, shows save %, "2 أشهر مجاناً"
  badge and monthly-equivalent price; yearly request = 365 days.
- Trial countdown: src/components/TrialCountdown.tsx on owner home — TRIAL ≤5 days / paid ≤7 days, days+hours,
  ticks every minute, upgrade CTA.
- Sales targets: convex/targets.ts (list/set) + sales_targets table; live via Convex useQuery in
  src/components/SalesTargets.tsx (owner/acct card with edit sheet; agent "هدفي" card with daily pace);
  REST GET/PUT /api/targets; agent notified on new target.
- Testing: iteration_14 — 20/20 backend, frontend verified (trial banner TRIAL path by code review).

## Auth switch (2026-06): phone number + SMS OTP via Bird Verify (Google removed)
- convex/edge.ts: requestOtp / verifyOtp actions (Bird eu1 /v1/verify/verifications[/check]), per-phone rate
  limit (5 sends/hour), 5 verify attempts per code, internal createSession (30-day token), setName,
  internal seedTestAccounts (CLI-only). Routes: POST /auth/otp/request, /auth/otp/verify, /auth/name.
- Secrets in Convex env: BIRD_API_KEY, BIRD_BASE_URL, DEVELOPER_PHONES (+963 dev phone). Not in code.
- Users: `phone` field + by_phone index; `email` holds phone as display id. otp_requests table.
- Frontend: app/login.tsx PhoneAuth (phone → code → name), default +963; src/auth.tsx requestOtp/verifyOtp/saveName;
  homeFor routes users without a name to /login.
- Security: removed public auth:syncSession (anyone could mint sessions/roles) + ConvexSessionSync.
- Data: all previous accounts/data wiped per user (backup: /app/backups/convex_before_phone_login.zip).
- Testing: iteration_15 — 21/21 backend, frontend OK.
- UPDATE: Bird key has no `verify` scope but has `sms:write` → switched to self-managed OTP over Bird SMS API
  (POST /v1/sms/messages, category "authentication"). 6-digit code from crypto.getRandomValues, stored only as
  sha256(OTP_PEPPER:phone:code) in otp_requests, 10-min expiry, 5 attempts, 30s resend cooldown, 5 sends/hour.
  Convex env OTP_PEPPER added. Verified Bird accepts the key (422 on invalid recipient, not 403).

- FIX (2026-06): SMS failed with Bird E12020 SMSDestinationNotEnabled (Syria not enabled in Bird workspace). Failed sends now dropped (no cooldown/limit consumed), clear Arabic error; login strips duplicated country code / 00 prefix.

- WhatsApp fallback (2026-06): requestOtp sends SMS; on SMS failure (except invalid recipient) or channel:"whatsapp" it sends Bird WhatsApp template (POST /v1/whatsapp/messages, env BIRD_WHATSAPP_TEMPLATE default bird_otp, BIRD_WHATSAPP_LANGUAGE default en). Returns {channel}. Login shows channel + "أرسله عبر واتساب" button. BLOCKER: key lacks whatsapp:write scope; Syria SMS destination not enabled.
