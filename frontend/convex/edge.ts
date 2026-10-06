import { v } from "convex/values";

import { action, internalMutation, mutation } from "./_generated/server";
import { api, internal } from "./_generated/api";
import { clean, getUser, newId, nowIso, orgById } from "./lib";

const PHONE_RE = /^\+[1-9]\d{7,14}$/;
const OTP_MAX_PER_HOUR = 5;
const VERIFY_MAX_ATTEMPTS = 5;

function birdHeaders() {
  const key = process.env.BIRD_API_KEY;
  if (!key || !process.env.BIRD_BASE_URL) throw new Error("خدمة الرسائل غير مهيأة");
  return { Authorization: `Bearer ${key}`, "content-type": "application/json" };
}

function birdError(status: number) {
  if (status === 429) return "محاولات كثيرة، يرجى الانتظار قليلاً ثم المحاولة مجدداً";
  if (status === 400 || status === 422) return "رقم الهاتف غير صالح أو غير مدعوم";
  return "تعذر إرسال رمز التحقق، حاول لاحقاً";
}

// Internal: per-phone rate limit for OTP sends (rolling hour).
export const noteOtpRequest = internalMutation({
  args: { phone: v.string() },
  handler: async (ctx, { phone }) => {
    const hourAgo = Date.now() - 3600000;
    const rows = await ctx.db.query("otp_requests").withIndex("by_phone", (q) => q.eq("phone", phone)).collect();
    for (const r of rows) if (r.created_ms < hourAgo) await ctx.db.delete(r._id);
    const recent = rows.filter((r) => r.created_ms >= hourAgo);
    if (recent.length >= OTP_MAX_PER_HOUR) throw new Error("تم تجاوز عدد مرات الإرسال، حاول بعد ساعة");
    await ctx.db.insert("otp_requests", { phone, created_ms: Date.now(), attempts: 0 });
  },
});

// Internal: count a verify attempt against the latest OTP request; blocks brute force.
export const noteVerifyAttempt = internalMutation({
  args: { phone: v.string() },
  handler: async (ctx, { phone }) => {
    const rows = await ctx.db.query("otp_requests").withIndex("by_phone", (q) => q.eq("phone", phone)).collect();
    const last = rows.sort((x, y) => y.created_ms - x.created_ms)[0];
    if (!last) throw new Error("اطلب رمز تحقق أولاً");
    if ((last.attempts ?? 0) >= VERIFY_MAX_ATTEMPTS) throw new Error("محاولات خاطئة كثيرة، اطلب رمزاً جديداً");
    await ctx.db.patch(last._id, { attempts: (last.attempts ?? 0) + 1 });
  },
});

// POST /api/auth/otp/request — Bird Verify sends an SMS code to the phone.
export const requestOtp = action({
  args: { phone: v.string() },
  handler: async (ctx, { phone: raw }) => {
    const phone = raw.trim();
    if (!PHONE_RE.test(phone)) throw new Error("أدخل رقم هاتف صحيح مع رمز الدولة");
    await ctx.runMutation(internal.edge.noteOtpRequest, { phone });
    const r = await fetch(`${process.env.BIRD_BASE_URL}/v1/verify/verifications`, {
      method: "POST",
      headers: birdHeaders(),
      body: JSON.stringify({ to: { phone_number: phone }, options: { code_length: 6, channels: ["sms"] } }),
    });
    if (!r.ok) {
      console.error("bird send failed", r.status, (await r.text()).slice(0, 300));
      throw new Error(birdError(r.status));
    }
    return { ok: true };
  },
});

