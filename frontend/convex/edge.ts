import { v } from "convex/values";

import { action, internalMutation, mutation } from "./_generated/server";
import { api, internal } from "./_generated/api";
import { clean, newId, nowIso, orgById } from "./lib";

const SESSION_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data";

// Internal: create/refresh the user + session from verified OAuth data (mirror auth_session).
export const createSession = internalMutation({
  args: { email: v.string(), name: v.optional(v.union(v.string(), v.null())), picture: v.optional(v.union(v.string(), v.null())), session_token: v.string() },
  handler: async (ctx, { email: rawEmail, name, picture, session_token }) => {
    const email = rawEmail.toLowerCase();
    // Developer accounts come ONLY from the Convex deployment env var DEVELOPER_EMAILS
    // (set via `npx convex env set`), never from the codebase.
    const devEmails = (process.env.DEVELOPER_EMAILS ?? "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
    const isDev = devEmails.includes(email);
    const existing = await ctx.db.query("users").withIndex("by_email", (q) => q.eq("email", email)).unique();
    let user_id: string;
    if (existing) {
      user_id = existing.user_id;
      await ctx.db.patch(existing._id, { name: name ?? null, picture: picture ?? null, ...(isDev && existing.role !== "DEVELOPER" ? { role: "DEVELOPER", employee_type: null, org_id: null } : {}) });
    } else {
      user_id = `user_${newId().slice(0, 12)}`;
      const role = isDev ? "DEVELOPER" : null;
      await ctx.db.insert("users", { user_id, email, name: name ?? null, picture: picture ?? null, role, employee_type: null, org_id: null, consent_at: null, created_at: nowIso() });
    }
    await ctx.db.insert("user_sessions", { session_token, user_id, expires_at: new Date(Date.now() + 7 * 86400000).toISOString(), created_at: nowIso() });
    const user: any = await ctx.db.query("users").withIndex("by_user_id", (q) => q.eq("user_id", user_id)).unique();
    const out: any = { ...clean(user), org: null };
    if (user.org_id) out.org = clean(await orgById(ctx, user.org_id));
    return { session_token, user: out };
  },
});

// POST /api/auth/session — exchange an Emergent OAuth session_id for our app session.
export const exchangeSession = action({
  args: { session_id: v.string() },
  handler: async (ctx, { session_id }): Promise<any> => {
    const r = await fetch(SESSION_URL, { headers: { "X-Session-ID": session_id } });
    if (!r.ok) throw new Error("فشل تسجيل الدخول");
    const data: any = await r.json();
    return await ctx.runMutation(internal.edge.createSession, { email: data.email, name: data.name ?? null, picture: data.picture ?? null, session_token: data.session_token });
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
