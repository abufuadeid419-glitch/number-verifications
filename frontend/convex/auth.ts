import { v } from "convex/values";

import { mutation } from "./_generated/server";
import { nowIso } from "./lib";

// Mirrors the ALREADY-authenticated FastAPI/Mongo session into Convex so Convex
// functions can validate the same bearer token the app already holds. This does
// NOT create or change authentication — Google OAuth + FastAPI still issue the
// token; we only copy the session/user/org rows so Convex `getUser` can look the
// token up. Called by the app once the user is logged in.
export const syncSession = mutation({
  args: {
    token: v.string(),
    user: v.object({
      user_id: v.string(),
      email: v.string(),
      name: v.optional(v.union(v.string(), v.null())),
      picture: v.optional(v.union(v.string(), v.null())),
      role: v.optional(v.union(v.string(), v.null())),
      employee_type: v.optional(v.union(v.string(), v.null())),
      org_id: v.optional(v.union(v.string(), v.null())),
    }),
    org: v.optional(v.union(v.any(), v.null())),
  },
  handler: async (ctx, { token, user, org }) => {
    const uDoc = {
      user_id: user.user_id,
      email: user.email,
      name: user.name ?? null,
      picture: user.picture ?? null,
      role: user.role ?? null,
      employee_type: user.employee_type ?? null,
      org_id: user.org_id ?? null,
    };
    const existingUser = await ctx.db
      .query("users")
      .withIndex("by_user_id", (q) => q.eq("user_id", user.user_id))
      .unique();
    if (existingUser) await ctx.db.patch(existingUser._id, uDoc);
    else await ctx.db.insert("users", { ...uDoc, created_at: nowIso() });

    if (org && org.id) {
      const { _id, _creationTime, ...orgRest } = org;
      const existingOrg = await ctx.db
        .query("organizations")
        .withIndex("by_biz_id", (q) => q.eq("id", org.id))
        .unique();
      if (existingOrg) await ctx.db.patch(existingOrg._id, orgRest);
      else await ctx.db.insert("organizations", orgRest);
    }

    const sDoc = {
      session_token: token,
      user_id: user.user_id,
      expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
    };
    const existingSess = await ctx.db
      .query("user_sessions")
      .withIndex("by_token", (q) => q.eq("session_token", token))
      .unique();
    if (existingSess) await ctx.db.patch(existingSess._id, sDoc);
    else await ctx.db.insert("user_sessions", { ...sDoc, created_at: nowIso() });

    return { ok: true };
  },
});
