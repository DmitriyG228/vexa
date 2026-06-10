"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Loader2, X, FolderOpen, FileText } from "lucide-react";
import { WikiMarkdown, FileIndex } from "./wiki-markdown";

/**
 * Contextual side panel rendering one workspace file (Claude-Desktop-style
 * right panel). Wikilinks inside the file navigate within the panel.
 */
export function FilePanel({
  path,
  fileIndex,
  onNavigate,
  onClose,
}: {
  path: string;
  fileIndex: FileIndex;
  onNavigate: (path: string) => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`/api/workspace-ei/file?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        if (alive) setContent(d.content || "");
      })
      .catch(() => {
        if (alive) setContent("*Could not load file.*");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [path]);

  return (
    <div className="flex flex-col h-full bg-background">
      <div className="flex items-center gap-2 px-3 py-2 border-b">
        <FileText className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        <span className="text-xs font-mono truncate flex-1" title={path}>
          {path}
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs gap-1"
          onClick={() => router.push(`/workspace?file=${encodeURIComponent(path)}`)}
        >
          <FolderOpen className="h-3 w-3" />
          Workspace
        </Button>
        <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-4 text-sm">
        {loading ? (
          <div className="flex items-center justify-center h-32">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <WikiMarkdown text={content} fileIndex={fileIndex} onOpenFile={onNavigate} />
        )}
      </div>
    </div>
  );
}
