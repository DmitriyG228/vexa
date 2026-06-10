"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { getDocsUrl, getWebappUrl } from "@/lib/docs/webapp-url";
import {
  Video,
  Plus,
  Settings,
  X,
  Users,
  Shield,
  LogOut,
  Lock,
  Bot,
  BookOpen,
  Zap,
  CreditCard,
  Webhook,
  User,
  Bug,
  GitPullRequest,
  MessagesSquare,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useJoinModalStore } from "@/stores/join-modal-store";
import { useAdminAuthStore } from "@/stores/admin-auth-store";
import { AdminAuthModal } from "@/components/admin/admin-auth-modal";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import { VersionChip } from "@/components/version-chip";
import { withBasePath } from "@/lib/base-path";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

// Primary product modes — rendered as a segmented switcher (Chat | Meetings | Workspace)
const primaryModes = [
  { name: "Chat", href: "/chat", icon: MessagesSquare },
  { name: "Meetings", href: "/meetings", icon: Video },
  { name: "Workspace", href: "/workspace", icon: BookOpen },
];

const navigation = [
  { name: "Proposals", href: "/proposals", icon: GitPullRequest },
  ...(process.env.NEXT_PUBLIC_TRACKER_ENABLED === "true"
    ? [{ name: "Tracker", href: "/tracker", icon: Zap }]
    : []),
];

const adminNavigation = [
  { name: "Users", href: "/admin/users", icon: Users },
  { name: "Bots", href: "/admin/bots", icon: Bot },
  { name: "Settings", href: "/settings", icon: Settings },
];

// IS_HOSTED is determined at runtime via /api/config, not build time

