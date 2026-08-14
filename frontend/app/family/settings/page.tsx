import { redirect } from "next/navigation";

/**
 * Privacy & Data is the only section this sprint, so /family/settings has
 * nothing of its own to show. Redirect rather than render an empty shell —
 * when a second section lands, this becomes a real index or keeps
 * redirecting to whichever section should open by default.
 */
export default function SettingsIndexPage() {
  redirect("/family/settings/privacy");
}