// POST /api/auth/otp/verify — Bird checks the code; on success we issue our own session.
export const verifyOtp = action({
  args: { phone: v.string(), code: v.string() },
  handler: async (ctx, { phone: raw, code }): Promise<any> => {
    const phone = raw.trim();
    if (!PHONE_RE.test(phone) || !/^\d{4,10}$/.test(code.trim())) throw new Error("رمز التحقق غير صحيح");
    await ctx.runMutation(internal.edge.noteVerifyAttempt, { phone });
    const r = await fetch(`${process.env.BIRD_BASE_URL}/v1/verify/verifications/check`, {
      method: "POST",
      headers: birdHeaders(),
      body: JSON.stringify({ to: { phone_number: phone }, code: code.trim() }),
    });
    const body: any = await r.json().catch(() => ({}));
    if (!r.ok) {
      console.error("bird check failed", r.status, JSON.stringify(body).slice(0, 300));
      throw new Error(r.status === 429 ? birdError(429) : "رمز التحقق غير صحيح أو منتهي");
    }
    if (body.success !== true) throw new Error(body.reason === "expired" ? "انتهت صلاحية الرمز، اطلب رمزاً جديداً" : "رمز التحقق غير صحيح");
    const session_token = `${crypto.randomUUID()}${crypto.randomUUID()}`.replace(/-/g, "");
    return await ctx.runMutation(internal.edge.createSession, { phone, session_token });
  },
});

// Internal: find-or-create the user by verified phone and open a 30-day session.
export const createSession = internalMutation({
  args: { phone: v.string(), session_token: v.string() },
  handler: async (ctx, { phone, session_token }) => {
    // Developer accounts come ONLY from the Convex deployment env var DEVELOPER_PHONES.
    const devPhones = (process.env.DEVELOPER_PHONES ?? "").split(",").map((p) => p.trim()).filter(Boolean);
    const isDev = devPhones.includes(phone);
    const existing = await ctx.db.query("users").withIndex("by_phone", (q) => q.eq("phone", phone)).unique();
    let user_id: string;
    if (existing) {
      user_id = existing.user_id;
      if (isDev && existing.role !== "DEVELOPER") await ctx.db.patch(existing._id, { role: "DEVELOPER", employee_type: null, org_id: null });
    } else {
      user_id = `user_${newId().slice(0, 12)}`;
      // `email` doubles as the display identifier across the app; for phone accounts it holds the phone.
      await ctx.db.insert("users", { user_id, phone, email: phone, name: null, picture: null, role: isDev ? "DEVELOPER" : null, employee_type: null, org_id: null, consent_at: null, created_at: nowIso() });
    }
    // Used OTP rows are no longer needed.
    for (const o of await ctx.db.query("otp_requests").withIndex("by_phone", (q) => q.eq("phone", phone)).collect()) await ctx.db.delete(o._id);
    await ctx.db.insert("user_sessions", { session_token, user_id, expires_at: new Date(Date.now() + 30 * 86400000).toISOString(), created_at: nowIso() });
    const user: any = await ctx.db.query("users").withIndex("by_user_id", (q) => q.eq("user_id", user_id)).unique();
    const out: any = { ...clean(user), org: null };
    if (user.org_id) out.org = clean(await orgById(ctx, user.org_id));
    return { session_token, user: out };
  },
});

// POST /api/auth/name — new phone accounts set their display name once.
export const setName = mutation({
  args: { token: v.string(), name: v.string() },
  handler: async (ctx, { token, name }) => {
    const user: any = await getUser(ctx, token);
    const n = name.trim();
    if (n.length < 2 || n.length > 60) throw new Error("أدخل اسماً صحيحاً");
    await ctx.db.patch(user._id, { name: n });
    return { ok: true, name: n };
  },
});

