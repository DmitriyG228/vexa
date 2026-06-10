import type { Metadata } from "next";
import { ProposalsView } from "@/components/proposals/proposals-view";

export const metadata: Metadata = {
  title: "Knowledge Proposals",
  description:
    "Review and approve the agent's proposed updates to your organization's knowledge base.",
};

export default function ProposalsPage() {
  return <ProposalsView />;
}
