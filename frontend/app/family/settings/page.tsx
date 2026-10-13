import { redirect } from "next/navigation";

/**
 * /family/settings has nothing of its own to show, so it opens the first
 * section rather than rendering an empty shell.
 */
export default function SettingsIndexPage() {
  redirect("/family/settings/storyteller");
}
