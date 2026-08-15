"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Settings shell.
 *
 * Sprint 1 ships Privacy & Data only. Conversation schedule, language and
 * pause (D1/D2, closing F-06) are real gaps but not compliance blockers,
 * so the section list below is deliberately built to take a second entry
 * without restructuring — add to SECTIONS and create the route.
 */

const SECTIONS = [
  {
    href: "/family/settings/privacy",
    label: "Privacy & Data",
    description: "Export or delete everything Katha has stored",
  },
];

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <main id="main" className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="font-display text-2xl font-semibold text-ink">Settings</h1>

      <div className="mt-8 flex flex-col gap-8 md:flex-row">
        <nav aria-label="Settings sections" className="md:w-56 md:shrink-0">
          <ul className="flex flex-col gap-1">
            {SECTIONS.map((section) => {
              const isActive = pathname === section.href;
              return (
                <li key={section.href}>
                  <Link
                    href={section.href}
                    aria-current={isActive ? "page" : undefined}
                    className={`block rounded-lg px-4 py-2 text-sm ${
                      isActive
                        ? "bg-surface font-medium text-ink"
                        : "text-ink-mid hover:text-saffron-ink"
                    }`}
                  >
                    {section.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </main>
  );
}
