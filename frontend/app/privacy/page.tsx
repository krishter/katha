export const metadata = {
  title: "Privacy Policy — Katha",
};

/*
 * Every claim on this page maps to code or configuration; the mapping is in
 * the pull request that last changed it (PRE_PILOT_FIX_PLAN P10, M-06). If
 * you change what the system does, change this page in the same PR.
 */

const LINK = "text-saffron-ink underline";

export default function PrivacyPage() {
  return (
    <main id="main" className="mx-auto max-w-2xl px-6 py-12 text-ink">
      <h1 className="font-display text-2xl font-semibold">Katha Privacy Policy</h1>
      <p className="mt-2 text-sm text-ink-mid">Version 1.1 · 21 October 2026</p>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">What we collect</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          <li>
            What your parent says to Katha, as text. Her voice notes are
            transcribed; Katha does not keep the audio.
          </li>
          <li>
            Story summaries, quotes and details (names, places, dates) drawn
            from those conversations.
          </li>
          <li>
            Katha&apos;s own spoken replies, as audio files, until the account
            is deleted.
          </li>
          <li>
            Contact details: your parent&apos;s name and WhatsApp number, and
            your email and WhatsApp number.
          </li>
          <li>
            For families using conversation cards: what you call your parent,
            your usual call times and time zone, and any questions or
            reactions you send back about a card. A question sent as a voice
            note is transcribed, and the audio is not kept.
          </li>
        </ul>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">How it&apos;s used</h2>
        <p className="mt-2 text-sm">
          Transcripts and the details drawn from them are used to keep your
          parent&apos;s stories, make memory cards, and let Katha remember what
          she has told it from one conversation to the next. Story summaries
          and memory cards are shown to you on the family dashboard, and
          memory cards are sent to your WhatsApp number once you have
          confirmed it.
        </p>
        <p className="mt-2 text-sm">
          If your parent asks for something to stay private, or not to be
          told to her family, it is never put on a card or shown on the
          dashboard, and the same holds for anything else she says on that
          subject. A card already sent before she asked cannot be recalled;
          it is removed from the dashboard and we are alerted.
        </p>
        <p className="mt-2 text-sm">
          Conversation cards are read back to your parent first, and are sent
          to you (by WhatsApp and email) only if she says yes to that card.
          She can decline any card, or choose not to share at all. A question
          you send back may be asked to her, always saying who it is from. Her
          answer is never sent back directly; it reaches you only as a later
          card she agrees to.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Who reads it</h2>
        <p className="mt-2 text-sm">
          A member of the Katha team may read what your parent says: to review
          a conversation card before it is offered to her, to check on her if
          something she says suggests she may be in danger, and to fix a
          problem with the service. If she says something that may mean she
          is in crisis, Katha gives her the iCall India helpline (9152987821)
          and emails the Katha team at once, with what she said, so a person
          can read it the same day.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Where it is, and who processes it</h2>
        <p className="mt-2 text-sm">
          Everything we store is stored in India, on Amazon Web Services in
          Mumbai (ap-south-1): the database, the files, and the nightly
          backups. Emails are sent through Amazon SES in Mumbai. To work,
          Katha passes some of it to these companies:
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          <li>
            <strong>Sarvam AI</strong> (India) turns her voice notes into text,
            and speaks some replies.
          </li>
          <li>
            <strong>Anthropic</strong> (USA) writes Katha&apos;s replies and
            picks out the stories from what she said.
          </li>
          <li>
            <strong>Google</strong> (USA) speaks Katha&apos;s replies, from
            text, with the request marked not to be stored.
          </li>
          <li>
            <strong>Twilio</strong> (USA) and <strong>Meta</strong>, which runs
            WhatsApp, carry the messages. Katha deletes each message and its
            audio from Twilio once it is no longer needed: her voice note
            straight after it is transcribed, and every other message within a
            day. Meta handles WhatsApp messages under its own terms.
          </li>
        </ul>
        <p className="mt-2 text-sm">
          The AI companies use what they are sent only to answer each request,
          and do not train their models on it. The website is hosted by Vercel;
          your family&apos;s data goes from your browser to our servers in
          Mumbai, not through Vercel.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">How long we keep it</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          <li>
            Your family&apos;s data: for as long as the account is open, until
            you or your parent asks us to delete it.
          </li>
          <li>
            Backups: each nightly backup is kept for 14 days, so deleted data
            is gone from backups within 14 days of deletion.
          </li>
          <li>
            Server logs: they never contain what your parent says, her number
            or your email. They are rotated by size, so older entries are
            overwritten.
          </li>
        </ul>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Stopping, and deletion</h2>
        <p className="mt-2 text-sm">
          Your parent can stop at any time by telling Katha so, in her own
          words. Katha stops at once, tells her once, and tells you by email.
          Everything is then deleted after seven days, a pause in case it was
          said by mistake; only she can ask us, through you, to keep it.
        </p>
        <p className="mt-2 text-sm">
          You can delete all of your family&apos;s data at any time from your
          account settings, or by emailing{" "}
          <a href="mailto:privacy@katha.life" className={LINK}>
            privacy@katha.life
          </a>
          . Deletion removes stories, memory cards, conversation cards and
          their images, Katha&apos;s spoken replies, questions you sent back,
          session history and profile data, and any messages still held at
          Twilio. We keep a record that consent was given or declined, for
          legal audit: it holds the date and the wording agreed to, and a
          one-way hash of your email address, so we can show consent if asked.
          Your names and numbers are removed from it.
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
          <a href="mailto:privacy@katha.life" className={LINK}>
            privacy@katha.life
          </a>
        </p>
      </section>
    </main>
  );
}
