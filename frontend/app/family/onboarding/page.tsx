"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api";

type Step = "checking" | "email" | "verify" | "profile" | "consent" | "done";

const LANGUAGE_OPTIONS: Array<{ code: string; label: string }> = [
  { code: "hi-IN", label: "Hindi" },
  { code: "ta-IN", label: "Tamil" },
  { code: "te-IN", label: "Telugu" },
  { code: "ml-IN", label: "Malayalam" },
  { code: "kn-IN", label: "Kannada" },
  { code: "bn-IN", label: "Bengali" },
  { code: "mr-IN", label: "Marathi" },
  { code: "gu-IN", label: "Gujarati" },
  { code: "en-IN", label: "English" },
];

const CARD_CLASS = "w-full max-w-md rounded-xl bg-white p-8 shadow-sm";
const LABEL_CLASS = "text-sm font-medium text-[#2C2C2C]";
const INPUT_CLASS =
  "mt-1 w-full rounded-lg border border-[#E8DDD4] px-4 py-2 text-[#2C2C2C] focus:border-[#C8956C] focus:outline-none";
const BUTTON_CLASS =
  "w-full rounded-lg bg-[#C8956C] px-4 py-2 font-medium text-white transition hover:bg-[#b17f57] disabled:opacity-60";

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("checking");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");

  const [parentName, setParentName] = useState("");
  const [whatsappNumber, setWhatsappNumber] = useState("");
  const [familyWhatsappNumber, setFamilyWhatsappNumber] = useState("");
  const [preferredLanguage, setPreferredLanguage] = useState("hi-IN");
  const [sessionTime, setSessionTime] = useState("09:30");
  const [onboardingContext, setOnboardingContext] = useState("");

  const [consentChecked, setConsentChecked] = useState(false);
  const [doneInfo, setDoneInfo] = useState<{
    parentName: string;
    sessionTime: string;
  } | null>(null);

  // A magic-link click is a full page reload landing back on this same
  // route once the cookie is set — this mount check is what actually
  // advances the wizard past step "email"/"verify" after that reload.
  useEffect(() => {
    api.isAuthenticated().then((authed) => {
      setStep(authed ? "profile" : "email");
    });
  }, []);

  async function handleEmailSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.startOnboarding(email);
      setStep("verify");
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleProfileSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.submitProfile({
        parent_name: parentName,
        whatsapp_number: whatsappNumber,
        family_whatsapp_number: familyWhatsappNumber,
        preferred_language: preferredLanguage,
        session_time: sessionTime,
        onboarding_context: onboardingContext,
      });
      setStep("consent");
    } catch {
      setError(
        "Couldn't save that. Double-check the WhatsApp numbers are in " +
          "+91XXXXXXXXXX format."
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function handleConsentSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.submitConsent(true);
      setDoneInfo({
        parentName: result.parent_name,
        sessionTime: result.session_time,
      });
      setStep("done");
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#FDF6EC] p-8">
      {step === "checking" && <p className="text-[#6B5B4E]">Loading...</p>}

      {step === "email" && (
        <div className={CARD_CLASS}>
          <h1 className="text-2xl font-semibold text-[#2C2C2C]">
            Welcome to Katha
          </h1>
          <p className="mt-2 text-sm text-[#6B5B4E]">
            Let&apos;s set up daily conversations for your parent. Enter your
            email to get started.
          </p>
          <form onSubmit={handleEmailSubmit} className="mt-6 flex flex-col gap-3">
            <label htmlFor="email" className="sr-only">
              Email address
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className={INPUT_CLASS}
            />
            <button type="submit" disabled={submitting} className={BUTTON_CLASS}>
              {submitting ? "Sending..." : "Continue"}
            </button>
            {error && <p className="text-sm text-red-600">{error}</p>}
          </form>
        </div>
      )}

      {step === "verify" && (
        <div className={`${CARD_CLASS} text-center`}>
          <h1 className="text-2xl font-semibold text-[#2C2C2C]">
            Check your email
          </h1>
          <p className="mt-3 text-sm text-[#6B5B4E]">
            We&apos;ve sent a login link to <strong>{email}</strong>. Click it
            to continue.
          </p>
        </div>
      )}

      {step === "profile" && (
        <div className={CARD_CLASS}>
          <h1 className="text-2xl font-semibold text-[#2C2C2C]">
            Tell us about your parent
          </h1>
          <form onSubmit={handleProfileSubmit} className="mt-6 flex flex-col gap-4">
            <div>
              <label htmlFor="parent_name" className={LABEL_CLASS}>
                Parent&apos;s name
              </label>
              <input
                id="parent_name"
                required
                value={parentName}
                onChange={(e) => setParentName(e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="whatsapp_number" className={LABEL_CLASS}>
                Parent&apos;s WhatsApp number
              </label>
              <input
                id="whatsapp_number"
                type="tel"
                required
                value={whatsappNumber}
                onChange={(e) => setWhatsappNumber(e.target.value)}
                placeholder="+91 98765 43210"
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="family_whatsapp_number" className={LABEL_CLASS}>
                Your WhatsApp number (for memory cards)
              </label>
              <input
                id="family_whatsapp_number"
                type="tel"
                required
                value={familyWhatsappNumber}
                onChange={(e) => setFamilyWhatsappNumber(e.target.value)}
                placeholder="+91 98765 43210"
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="preferred_language" className={LABEL_CLASS}>
                Preferred language
              </label>
              <select
                id="preferred_language"
                value={preferredLanguage}
                onChange={(e) => setPreferredLanguage(e.target.value)}
                className={INPUT_CLASS}
              >
                {LANGUAGE_OPTIONS.map((lang) => (
                  <option key={lang.code} value={lang.code}>
                    {lang.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="session_time" className={LABEL_CLASS}>
                Best time to call (IST)
              </label>
              <input
                id="session_time"
                type="time"
                required
                value={sessionTime}
                onChange={(e) => setSessionTime(e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="onboarding_context" className={LABEL_CLASS}>
                A little about them
              </label>
              <textarea
                id="onboarding_context"
                value={onboardingContext}
                onChange={(e) => setOnboardingContext(e.target.value)}
                placeholder="E.g. Grew up in Chennai. Worked as a schoolteacher for 35 years. Has two children."
                rows={3}
                maxLength={1000}
                className={INPUT_CLASS}
              />
            </div>
            <button type="submit" disabled={submitting} className={BUTTON_CLASS}>
              {submitting ? "Saving..." : "Continue"}
            </button>
            {error && <p className="text-sm text-red-600">{error}</p>}
          </form>
        </div>
      )}

      {step === "consent" && (
        <div className={CARD_CLASS}>
          <h1 className="text-2xl font-semibold text-[#2C2C2C]">
            Before we begin
          </h1>
          <p className="mt-2 text-sm text-[#6B5B4E]">
            Please read and agree to the following:
          </p>
          <ul className="mt-4 flex flex-col gap-2 text-sm text-[#2C2C2C]">
            {[
              `Katha will record voice conversations with ${parentName || "your parent"} via WhatsApp`,
              "Conversations are transcribed and stored to preserve life stories",
              "Story summaries and quotes are shared with you (the family account holder)",
              "Your family's data is stored securely in India (Mumbai)",
              "You can delete all data at any time from your account settings",
              "Katha does not use your family's data to train AI models",
              "Katha is not a medical service. For emergencies, please call 112.",
            ].map((line) => (
              <li key={line} className="flex gap-2">
                <span aria-hidden="true" className="text-[#C8956C]">
                  ✓
                </span>
                <span>{line}</span>
              </li>
            ))}
          </ul>

          <label className="mt-6 flex items-start gap-2 text-sm text-[#2C2C2C]">
            <input
              type="checkbox"
              checked={consentChecked}
              onChange={(e) => setConsentChecked(e.target.checked)}
              className="mt-1"
            />
            <span>
              I have read and agree to Katha&apos;s Privacy Policy and the
              above terms.
            </span>
          </label>

          <a
            href="/privacy"
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-block text-sm text-[#C8956C] underline"
          >
            Read Privacy Policy →
          </a>

          <button
            type="button"
            onClick={handleConsentSubmit}
            disabled={!consentChecked || submitting}
            className={`${BUTTON_CLASS} mt-6`}
          >
            {submitting ? "Saving..." : "I Agree"}
          </button>
          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </div>
      )}

      {step === "done" && doneInfo && (
        <div className={`${CARD_CLASS} text-center`}>
          <h1 className="text-2xl font-semibold text-[#2C2C2C]">All set!</h1>
          <p className="mt-3 text-sm text-[#6B5B4E]">
            Katha will message {doneInfo.parentName} tomorrow at{" "}
            {doneInfo.sessionTime} IST.
          </p>
          <button
            type="button"
            onClick={() => router.push("/family")}
            className={`${BUTTON_CLASS} mt-6`}
          >
            Go to dashboard →
          </button>
        </div>
      )}
    </main>
  );
}
