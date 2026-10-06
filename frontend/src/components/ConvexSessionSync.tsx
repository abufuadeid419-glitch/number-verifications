import { useMutation } from "convex/react";
import { useEffect, useRef } from "react";

import { api as cxApi } from "@/convex/_generated/api";
import { useAuth } from "@/src/auth";

// Mirrors the already-authenticated FastAPI session into Convex app-wide (once
// per token/role/org change) so every Convex-backed screen — the real-time
// notification bell, the live agent map, routes/vouchers/purchases — can
// authenticate with the SAME bearer token the app already holds. See
// convex/auth.ts. This does NOT change authentication; Google OAuth + FastAPI
// still issue the token.
export function ConvexSessionSync() {
  const { token, user } = useAuth();
  const sync = useMutation(cxApi.auth.syncSession);
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    if (!token || !user) return;
    const key = `${token}:${user.role ?? ""}:${user.employee_type ?? ""}:${user.org_id ?? ""}`;
    if (lastKey.current === key) return;
    lastKey.current = key;
    sync({
      token,
      user: {
        user_id: user.user_id,
        email: user.email,
        name: user.name ?? null,
        picture: user.picture ?? null,
        role: user.role ?? null,
        employee_type: user.employee_type ?? null,
        org_id: user.org_id ?? null,
      },
      org: user.org ?? null,
    }).catch(() => {});
  }, [token, user, sync]);

  return null;
}
