"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";

import { api } from "@/lib/api";

export default function FamilyLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();

  // Pages that render without the signed-in chrome. Two kinds: the routes
  // you reach before authenticating, and the post-deletion confirmation —
  // by the time that one renders the katha_token cookie is already gone,
  // so fetching stats would 401 and bounce the user to a login screen at
  // the exact moment they need to be told the deletion worked.
  const isChromelessPage =
    pathname?.startsWith("/family/login") ||
    pathname?.startsWith("/family/auth") ||
    pathname?.startsWith("/family/onboarding") ||
    pathname?.startsWith("/family/settings/privacy/deleted");

  // SWR dedupes this against the same "stats" key used by the dashboard
  // home page, so this doesn't add an extra request there.
  const { data: stats } = useSWR(isChromelessPage ? null : "stats", api.getStats);

  async function handleLogout() {
    await api.logout();
    router.push("/family/login");
  }

  if (isChromelessPage) {
    return <>{children}</>;
  }

  const showUpgradeBanner =
    !!stats && stats.plan === "free" && stats.session_count >= stats.session_limit;

  return (
    <div className="min-h-screen bg-page">
      <nav className="flex items-center justify-between border-b border-border bg-surface-alt px-6 py-4">
        <Link
          href="/family"
          className="font-display text-xl font-bold text-indigo"
        >
          Katha
        </Link>
        <div className="flex items-center gap-6 text-sm font-medium text-ink-mid">
          <Link href="/family/stories" className="hover:text-saffron-ink">
            Stories
          </Link>
          <Link href="/family/cards" className="hover:text-saffron-ink">
            Memory Cards
          </Link>
          <Link href="/family/settings/privacy" className="hover:text-saffron-ink">
            Settings
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            className="hover:text-saffron-ink"
          >
            Logout
          </button>
        </div>
      </nav>

      {showUpgradeBanner && (
        <div className="flex flex-wrap items-center justify-center gap-2 bg-saffron px-6 py-3 text-center text-sm text-indigo">
          <span>
            {stats.user_name} has completed all {stats.session_limit} free
            sessions with Katha.
          </span>
          <a
            href="mailto:hello@katha.life?subject=Upgrade"
            className="font-semibold underline"
          >
            Contact us to continue →
          </a>
        </div>
      )}

      {children}
    </div>
  );
}
