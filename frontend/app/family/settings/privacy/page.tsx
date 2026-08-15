"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { api } from "@/lib/api";

/**
 * Privacy & Data.
 *
 * The onboarding consent checklist tells every buyer, verbatim, that they
 * can delete all data from account settings. Until this page existed that
 * was a promise with nothing behind it — DELETE /user/{user_id} was
 * well-implemented and simply unreachable (F-01). Under the DPDP Act the
 * right to erasure has to be exercisable, not merely honoured on request.
 *
 * Deletion is deliberately awkward: two steps, and the confirmation string
 * is the parent's name rather than the word DELETE, so the last thing the
 * user reads before destroying an archive is who it belongs to.
 */

const CARD_CLASS = "rounded-xl bg-surface p-6 shadow-sm";

export default function PrivacySettingsPage() {
  const router = useRouter();
  const { data: stats, isLoading } = useSWR("stats", api.getStats);

  const [confirming, setConfirming] = useState(false);
  const [typedName, setTypedName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exported, setExported] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parentName = stats?.user_name ?? "";
  const nameMatches =
    typedName.trim().toLowerCase() === parentName.trim().toLowerCase() &&
    parentName.length > 0;

  async function handleExport() {
    setExporting(true);
    setError(null);
    try {
      const bundle = await api.exportData();
      const blob = new Blob([JSON.stringify(bundle, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `katha-export-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
      setExported(true);
    } catch {
      setError("Couldn't produce the export. Nothing has been deleted.");
    } finally {
      setExporting(false);
    }
  }

  async function handleDelete() {
    if (!stats?.user_id || !nameMatches) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteAllData(stats.user_id);
      // The endpoint clears katha_token itself via delete_cookie. Calling
      // logout() here as well would race it, so navigate straight out.
      router.push("/family/settings/privacy/deleted");
    } catch {
      setError("Deletion failed. Your data has not been changed.");
      setDeleting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section className={CARD_CLASS}>
        <h2 className="font-display text-xl font-semibold text-ink">
          Your data
        </h2>
        <p className="mt-2 text-sm text-ink-mid">
          Everything Katha has recorded is stored in India (Mumbai) and is
          never used to train AI models.
        </p>

        <button
          type="button"
          onClick={handleExport}
          disabled={exporting}
          className="mt-4 rounded-lg border border-border-strong px-4 py-2 text-sm font-medium text-ink hover:border-saffron-ink disabled:opacity-50"
        >
          {exporting ? "Preparing export..." : "Download a copy (JSON)"}
        </button>
        <p className="mt-2 text-meta text-ink-muted">
          Stories, sessions and memory card quotes. Audio recordings are not
          included yet.
        </p>
        {exported && (
          <p className="mt-2 text-meta text-success">Export downloaded.</p>
        )}
      </section>

      <section className={CARD_CLASS}>
        <h2 className="font-display text-xl font-semibold text-ink">
          Delete everything
        </h2>

        {isLoading && <p className="mt-2 text-sm text-ink-mid">Loading...</p>}

        {stats && !confirming && (
          <>
            <p className="mt-2 text-sm text-ink">
              This permanently deletes{" "}
              <strong>{stats.total_story_atoms} stories</strong> from{" "}
              <strong>{stats.total_sessions} conversations</strong>, along with{" "}
              <strong>{stats.total_memory_cards} memory cards</strong> and every
              voice recording, from {parentName}&apos;s archive.
            </p>
            <p className="mt-3 text-sm text-ink-mid">
              This cannot be undone. If you only want to stop the daily
              conversations, contact us instead — you do not need to delete
              anything to pause Katha.
            </p>
            {!exported && (
              <p className="mt-3 text-sm text-attention">
                Consider downloading a copy first. Once deleted, these stories
                cannot be recovered by anyone.
              </p>
            )}
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="mt-4 rounded-lg border border-danger px-4 py-2 text-sm font-medium text-danger hover:bg-danger-soft"
            >
              Delete all data
            </button>
          </>
        )}

        {stats && confirming && (
          <>
            <p className="mt-2 text-sm text-ink">
              To confirm, type <strong>{parentName}</strong> below. This deletes
              their entire archive permanently.
            </p>
            <label
              htmlFor="confirm-name"
              className="mt-4 block text-sm font-medium text-ink"
            >
              Parent&apos;s name
            </label>
            <input
              id="confirm-name"
              type="text"
              value={typedName}
              autoComplete="off"
              onChange={(e) => setTypedName(e.target.value)}
              className="mt-1 w-full max-w-sm rounded-lg border border-border-strong px-4 py-2 text-ink focus:border-danger"
            />

            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={handleDelete}
                disabled={!nameMatches || deleting}
                className="rounded-lg bg-danger px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                {deleting ? "Deleting..." : "Permanently delete"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirming(false);
                  setTypedName("");
                }}
                disabled={deleting}
                className="rounded-lg border border-border px-4 py-2 text-sm text-ink"
              >
                Cancel
              </button>
            </div>
          </>
        )}

        {error && <p className="mt-4 text-sm text-danger">{error}</p>}
      </section>
    </div>
  );
}
