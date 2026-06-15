"use client";

import { useState, useEffect, useCallback } from "react";

export interface MeetingItem {
  id: number | string;
  platform: string;
  native_meeting_id?: string;
  title: string;
  status: string;
}

// Statuses that mean a bot is currently in/attaching to a meeting (live).
const ACTIVE_STATUSES = new Set([
  "active",
  "joining",
  "requested",
  "awaiting_admission",
  "admitted",
  "up",
  "running",
  "needs_human_help",
]);

export function isActiveStatus(s?: string): boolean {
  return !!s && ACTIVE_STATUSES.has(s.toLowerCase());
}

function meetingTitle(m: Record<string, unknown>): string {
  const data = (m.data as Record<string, unknown>) || {};
  return (
    (m.title as string) ||
    (data.title as string) ||
    (data.name as string) ||
    (m.native_meeting_id as string) ||
    (m.platform_specific_id as string) ||
    `Meeting ${m.id}`
  );
}

/**
 * The user's meetings (live + recent) from the gateway, via /api/vexa/meetings.
 * Polls so the live pill appears/disappears as bots join/leave. `active` are the
 * in-progress ones; the full list feeds the @-mention search.
 */
export function useMeetings(pollMs = 15000) {
  const [meetings, setMeetings] = useState<MeetingItem[]>([]);

  const load = useCallback(async () => {
    try {
      const resp = await fetch("/api/vexa/meetings?limit=50");
      if (!resp.ok) return;
      const data = await resp.json();
      const list: MeetingItem[] = (data.meetings || []).map(
        (m: Record<string, unknown>) => ({
          id: m.id as number,
          platform: (m.platform as string) || "",
          native_meeting_id:
            (m.platform_specific_id as string) || (m.native_meeting_id as string) || "",
          title: meetingTitle(m),
          status: (m.status as string) || "",
        })
      );
      setMeetings(list);
    } catch {
      /* best-effort; keep last list */
    }
  }, []);

  useEffect(() => {
    load();
    if (!pollMs) return;
    const t = setInterval(load, pollMs);
    return () => clearInterval(t);
  }, [load, pollMs]);

  const active = meetings.filter((m) => isActiveStatus(m.status));
  return { meetings, active, reload: load };
}