// Internal (CLI only, `npx convex run edge:seedTestAccounts`): seeded org + bearer sessions for automated tests.
export const seedTestAccounts = internalMutation({
  args: {},
  handler: async (ctx) => {
    const org_id = "org_test_1";
    if (!(await orgById(ctx, org_id))) {
      await ctx.db.insert("organizations", { id: org_id, name: "TEST مؤسسة الاختبار", owner_id: "user_test_owner", plan: "LICENSE", status: "ACTIVE", max_employees: 15, expires_at: new Date(Date.now() + 365 * 86400000).toISOString(), created_at: nowIso() } as any);
    }
    const people = [
      ["user_test_dev", "+963900000001", "مطور الاختبار", "DEVELOPER", null, null, "test_token_dev"],
      ["user_test_owner", "+963900000002", "مالك الاختبار", "OWNER", null, org_id, "test_token_owner"],
      ["user_test_acct", "+963900000003", "محاسب الاختبار", "EMPLOYEE", "ACCOUNTANT", org_id, "test_token_acct"],
      ["user_test_agent", "+963900000004", "موزع الاختبار", "EMPLOYEE", "FIELD_AGENT", org_id, "test_token_agent"],
    ] as const;
    for (const [user_id, phone, name, role, employee_type, oid, token] of people) {
      const ex = await ctx.db.query("users").withIndex("by_user_id", (q) => q.eq("user_id", user_id)).unique();
      const doc = { user_id, phone, email: phone, name, picture: null, role, employee_type, org_id: oid, consent_at: nowIso() };
      if (ex) await ctx.db.patch(ex._id, doc);
      else await ctx.db.insert("users", { ...doc, created_at: nowIso() });
      const s = await ctx.db.query("user_sessions").withIndex("by_token", (q) => q.eq("session_token", token)).unique();
      if (!s) await ctx.db.insert("user_sessions", { session_token: token, user_id, expires_at: new Date(Date.now() + 365 * 86400000).toISOString(), created_at: nowIso() });
    }
    return { ok: true };
  },
});

// Internal: stamp the org with its logo storage id.
export const setLogo = mutation({
  args: { token: v.string(), storageId: v.string(), content_type: v.string() },
  handler: async (ctx, { token, storageId, content_type }) => {
    const sess = await ctx.db.query("user_sessions").withIndex("by_token", (q) => q.eq("session_token", token)).unique();
    if (!sess) throw new Error("الجلسة غير صالحة");
    const user: any = await ctx.db.query("users").withIndex("by_user_id", (q) => q.eq("user_id", sess.user_id)).unique();
    if (!user || user.role !== "OWNER") throw new Error("ليس لديك صلاحية");
    const org: any = await orgById(ctx, user.org_id);
    await ctx.db.patch(org._id, { logo_storage: storageId, logo_type: content_type });
    return { ok: true };
  },
});

export const getLogoId = mutation({
  args: { token: v.string() },
  handler: async (ctx, { token }) => {
    const sess = await ctx.db.query("user_sessions").withIndex("by_token", (q) => q.eq("session_token", token)).unique();
    if (!sess) throw new Error("الجلسة غير صالحة");
    const user: any = await ctx.db.query("users").withIndex("by_user_id", (q) => q.eq("user_id", sess.user_id)).unique();
    const org: any = user?.org_id ? await orgById(ctx, user.org_id) : null;
    return { storage: org?.logo_storage ?? null, type: org?.logo_type ?? "image/jpeg" };
  },
});

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// POST /api/org/logo — store the base64 logo in Convex file storage.
export const uploadLogo = action({
  args: { token: v.string(), data: v.string(), content_type: v.optional(v.string()) },
  handler: async (ctx, { token, data, content_type }): Promise<any> => {
    const ct = content_type ?? "image/jpeg";
    const bytes = b64ToBytes(data.split(",").pop() as string);
    if (bytes.length > 1_500_000) throw new Error("حجم الشعار كبير جداً (الحد 1.5MB)");
    const storageId = await ctx.storage.store(new Blob([bytes], { type: ct }));
    await ctx.runMutation(api.edge.setLogo, { token, storageId, content_type: ct });
    return { ok: true };
  },
});

// GET /api/org/logo — return the logo as a data URI (keeps the existing frontend contract).
export const getLogo = action({
  args: { token: v.string() },
  handler: async (ctx, { token }): Promise<any> => {
    const info: any = await ctx.runMutation(api.edge.getLogoId, { token });
    if (!info.storage) return { data_uri: null };
    const blob = await ctx.storage.get(info.storage);
    if (!blob) return { data_uri: null };
    const buf = new Uint8Array(await blob.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
    return { data_uri: `data:${info.type};base64,${btoa(bin)}` };
  },
});
