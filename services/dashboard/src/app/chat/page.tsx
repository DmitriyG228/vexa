"use client";

import { Suspense } from "react";
import { EiChat } from "@/components/workspace/ei-chat";

export default function ChatPage() {
  return (
    <div className="h-[calc(100vh-64px)]">
      <Suspense>
        <EiChat />
      </Suspense>
    </div>
  );
}
