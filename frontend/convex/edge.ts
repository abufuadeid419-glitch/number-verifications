import { v } from "convex/values";

import { action, internalMutation, mutation } from "./_generated/server";
import { api, internal } from "./_generated/api";
import { clean, getUser, newId, nowIso, orgById } from "./lib";

const PHONE_RE = /^\+[1-9]\d{7,14}$/;
const OTP_MAX_PER_HOUR = 5;
const VERIFY_MAX_ATTEMPTS = 5;

const OTP_TTL_MS = 10 * 60 * 1000;

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

// Codes are never stored in plain text: sha256(pepper:phone:code).
async function hashCode(phone: string, code: string) {
  const data = new TextEncoder().encode(`${process.env.OTP_PEPPER ?? ""}:${phone}:${code}`);
  const d = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(d)).map((x) => x.toString(16).padStart(2, "0")).join("");
}

function randomCode() {
  const n = new Uint32Array(1);
  crypto.getRandomValues(n);
  return String(n[0] % 1000000).padStart(6, "0");
}

// Internal: per-phone rate limit (rolling hour) + store the hashed code for this send.
export const storeOtp = internalMutation({
  args: { phone: v.string(), code_hash: v.string() },
  handler: async (ctx, { phone, code_hash }) => {
    const hourAgo = Date.now() - 3600000;
    const rows = await ctx.db.query("otp_requests").withIndex("by_phone", (q) => q.eq("phone", phone)).collect();
    for (const r of rows) if (r.created_ms < hourAgo) await ctx.db.delete(r._id);
    const recent = rows.filter((r) => r.created_ms >= hourAgo);
    if (recent.length >= OTP_MAX_PER_HOUR) throw new Error("تم تجاوز عدد مرات الإرسال، حاول بعد ساعة");
    const last = recent.sort((x, y) => y.created_ms - x.created_ms)[0];
    if (last && Date.now() - last.created_ms < 30000) throw new Error("انتظر قليلاً قبل طلب رمز جديد");
    return await ctx.db.insert("otp_requests", { phone, created_ms: Date.now(), attempts: 0, code_hash, expires_ms: Date.now() + OTP_TTL_MS });
  },
});

// Internal: a send that Bird rejected must not count toward cooldown / hourly limit.
export const dropOtp = internalMutation({
  args: { id: v.id("otp_requests") },
  handler: async (ctx, { id }) => {
    if (await ctx.db.get(id)) await ctx.db.delete(id);
  },
});

async function sendSms(phone: string, code: string) {
  const r = await fetch(`${process.env.BIRD_BASE_URL}/v1/sms/messages`, {
    method: "POST",
    headers: birdHeaders(),
    body: JSON.stringify({ to: phone, text: `رمز التحقق في النظام الذكي: ${code}\nصالح لمدة 10 دقائق. لا تشاركه مع أحد.`, category: "authentication" }),
  });
  return { ok: r.ok, status: r.status, body: r.ok ? "" : await r.text() };
}

// WhatsApp authentication template (Bird-managed `bird_otp` by default; override via Convex env).
async function sendWhatsApp(phone: string, code: string) {
  const r = await fetch(`${process.env.BIRD_BASE_URL}/v1/whatsapp/messages`, {
    method: "POST",
    headers: birdHeaders(),
    body: JSON.stringify({
      to: phone,
      template: {
        slug: process.env.BIRD_WHATSAPP_TEMPLATE || "bird_otp",
        language: process.env.BIRD_WHATSAPP_LANGUAGE || "en",
        components: [{ type: "body", parameters: [{ type: "text", text: code }] }],
      },
    }),
  });
  return { ok: r.ok, status: r.status, body: r.ok ? "" : await r.text() };
}

const isBadRecipient = (body: string) => /SMSInvalidRecipient|E12087|InvalidRecipient/.test(body);

