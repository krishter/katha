"use client";

import { useState } from "react";
import useSWR from "swr";

import { api } from "@/lib/api";

/**
 * Storyteller settings — currently pronouns, and only pronouns.
 *
 * This exists because the fix that added the field cannot reach the
 * families who need it most without it. Pronouns were added to onboarding,
 * but every profile created before that has the value empty, and empty is
 * a degraded fallback rather than a safe default: in Hindi and similar
 * languages the verb agrees with the person being addressed, so Katha ends
 * up choosing a gender anyway, and inconsistently. Measured at 4 feminine
 * / 1 masculine across five runs on one unchanged profile.
 *
 * So the point of this page is that an existing family can correct it
 * without re-onboarding.
 */

const CARD_CLASS = "rounded-xl bg-surface p-6 shadow-sm";
const INPUT_CLASS =
  "mt-1 w-full max-w-sm rounded-lg border border-border px-4 py-2 text-ink focus:border-saffron";

const PRESETS = ["she/her", "he/him", "they/them"];

export default function StorytellerSettingsPage() {
  const { data: stats, isLoading, mutate } = useSWR("stats", api.getStats);

  // Null means "the user has not touched this yet", in which case the
  // field shows what the server holds. Deriving rather than copying the
  // server value into state with an effect: the effect version has to
  // guard against overwriting a half-typed edit on every refetch, and
  // gets it wrong the first time someone types while a revalidation is
  // in flight.
  const [choiceEdit, setChoiceEdit] = useState<string | null>(null);
  const [customEdit, setCustomEdit] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stored = stats?.pronouns ?? "";
  // A stored value that is not one of the presets is a free-text answer,
  // and opens the "something else" box holding its own wording rather
  // than being silently dropped.
  const storedIsPreset = PRESETS.includes(stored);
  const choice =
    choiceEdit ?? (stored === "" ? "" : storedIsPreset ? stored : "other");
  const custom = customEdit ?? (stored !== "" && !storedIsPreset ? stored : "");

  const parentName = stats?.user_name ?? "your parent";
  const value = choice === "other" ? custom.trim() : choice;

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.updatePronouns(value);
      await mutate();
      setSaved(true);
    } catch {
      setError("Couldn't save that. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-ink-mid">Loading…</p>;
  }

  return (
    <section className={CARD_CLASS}>
      <h2 className="font-display text-lg font-semibold text-ink">
        How Katha speaks to {parentName}
      </h2>
      <p className="mt-2 text-sm text-ink-mid">
        Many Indian languages change the verb depending on gender. Telling
        Katha which to use is the only reliable way for her to get it right
        — without it she has to guess, and in Hindi, Tamil and others she
        cannot avoid guessing.
      </p>

      <form onSubmit={handleSave} className="mt-6 flex flex-col gap-3">
        <label htmlFor="pronouns" className="text-sm font-medium text-ink">
          Pronouns
        </label>
        <select
          id="pronouns"
          value={choice}
          onChange={(e) => {
            setChoiceEdit(e.target.value);
            setSaved(false);
          }}
          className={INPUT_CLASS}
        >
          {PRESETS.map((p) => (
            <option key={p} value={p}>
              {p.replace("/", " / ")}
            </option>
          ))}
          <option value="other">Something else</option>
          <option value="">Prefer not to say</option>
        </select>

        {choice === "other" && (
          <input
            type="text"
            value={custom}
            onChange={(e) => {
              setCustomEdit(e.target.value);
              setSaved(false);
            }}
            placeholder="e.g. she/they"
            maxLength={40}
            className={INPUT_CLASS}
          />
        )}

        {choice === "" && (
          <p className="max-w-sm text-xs text-ink-mid">
            Katha will avoid gendered wording where the language allows it.
            In Hindi and similar languages some sentences cannot avoid it,
            so she may still get it wrong.
          </p>
        )}

        <div className="mt-2 flex items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-saffron px-4 py-2 text-sm font-medium text-indigo transition hover:bg-gold disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {saved && <span className="text-sm text-ink-mid">Saved.</span>}
          {error && <span className="text-sm text-danger">{error}</span>}
        </div>
      </form>
    </section>
  );
}
