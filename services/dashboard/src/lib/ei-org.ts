/**
 * Server-side EI org resolution for the proposals proxy (issue #25).
 *
 * Tenancy model: the org a user may see is derived ONLY from their
 * authenticated session — resolve the user via the existing dashboard auth
 * (cookie -> admin-api lookup), then read the org from the user's EI config
 * (`user.data.ei`), exactly mirroring agent-api's resolve_ei_config()
 * (services/agent-api/agent_api/lineage.py): org_id = ei.org_id or
 * `user-<id>`, sanitized the same way; EI disabled (default) -> null.
 *
 * The client NEVER supplies the org. Server-only module.
 */

import { cookies } from "next/headers";
import { getAuthCookieName, getUserInfoCookieName } from "@/lib/auth-cookies";
import { sanitizeOrgId } from "@/lib/proposals";

export type EiOrgResult =
  | { status: "unauthenticated" }
  | { status: "disabled" }
  | { status: "ok"; org: string };

/**
 * Resolve the authenticated user's EI org.
 *
 * - no/invalid session cookie -> unauthenticated
 * - user found but EI not enabled (default OFF) -> disabled
 * - EI enabled -> the sanitized org id
 */
export async function getAuthenticatedEiOrg(): Promise<EiOrgResult> {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL;
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_ADMIN_API_URL || !VEXA_ADMIN_API_KEY || !VEXA_API_URL) {
    return { status: "unauthenticated" };
  }

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) return { status: "unauthenticated" };

  // Validate the token against the gateway (same pattern as auth-utils).
  const verifyRes = await fetch(`${VEXA_API_URL}/meetings`, {
    headers: { "X-API-Key": token },
  });
  if (!verifyRes.ok) return { status: "unauthenticated" };

  const userInfoStr = cookieStore.get(getUserInfoCookieName())?.value;
  if (!userInfoStr) return { status: "unauthenticated" };
  let email: string;
  try {
    email = JSON.parse(userInfoStr).email;
    if (!email) return { status: "unauthenticated" };
  } catch {
    return { status: "unauthenticated" };
  }

  let user: { id?: number | string; data?: Record<string, unknown> };
  try {
    const res = await fetch(
      `${VEXA_ADMIN_API_URL}/admin/users/email/${encodeURIComponent(email)}`,
      { headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY }, cache: "no-store" }
    );
    if (!res.ok) return { status: "unauthenticated" };
    user = await res.json();
  } catch {
    return { status: "unauthenticated" };
  }
  if (user.id == null) return { status: "unauthenticated" };

  const ei = (user.data?.ei ?? null) as { enabled?: boolean; org_id?: string } | null;
  if (!ei || !ei.enabled) return { status: "disabled" };

  const org = sanitizeOrgId(ei.org_id || `user-${user.id}`);
  if (!org) return { status: "disabled" };
  return { status: "ok", org };
}