// POST /api/auth/otp/request — send the code by SMS; if SMS can't reach the number, fall back to WhatsApp.
// `channel: "whatsapp"` lets the user explicitly ask for WhatsApp (e.g. SMS never arrived).
export const requestOtp = action({
  args: { phone: v.string(), channel: v.optional(v.string()) },
  handler: async (ctx, { phone: raw, channel }) => {
    const phone = raw.trim();
    if (!PHONE_RE.test(phone)) throw new Error("أدخل رقم هاتف صحيح مع رمز الدولة");
    const code = randomCode();
    const otpId = await ctx.runMutation(internal.edge.storeOtp, { phone, code_hash: await hashCode(phone, code) });
    if (channel !== "whatsapp") {
      const sms = await sendSms(phone, code);
      if (sms.ok) return { ok: true, channel: "sms" };
      console.error("bird sms failed", sms.status, sms.body.slice(0, 300));
      if (isBadRecipient(sms.body)) {
        await ctx.runMutation(internal.edge.dropOtp, { id: otpId });
        throw new Error("رقم الهاتف غير صالح أو غير مدعوم");
      }
    }
    const wa = await sendWhatsApp(phone, code);
    if (wa.ok) return { ok: true, channel: "whatsapp" };
    console.error("bird whatsapp failed", wa.status, wa.body.slice(0, 300));
    await ctx.runMutation(internal.edge.dropOtp, { id: otpId });
    throw new Error(wa.status === 429 ? birdError(429) : "تعذر إرسال رمز التحقق عبر الرسائل أو واتساب، حاول لاحقاً");
  },
});

// POST /api/auth/otp/verify — check the code against the latest request; on success open a session.
export const verifyOtp = action({
  args: { phone: v.string(), code: v.string() },
  handler: async (ctx, { phone: raw, code }): Promise<any> => {
    const phone = raw.trim();
    if (!PHONE_RE.test(phone) || !/^\d{6}$/.test(code.trim())) throw new Error("رمز التحقق غير صحيح");
    const session_token = `${crypto.randomUUID()}${crypto.randomUUID()}`.replace(/-/g, "");
    const res: any = await ctx.runMutation(internal.edge.checkOtp, { phone, code_hash: await hashCode(phone, code.trim()), session_token });
    if (res.error) throw new Error(res.error);
    return res;
  },
});

// Internal: attempt-limited code check; returns an error message (so the attempt count persists) or a session.
export const checkOtp = internalMutation({
  args: { phone: v.string(), code_hash: v.string(), session_token: v.string() },
  handler: async (ctx, { phone, code_hash, session_token }): Promise<any> => {
    const rows = await ctx.db.query("otp_requests").withIndex("by_phone", (q) => q.eq("phone", phone)).collect();
    const last = rows.sort((x, y) => y.created_ms - x.created_ms)[0];
    if (!last || !last.code_hash) return { error: "اطلب رمز تحقق أولاً" };
    if ((last.attempts ?? 0) >= VERIFY_MAX_ATTEMPTS) return { error: "محاولات خاطئة كثيرة، اطلب رمزاً جديداً" };
    if ((last.expires_ms ?? 0) < Date.now()) return { error: "انتهت صلاحية الرمز، اطلب رمزاً جديداً" };
    if (last.code_hash !== code_hash) {
      await ctx.db.patch(last._id, { attempts: (last.attempts ?? 0) + 1 });
      return { error: "رمز التحقق غير صحيح" };
    }
    return await openSession(ctx, phone, session_token);
  },
});

// Find-or-create the user by verified phone and open a 30-day session.
async function openSession(ctx: any, phone: string, session_token: string) {
  {
    // (block kept for indentation parity with the former mutation handler)
    // Developer accounts come ONLY from the Convex deployment env var DEVELOPER_PHONES.
    const devPhones = (process.env.DEVELOPER_PHONES ?? "").split(",").map((p) => p.trim()).filter(Boolean);
    const isDev = devPhones.includes(phone);
    const existing = await ctx.db.query("users").withIndex("by_phone", (q: any) => q.eq("phone", phone)).unique();
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
    for (const o of await ctx.db.query("otp_requests").withIndex("by_phone", (q: any) => q.eq("phone", phone)).collect()) await ctx.db.delete(o._id);
    await ctx.db.insert("user_sessions", { session_token, user_id, expires_at: new Date(Date.now() + 30 * 86400000).toISOString(), created_at: nowIso() });
    const user: any = await ctx.db.query("users").withIndex("by_user_id", (q: any) => q.eq("user_id", user_id)).unique();
    const out: any = { ...clean(user), org: null };
    if (user.org_id) out.org = clean(await orgById(ctx, user.org_id));
    return { session_token, user: out };
  }
}

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
