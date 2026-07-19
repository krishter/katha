export const metadata = {
  title: "Privacy Policy — Katha",
};

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-12 text-[#2C2C2C]">
      <h1 className="text-2xl font-semibold">Katha Privacy Policy</h1>
      <p className="mt-2 text-sm text-[#6B5B4E]">Version 1.0</p>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">What data we collect</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          <li>Voice conversation transcripts (not the raw audio — see below)</li>
          <li>
            Story summaries, quotes, and extracted details (names, places,
            dates) captured from those conversations
          </li>
          <li>
            Family contact details: the elderly user&apos;s name and WhatsApp
            number, and the family account holder&apos;s email and WhatsApp
            number
          </li>
        </ul>
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">How it&apos;s used</h2>
        <p className="mt-2 text-sm">
          Transcripts and extracted details are used to preserve life stories,
          generate memory cards, and let Katha have continuous, informed
          conversations across sessions. Story summaries and memory cards are
          shared with the family account holder via the family dashboard and
          WhatsApp.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">Data residency</h2>
        <p className="mt-2 text-sm">
          All data is stored in India (AWS ap-south-1, Mumbai), in line with
          the Digital Personal Data Protection Act, 2023.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">Retention</h2>
        <p className="mt-2 text-sm">
          We keep your family&apos;s data for as long as your account is
          active. Raw voice notes are never persisted — audio is processed in
          memory during a conversation and discarded immediately after
          transcription.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">Your right to deletion</h2>
        <p className="mt-2 text-sm">
          You can permanently delete all of your family&apos;s data at any
          time from your account settings, or by emailing{" "}
          <a href="mailto:privacy@katha.life" className="text-[#C8956C] underline">
            privacy@katha.life
          </a>
          . Deletion removes stories, memory cards, session history, and
          profile data. A minimal, anonymized consent record is retained for
          legal audit purposes, with all identifying details removed.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">No AI training on your data</h2>
        <p className="mt-2 text-sm">
          Katha never uses your family&apos;s conversations, stories, or any
          other content to train or fine-tune AI models.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">Contact</h2>
        <p className="mt-2 text-sm">
          Questions about this policy or your data:{" "}
          <a href="mailto:privacy@katha.life" className="text-[#C8956C] underline">
            privacy@katha.life
          </a>
        </p>
      </section>
    </main>
  );
}
