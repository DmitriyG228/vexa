"use client";

import { Suspense } from "react";
import { WorkspaceEditor } from "@/components/workspace/workspace-editor";

export default function WorkspacePage() {
  return (
    <div className="h-[calc(100vh-64px)]">
      <Suspense>
        <WorkspaceEditor />
      </Suspense>
    </div>
  );
}