function BillingStatus() {
  const [status, setStatus] = useState<{
    subscription_status: string | null;
    subscription_tier: string | null;
    subscription_trial_end: string | null;
  } | null>(null);

  useEffect(() => {
    fetch(withBasePath("/api/billing/status"))
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => {});
  }, []);

  if (!status || !status.subscription_status) return null;

  const { subscription_status, subscription_tier, subscription_trial_end } =
    status;

  if (subscription_status === "trialing" && subscription_trial_end) {
    const daysLeft = Math.max(
      0,
      Math.ceil(
        (new Date(subscription_trial_end).getTime() - Date.now()) /
          (1000 * 60 * 60 * 24)
      )
    );
    return (
      <div className="px-3 py-1.5">
        <span className="text-xs font-medium text-amber-500">
          Trial: {daysLeft} day{daysLeft !== 1 ? "s" : ""} left
        </span>
      </div>
    );
  }

  if (
    subscription_status === "canceled" ||
    subscription_status === "expired"
  ) {
    return (
      <div className="px-3 py-1.5 flex items-center justify-between">
        <span className="text-xs font-medium text-red-500">Plan expired</span>
        <a
          href={`${getWebappUrl()}/pricing`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-medium text-primary hover:underline"
        >
          Subscribe
        </a>
      </div>
    );
  }

  if (subscription_status === "active") {
    const label = subscription_tier
      ? subscription_tier.charAt(0).toUpperCase() + subscription_tier.slice(1)
      : "Active";
    return (
      <div className="px-3 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">
          {label} plan
        </span>
      </div>
    );
  }

  return null;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  // Contextual sidebar data (per primary mode)
  const [proposalsCount, setProposalsCount] = useState(0);
  const [wsFiles, setWsFiles] = useState<string[]>([]);

  const pathname = usePathname();

  useEffect(() => {
    if (!pathname.startsWith("/workspace") && !pathname.startsWith("/chat")) return;
    fetch("/api/workspace-ei/tree")
      .then((r) => (r.ok ? r.json() : { files: [] }))
      .then((d) =>
        setWsFiles(((d.files as string[]) || []).filter((f) => f.endsWith(".md")).slice(0, 40))
      )
      .catch(() => {});
  }, [pathname]);

  useEffect(() => {
    fetch("/api/proposals")
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setProposalsCount(Array.isArray(d) ? d.length : 0))
      .catch(() => {});
  }, [pathname]);

  const router = useRouter();
  const openJoinModal = useJoinModalStore((state) => state.openModal);
  const { isAdminAuthenticated, logout: adminLogout } = useAdminAuthStore();
  const [showAdminAuthModal, setShowAdminAuthModal] = useState(false);
  const { config } = useRuntimeConfig();
  const isHosted = config?.hostedMode ?? false;

  const handleJoinClick = () => {
    openJoinModal();
    onClose?.();
  };

  const handleAdminClick = (href: string) => {
    if (isAdminAuthenticated) {
      router.push(href);
      onClose?.();
    } else {
      setShowAdminAuthModal(true);
    }
  };

  const handleAdminAuthSuccess = () => {
    // Redirect to admin after successful auth
    router.push("/admin/users");
    onClose?.();
  };

  const handleAdminLogout = () => {
    adminLogout();
  };

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar - fixed on mobile, relative on desktop */}
      <aside
        className={cn(
          // Mobile: fixed, full height, slides in
          "fixed inset-y-0 left-0 z-50 w-64 bg-card border-r border-border",
          "transform transition-transform duration-200 ease-in-out",
          // Desktop: relative, part of flex layout
          "md:relative md:z-0 md:translate-x-0 md:flex md:flex-col md:shrink-0",
          // Mobile visibility
          isOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
      >
        <div className="flex h-full flex-col">
          {/* Mobile header */}
          <div className="flex h-14 items-center justify-between border-b px-4 md:hidden shrink-0">
            <span className="font-semibold">Menu</span>
            <Button variant="ghost" size="icon" onClick={onClose}>
              <X className="h-5 w-5" />
            </Button>
          </div>

          {/* Primary mode switcher (Chat | Meetings | Workspace) */}
          <div className="px-4 pt-4">
            <div className="flex rounded-lg border bg-muted/40 p-1 gap-1">
              {primaryModes.map((m) => {
                const active = pathname.startsWith(m.href);
                return (
                  <Link
                    key={m.name}
                    href={m.href}
                    onClick={onClose}
                    className={cn(
                      "flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
                      active
                        ? "bg-background shadow-sm text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <m.icon className="h-3.5 w-3.5" />
                    {m.name}
                  </Link>
                );
              })}
            </div>
          </div>

          {/* Contextual section for the active mode */}
          <div className="px-4 pt-3">
            {pathname.startsWith("/chat") && (
              <div className="space-y-0.5">
                <button
                  onClick={() => {
                    window.dispatchEvent(new Event("ei-chat-new"));
                    router.push("/chat");
                    onClose?.();
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                >
                  <Plus className="h-4 w-4" />
                  New chat
                </button>
                {wsFiles.filter((f) => f.startsWith("chats/")).length > 0 && (
                  <p className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                    Conversations
                  </p>
                )}
                <div className="max-h-56 overflow-y-auto space-y-0.5">
                  {wsFiles
                    .filter((f) => f.startsWith("chats/"))
                    .map((f) => {
                      const sid = f.slice(6).replace(/\.md$/, "");
                      return (
                        <Link
                          key={f}
                          href={`/chat?session=${encodeURIComponent(sid)}`}
                          onClick={onClose}
                          title={sid}
                          className="block truncate rounded px-3 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                        >
                          {sid}
                        </Link>
                      );
                    })}
                </div>
              </div>
            )}
            {pathname.startsWith("/meetings") && (
              <button
                onClick={handleJoinClick}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              >
                <Plus className="h-4 w-4" />
                Join Meeting
              </button>
            )}
            {pathname.startsWith("/workspace") && wsFiles.length > 0 && (
              <div className="space-y-0.5 max-h-64 overflow-y-auto">
                <p className="px-3 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                  Files
                </p>
                {wsFiles.map((f) => (
                  <Link
                    key={f}
                    href={`/workspace?file=${encodeURIComponent(f)}`}
                    onClick={onClose}
                    title={f}
                    className="block truncate rounded px-3 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  >
                    {f.split("/").pop()}
                  </Link>
                ))}
              </div>
            )}
          </div>

          {/* Navigation - scrollable area */}
          <ScrollArea className="flex-1">
            <nav className="space-y-1 p-4">
              {navigation.map((item) => {
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href);

                return (
                  <Link
                    key={item.name}
                    href={item.href}
                    onClick={onClose}
                    className={cn(
                      "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                    )}
                  >
                    <item.icon className="h-5 w-5" />
                    {item.name}
                    {item.href === "/proposals" && proposalsCount > 0 && (
                      <span className="ml-auto rounded-full bg-primary/15 px-2 text-xs text-primary">
                        {proposalsCount}
                      </span>
                    )}
                  </Link>
                );
              })}
              {/* Admin Section */}
              <div className="mt-6 pt-4 border-t">
                <div className="flex items-center justify-between px-3 mb-2">
                  <div className="flex items-center gap-2">
                    <Shield className="h-4 w-4 text-muted-foreground" />
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      Admin
                    </span>
                  </div>
                  {isAdminAuthenticated && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={handleAdminLogout}
                      title="Logout from admin"
                    >
                      <LogOut className="h-3 w-3 text-muted-foreground" />
                    </Button>
                  )}
                </div>

                {isAdminAuthenticated ? (
                  // Show admin navigation when authenticated
                  adminNavigation.map((item) => {
                    const isActive = pathname.startsWith(item.href);

                    return (
                      <Link
                        key={item.name}
                        href={item.href}
                        onClick={onClose}
                        className={cn(
                          "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                          isActive
                            ? "bg-primary text-primary-foreground"
                            : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                        )}
                      >
                        <item.icon className="h-5 w-5" />
                        {item.name}
                      </Link>
                    );
                  })
                ) : (
                  // Show login prompt when not authenticated
                  <button
                    onClick={() => setShowAdminAuthModal(true)}
                    className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  >
                    <Lock className="h-5 w-5" />
                    <span>Unlock Admin</span>
                  </button>
                )}
              </div>
            </nav>
          </ScrollArea>

          {/* Footer */}
          <div className="border-t border-border p-4 shrink-0 space-y-2">
            {isHosted && (
              <>
                <BillingStatus />
                <a
                  href={`${config?.webappUrl || "https://vexa.ai"}/account`}
                  onClick={onClose}
                  className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                >
                  <CreditCard className="h-4 w-4" />
                  Account & Billing
                </a>
              </>
            )}
            {/* Bottom utility bar */}
            <div className="flex items-center justify-between px-2 pb-1">
              <Link href="/webhooks" onClick={onClose} title="Webhooks"
                className={cn("rounded-md p-2 hover:bg-accent", pathname.startsWith("/webhooks") ? "text-foreground" : "text-muted-foreground")}>
                <Webhook className="h-4 w-4" />
              </Link>
              <Link href="/mcp" onClick={onClose} title="MCP Setup"
                className={cn("rounded-md p-2 hover:bg-accent", pathname.startsWith("/mcp") ? "text-foreground" : "text-muted-foreground")}>
                <Zap className="h-4 w-4" />
              </Link>
              <Link href="/profile" onClick={onClose} title="Profile"
                className={cn("rounded-md p-2 hover:bg-accent", pathname.startsWith("/profile") ? "text-foreground" : "text-muted-foreground")}>
                <User className="h-4 w-4" />
              </Link>
              <a href={getDocsUrl("/")} target="_blank" rel="noopener noreferrer" title="API Docs"
                className="rounded-md p-2 text-muted-foreground hover:bg-accent">
                <BookOpen className="h-4 w-4" />
              </a>
              <a href="https://github.com/Vexa-ai/vexa/issues/new?labels=bug,hosted&title=[Hosted]%20" target="_blank" rel="noopener noreferrer" title="Report a Bug"
                className="rounded-md p-2 text-muted-foreground hover:bg-accent">
                <Bug className="h-4 w-4" />
              </a>
            </div>
            <div className="px-3">
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-muted-foreground">vexa</span>
                <VersionChip variant="minimal" look="pill" />
              </div>
            </div>
          </div>
        </div>
      </aside>

      {/* Admin Auth Modal */}
      <AdminAuthModal
        open={showAdminAuthModal}
        onOpenChange={setShowAdminAuthModal}
        onSuccess={handleAdminAuthSuccess}
      />
    </>
  );
}
