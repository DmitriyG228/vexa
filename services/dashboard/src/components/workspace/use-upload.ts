"use client";

import { useCallback, useState } from "react";

export type UploadResult = { uploaded: string[]; commit: string | null };

/** Upload files into the org workspace under `dir`. Returns committed paths. */
export function useWorkspaceUpload(dir: string) {
  const [uploading, setUploading] = useState(false);

  const upload = useCallback(
    async (files: FileList | File[]): Promise<UploadResult | null> => {
      const list = Array.from(files);
      if (list.length === 0) return null;
      setUploading(true);
      try {
        const fd = new FormData();
        for (const f of list) fd.append("files", f, f.name);
        const resp = await fetch(
          `/api/workspace-ei/upload?dir=${encodeURIComponent(dir)}`,
          { method: "POST", body: fd }
        );
        if (!resp.ok) return null;
        return (await resp.json()) as UploadResult;
      } catch {
        return null;
      } finally {
        setUploading(false);
      }
    },
    [dir]
  );

  return { upload, uploading };
}
