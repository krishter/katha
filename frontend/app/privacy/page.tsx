export const metadata = {
  title: "Privacy Policy — Katha",
};

export default function PrivacyPage() {
  return (
    <main id="main" className="mx-auto max-w-2xl px-6 py-12 text-ink">
      <h1 className="font-display text-2xl font-semibold">Katha Privacy Policy</h1>
      <p className="mt-2 text-sm text-ink-mid">Version 1.0</p>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">What data we collect</h2>
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
          <li>
            For families using conversation cards: what the family account
            holder calls the parent, their usual call times and time zone, and
            any questions or reactions they send back about a card. A question
            sent as a voice note is transcribed, and the audio is not kept.
          </li>
        </ul>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">How it&apos;s used</h2>
        <p className="mt-2 text-sm">
          Transcripts and extracted details are used to preserve life stories,
          generate memory cards, and let Katha have continuous, informed
          conversations across sessions. Story summaries and memory cards are
          shared with the family account holder via the family dashboard and
          WhatsApp.
        </p>
        <p className="mt-2 text-sm">
          Conversation cards are different: each one is read back to the
          parent first, and is sent to the family (by WhatsApp and email) only
          if the parent says yes to that card. The parent can decline any
          card, or choose not to share at all. If the parent asks for
          something to stay private, it is never put on a card or shown on
          the family dashboard. A question the family sends back may be asked
          to the parent, always saying who it is from. The parent&apos;s answer
          is never sent back directly; it reaches the family only as a later
          card the parent agrees to.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Data residency</h2>
        <p className="mt-2 text-sm">
          All data is stored in India (AWS ap-south-1, Mumbai), in line with
          the Digital Personal Data Protection Act, 2023. Card emails are sent
          through Amazon SES in the same Mumbai region.
        </p>
        <p className="mt-2 text-sm">
          To write and speak Katha&apos;s replies, the conversation text is
          processed by AI service providers outside India (Anthropic for the
          replies, Google for the voice). They use it only to answer each
          request and do not use it to train their models. Any copy they keep
          for abuse monitoring is deleted after a limited period.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Retention</h2>
        <p className="mt-2 text-sm">
          We keep your family&apos;s data for as long as your account is
          active. Raw voice notes are never persisted — audio is processed in
          memory during a conversation and discarded immediately after
          transcription.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Your right to deletion</h2>
        <p className="mt-2 text-sm">
          You can permanently delete all of your family&apos;s data at any
          time from your account settings, or by emailing{" "}
          <a href="mailto:privacy@katha.life" className="text-saffron-ink underline">
            privacy@katha.life
          </a>
          . Deletion removes stories, memory cards, conversation cards and
          their images, questions sent back by the family, session history,
          and profile data. A minimal, anonymized consent record is retained for
          legal audit purposes, with all identifying details removed.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">No AI training on your data</h2>
        <p className="mt-2 text-sm">
          Katha never uses your family&apos;s conversations, stories, or any
          other content to train or fine-tune AI models.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Contact</h2>
        <p className="mt-2 text-sm">
          Questions about this policy or your data:{" "}
          <a href="mailto:privacy@katha.life" className="text-saffron-ink underline">
            privacy@katha.life
          </a>
        </p>
      </section>
    </main>
  );
}
