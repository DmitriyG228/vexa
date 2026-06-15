"use client";

import { Calendar, FileText } from "lucide-react";

export interface MentionOption {
  kind: "meeting" | "file";
  value: string; // meeting id (as string) or file path
  label: string;
  sub?: string; // status (meeting) or path hint (file)
}

/**
 * Typeahead list for @-mentions in the chat composer. Presentational only — the
 * parent owns @-detection, filtering, keyboard nav (activeIndex), and insertion.
 * Anchored above the input. onMouseDown (not onClick) so the textarea keeps focus.
 */
export function MentionMenu({
  options,
  activeIndex,
  onPick,
  onHover,
}: {
  options: MentionOption[];
  activeIndex: number;
  onPick: (o: MentionOption) => void;
  onHover: (i: number) => void;
}) {
  if (options.length === 0) return null;
  return (
    <div className="absolute bottom-full left-0 mb-1 z-40 w-80 max-w-[90vw] overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-md">
      <div className="max-h-64 overflow-y-auto py-1">
        {options.map((o, i) => (
          <button
            key={`${o.kind}:${o.value}`}
            type="button"
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(o);
            }}
            onMouseEnter={() => onHover(i)}
            className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
              i === activeIndex ? "bg-muted" : ""
            }`}
          >
            {o.kind === "meeting" ? (
              <Calendar className="h-3.5 w-3.5 shrink-0 opacity-70" />
            ) : (
              <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" />
            )}
            <span className="truncate">{o.label}</span>
            {o.sub && (
              <span className="ml-auto shrink-0 truncate text-xs text-muted-foreground">
                {o.sub}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
