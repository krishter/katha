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

  const isAuthPage =
    pathname?.startsWith("/family/login") ||
    pathname?.startsWith("/family/auth") ||
    pathname?.startsWith("/family/onboarding");

  // SWR dedupes this against the same "stats" key used by the dashboard
  // home page, so this doesn't add an extra request there.
  const { data: stats } = useSWR(isAuthPage ? null : "stats", api.getStats);

  async function handleLogout() {
    await api.logout();
    router.push("/family/login");
  }

  if (isAuthPage) {
    return <>{children}</>;
  }

  const showUpgradeBanner =
    !!stats && stats.plan === "free" && stats.session_count >= stats.session_limit;

  return (
    <div className="min-h-screen bg-[#FDF6EC]">
      <nav className="flex items-center justify-between border-b border-[#E8DDD4] bg-white px-6 py-4">
        <Link href="/family" className="text-lg font-semibold text-[#2C2C2C]">
          katha
        </Link>
        <div className="flex items-center gap-6 text-sm font-medium text-[#6B5B4E]">
          <Link href="/family/stories" className="hover:text-[#C8956C]">
            Stories
          </Link>
          <Link href="/family/cards" className="hover:text-[#C8956C]">
            Memory Cards
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            className="hover:text-[#C8956C]"
          >
            Logout
          </button>
        </div>
      </nav>

      {showUpgradeBanner && (
        <div className="flex flex-wrap items-center justify-center gap-2 bg-[#C8956C] px-6 py-3 text-center text-sm text-white">
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
