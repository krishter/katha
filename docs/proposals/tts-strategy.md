# TTS strategy: Svara and the alternatives to Sarvam

**Status:** Decided 2026-10-18 after the listening tests: Gemini 3.8 Flash-Lite TTS for new families. See `docs/specs/GEMINI_TTS.md`. Svara Turbo was not tested.
**Written:** 2026-10-17
**Related:** `docs/specs/COST_TO_SERVE.md` §10 (C6), `backend/media/voices.py`

---

## 1. The problem, stated precisely

Since 2026-10-17 Katha speaks on Bulbul v4 Flash where Sarvam has a conversation-style voice, and on v3 everywhere else. Of the 26 (language, persona) pairs in `voices.py`:

| | Pairs | Languages |
|---|---|---|
| v4 Flash | 14 | Hindi, English, Marathi, Kannada, Gujarati, Bengali (both personas); Telugu female; Punjabi male |
| v3 | 8 | Tamil, Malayalam, Odia (both personas); Telugu male; Punjabi female |
| No voice at all | 4 | Assamese, Urdu (beta access needed; replies go out as text) |

Only 2 of the 14 v4 voices have been heard by a person (Hindi and English, female).

"Mixing v3 and v4" can mean two things, and only one of them reaches a parent:

- **Across families.** Each parent hears one language and one persona, so no parent ever hears both models side by side. A Tamil parent doesn't hear a jarring switch. She just gets the flatter voice. That is a quality gap, not an inconsistency.
- **Within one parent's life with Katha.** This is the inconsistency that matters, and it happens in three ways:
  1. **Migration.** When Sarvam ships v4 voices for Tamil, Malayalam or Odia, moving those parents changes Katha's voice partway through the relationship. For an elderly user who has bonded with "her" Katha, that is the change most likely to be felt.
  2. **The rescue path.** If v4 fails twice (about 1 clip in 170, by the 2026-10-17 test), the reply goes out on v3, which is a different-sounding speaker.
  3. **No voice.** Assamese and Urdu parents get text, which breaks the voice-first premise entirely.

**The gap falls on the most important markets.** Tamil Nadu and Kerala are the two states whose elderly share is projected to pass 20% by 2036, against 15% nationally (UNFPA India Ageing Report 2023). Malayalam has no v4 voice at all, and Tamil's v4 voices are ads and narration styles only.

**So the goal is: one parent, one voice, for life, and that voice good in every language we sell.** It is not "one model for everyone."

## 2. Svara: what it actually is

"Svara" is two different products from Kenpath Labs (Bengaluru). The distinction matters.

### Svara TTS v1: open weights

- **What it is.** A 3B model (Orpheus-style audio tokens on Llama-3.2-3B), Apache-2.0, released 27 Oct 2025.
- **Training data.** About 1,600–2,000 hours from public read-speech corpora (SYSPIN, AI4Bharat RASA, IIT-M IndicTTS, SPICOR), with about 50 speakers.
- **Voices.** One voice per language and gender ("Tamil (Female)").
- **Emotion.** Tags: `<happy>`, `<sad>`, `<anger>`, `<fear>`, `<clear>`.
- **Training hours for Katha's gap languages.** About 55 h for Tamil, 60 h for Malayalam and 60 h for Assamese, against 100–130 h for Hindi, Bengali and Marathi. Kenpath says emotion strength "varies by language" with data density.
- **The language list is inconsistent.** The Hugging Face card lists no Odia or Urdu. Kenpath's own page lists Odia but drops English.
- **Evidence.** No MOS, WER or latency figures are published. The only signals are downloads (76.5k on Hugging Face for v1; Kenpath claims over 1M across all) and anecdotal reviews.
- **Self-hosting.** It needs a 16–24 GB GPU. At pilot scale, an always-on GPU costs far more per month than any API option below. It is not a real option for Katha now.

### Svara TTS Turbo: hosted API

This is the serious contender.

- **Coverage.** 82 languages and 320 voices. Every voice speaks every language, and its native language sets its accent.
- **API.** OpenAI- and ElevenLabs-compatible. It outputs `opus`, which suits WhatsApp voice notes, and accepts 5,000 characters per request.
- **Price.** ₹1 per 1,000 characters, which is half of v4 Flash and a third of v3. The free tier is 100k characters a month.
- **Gaps between marketing and the API.**
  - Kenpath's page advertises "30+ audio tags", but the API spec defines none, and `voice_settings` is "accepted and ignored". There is currently no documented way to ask for a slower, softer delivery for a tired parent.
  - India data residency and zero data retention are **Enterprise-only**.
  - The docs do not list which Indian languages have *native* voices. Every voice "speaks" Tamil, but a Hindi-native voice will sound like a Hindi speaker doing so. This has to be checked by listening.
- **Real-world evidence.** Thin, and none of it independent.
  - The Python SDK reached 0.2.0 on 20 Sep 2026.
  - One public integration: COSS-India's VoicEra merged Svara Turbo on 29 Sep 2026, into a `deployment-mahavistaar` branch. MahaVISTAAR is Maharashtra's farmer advisory service, built with EkStep and COSS. This shows it is being tried in a population-scale public deployment, but nothing has been published about how it performed.
  - Svara is in neither of the two large independent evaluations below.

**Verdict on Svara:** the price and coverage are compelling, and it may sound excellent. But there is no independent evidence yet that it is good in Tamil, Malayalam or Odia, and those are the languages we need it for. It earns a place in a listening test, not a migration.

## 3. The alternatives

### The independent evidence

**Preferences of a Voice-First Nation** (IIT Madras, AI4Bharat and Josh Talks; arXiv 2604.21481, June 2026). This is the largest study there is: 1,915 native raters, more than 120,000 blind pairwise comparisons, and 10 Indian languages, including Tamil, Malayalam, Odia and Urdu.

| Rank | System | Score (Bradley-Terry) |
|---|---|---|
| 1 | Gemini 2.5 Pro TTS | 1129 (first in 9 of 10 languages) |
| 2 | ElevenLabs v3 | 1056 |
| 2 | Sonic 3 | 1051 |
| 4 | Sarvam Bulbul v3 (beta) | 1022 |
| 7 | IndicF5 (open source) | 806 |

Expressiveness and intelligibility were the strongest predictors of preference.

**Caveats for Katha:**
- Only 114 raters were over 40, and none were over 65.
- Neither v4 Flash nor Gemini 3.8 was tested.

**Artificial Analysis voice arena** (23 Sep 2026, mostly English). Gemini 3.8 Flash TTS is #2 (1260 Elo), and Flash-Lite is #6 (1235).

**Benchmarks that favour Sarvam are weaker.** Caller Digital's July 2026 study puts Bulbul first in all five languages it tested. But it used 12 raters per language and no significance tests, its raw data is unpublished, and the author sells multi-vendor routing. Your own blind A/B (v4 Flash preferred in 19 of 20 pairs) is the strongest Sarvam evidence, and it covers Hindi and English only.

**Tamil is hard for everyone.** On retroflex sounds, all four commercial systems tested in the PSP benchmark (arXiv 2604.25476) collapsed 64–70% of Tamil retroflexes, against about 1% in Hindi. Whatever we pick, Tamil needs native ears.

### The candidates

| Option | Covers Katha's 13 languages? | Style control | Data location | Evidence for Indian languages |
|---|---|---|---|---|
| **Sarvam v4 Flash** | 10; no Malayalam or Odia; Tamil has no conversation voice | Persona voices only | India | Your 19/20 A/B (Hindi and English) |
| **Sarvam v3** | 11; no Assamese or Urdu without beta | Pace only | India | #4 in the IIT-M study |
| **Svara Turbo** | Claims all 13 (82 languages); native voices per language unverified | None documented | India only on Enterprise | None independent |
| **Gemini 3.8 Flash / Flash-Lite TTS** | 12; Urdu not listed | Natural-language style prompt ("speak slowly and warmly…") | Outside India (global/US/EU) | Its predecessor ranked #1 in 9 of 10 Indian languages; arena #2 |
| **ElevenLabs v3** | All 12 Indic languages incl. Odia, Assamese, Urdu | Audio tags | Outside India | #2 in the IIT-M study |
| **Google Chirp 3 HD** | 10; no Odia or Assamese; Punjabi in preview | Pace, pauses | Outside India | Not in the IIT-M study |
| **Smallest Lightning v3.1 Pro** | 9; includes Tamil, Malayalam, Odia | — | — | None found |

Azure's neural voices cover Odia and Assamese and can run in Central India. They are an older, flatter generation, so they are useful only as a last-resort coverage fallback.

**Gemini's data location in context.** The text that goes to TTS is the same reply text that already goes to Anthropic's API outside India. Gemini adds a second foreign processor to the DPDP notice, not a new category of exposure. The paid tier does not use the data to improve Google's products.

## 4. Cost

**Assumptions.**
- About 2,300 characters of speech per session. This is TTS's 16% share of today's ₹43 session at v3's ₹30 per 10k characters.
- 15 characters per second, which gives about 2.6 minutes of audio. Katha's clips measured 12.5–20.4 characters per second.
- 200 sessions a year and ₹94.95 to the US dollar (16 Oct 2026).
- Gemini bills by audio *seconds*: a measured 31.9 tokens per second, not the 25 Google documents.

| Option | ₹ per 10k chars equiv. | ₹ per session | ₹ per family per year |
|---|---|---|---|
| Svara Turbo | 10 | **2.30** | 460 |
| Gemini 3.8 Flash-Lite (to 31 Dec) | ~12 | 2.86 | 573 |
| Sarvam v4 Flash (today's default) | 20 | 4.60 | 920 |
| Gemini 3.8 Flash (to 31 Dec) | ~19 | 4.26 | 851 |
| Gemini 3.8 Flash-Lite (from 1 Jan 2027) | ~24 | 5.73 | 1,145 |
| Google Chirp 3 HD | 28.5 | 6.55 | 1,310 |
| **Sarvam v3 (today's Tamil, Malayalam, Odia)** | 30 | **6.90** | 1,380 |
| Gemini 3.8 Flash (from 1 Jan 2027) | ~36 | 8.51 | 1,702 |
| ElevenLabs v3 | ~95 | 21.84 | 4,368 |

What this means:

- **For the gap languages, the baseline is v3 at ₹6.90, not v4 Flash.** Svara Turbo cuts that by two thirds. Gemini Flash-Lite is cheaper than v3 even after Google doubles its price in January. Only Gemini Flash after January costs more, by about ₹1.60 a session.
- **Moving everyone to Svara** would save about ₹2.30 a session against v4 Flash: roughly 10% of the ₹23.4 target in `COST_TO_SERVE.md` §16.
- **ElevenLabs v3 is ruled out on cost.** It would add about ₹17 a session and undo most of the cost-to-serve work.
- **Gemini's per-second billing.** If we slow Katha down for elderly listeners, Gemini costs more. Sarvam and Svara bill per character, so slowing down costs nothing there.

## 5. Recommendation

**Keep Sarvam v4 Flash for the languages where it has a heard, good voice. Don't migrate to Svara on today's evidence. Close the Tamil, Malayalam and Odia gap now, through a one-week listening test between Sarvam v3, Svara Turbo and Gemini 3.8 Flash. Then pin each parent's voice so that no later change of vendor or model ever alters it.**

### Step 1: the listening test (about a week; costs almost nothing)

- **Languages.** Tamil, Malayalam and Odia, for both personas. Add Telugu male and Punjabi female, and Assamese and Urdu if a vendor can speak them at all.
- **Candidates.**
  - Sarvam v3, the incumbent.
  - Svara Turbo, using its native-language voice for each language.
  - Gemini 3.8 Flash, with one fixed style prompt: an unhurried, warm voice speaking to an elderly listener.
- **Hindi control.** Run Hindi for both challengers against v4 Flash, to see whether one vendor could win everywhere.
- **Listeners.** Native speakers, 55+ if at all possible; pilot parents first. The big study had no raters over 65, and older listeners may weigh clarity over expressiveness.
- **Questions.** "Which would you rather talk to every day?" and "Did you understand every word?"
- **Method.** Use the existing `listening_kit.py abtest` format: blind, with 10 pairs per language per comparison.
- **Spend.** Svara's free tier (100k characters) covers it, and Gemini costs a few rupees.

### Step 2: decide with a rule set in advance

| Result | Action |
|---|---|
| A challenger beats v3 in Tamil, Malayalam and Odia | Use it for those languages now. Two vendors is fine, because each parent still hears one voice. |
| The same challenger also matches v4 Flash in Hindi | Consider consolidating on it for **new** families: one vendor, one persona voice across every language and, if it is Svara, half the TTS cost. Decide before the pilot grows, because moving existing parents later changes their voice. |
| Neither beats v3 | Stay on Sarvam. Don't move v3 parents to v4 when Sarvam ships those voices unless the family agrees. |
| Svara and Gemini tie | Prefer Svara for its cost and India-based vendor. Ask about Enterprise terms (India residency, zero retention) before real families use it. |

**My lean.** For the gap languages, Gemini 3.8 Flash is the choice the evidence supports, and Svara Turbo is the choice the cost supports. If native elderly listeners rate Svara as highly, take Svara: it is a quarter of Gemini's January price. If Svara's Tamil or Malayalam voice sounds like a Hindi speaker, take Gemini (Flash-Lite if it holds up, to stay below v3's cost).

### Step 3: engineering, whichever vendor wins

1. **Pin the voice per parent.** Store `vendor / model / voice` on the profile at onboarding, not just language and gender. Changing `TTS_MODEL` or the voice table then affects new families only. Moving an existing parent becomes an explicit, per-family action.
2. **A vendor-neutral TTS adapter.** Turn `adapters/sarvam_tts.py` into one interface, `synthesize(text, voice) -> audio`, with one implementation per vendor. Keep `tts_check` and the usage ledger in the shared layer, and add each vendor's rate to `core/pricing.py`.
3. **A fallback that keeps the voice.** Retry the same voice first. Fall back across vendors only on a hard error, to a voice of the same persona.
4. **Finish the unheard v4 voices.** Run `listening_kit.py voices` for the 12 v4 voices nobody has heard yet (Marathi, Kannada, Gujarati, Bengali, Telugu female, Punjabi male), as §10 already requires.

### What would change this recommendation

- Svara publishing per-language quality numbers, or appearing in an independent study.
- Sarvam announcing a date for Malayalam and Odia on v4 Flash, and conversation-style Tamil voices.
- Gemini's January price, or ElevenLabs v4's Indian coverage and price, once they are published.

---

## Sources

- Kenpath: [svara-tts-v1 model card](https://huggingface.co/kenpath/svara-tts-v1) · [launch blog (27 Oct 2025)](https://huggingface.co/blog/kenpath/svara-tts-open-multilingual-speech-for-india) · [Kenpath Svara page](https://www.kenpath.io/en/open-source/svara) · [Kenpath Labs](https://kenpathlabs.com/) · [pricing](https://kenpathlabs.com/pricing) · [API docs](https://docs.kenpathlabs.com) · [voices & languages](https://docs.kenpathlabs.com/voices) · [TTS endpoint](https://docs.kenpathlabs.com/text-to-speech) · [OpenAPI spec](https://api.kenpathlabs.com/openapi.json) · [inference repo](https://github.com/Kenpath/svara-tts-inference) · [svara-python 0.2.0 PR](https://github.com/kenpath-labs/svara-python/pull/2) · [Hugging Face org](https://huggingface.co/kenpath)
- Real-world use: [COSS-India VoicEra PR #134](https://github.com/COSS-India/VoicEra/pull/134) · [MahaVISTAAR launch (Elets)](https://egov.eletsonline.com/2025/05/maharashtra-launches-mahavistaar-ai-to-digitally-empower-farmers-with-ai-driven-services/)
- Sarvam: [Bulbul model docs](https://docs.sarvam.ai/api/getting-started/models/bulbul) · [TTS API & pricing](https://www.sarvam.ai/apis/text-to-speech) · [AlphaSignal on Bulbul V4](https://alphasignal.ai/news/sarvam-ai-ships-bulbul-v4-to-bring-emotional-voice-to-11-indian-languages)
- Independent evaluations: [Preferences of a Voice-First Nation (arXiv 2604.21481)](https://arxiv.org/pdf/2604.21481) · [PSP accent benchmark (arXiv 2604.25476)](https://arxiv.org/pdf/2604.25476) · [Pramiteeh benchmark](https://github.com/Pramiteeh/pramiteeh-tts-benchmark) · [Caller Digital Indic TTS benchmark](https://caller.digital/blog/indic-tts-benchmark-bulbul-elevenlabs-sarvam-google-ai4bharat-2026)
- Google: [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Gemini 3.8 Flash TTS](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-tts) · [Gemini-TTS in Cloud TTS](https://docs.cloud.google.com/text-to-speech/docs/gemini-tts) · [Cloud TTS pricing](https://cloud.google.com/text-to-speech/pricing?hl=nl) · [Chirp 3 HD](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd) · [measured Gemini tokens/sec](https://mxchat.ai/gemini-tts-pricing-tokens-per-second/) · [CellCog on 3.8 Flash TTS arena rank](https://cellcog.ai/blog/gemini-3-8-flash-tts/)
- ElevenLabs: [supported languages](https://elevenlabs.io/docs/help-center/other/what-languages-do-you-support) · [API pricing (Puter)](https://developer.puter.com/tutorials/elevenlabs-api-pricing/) · [v4 launch](https://aiweekly.co/alerts/elevenlabs-ships-v4-and-v4-turbo-speech-models-with-90-languages-10-second) · [22 Indian languages plan](https://www.newsbytesapp.com/news/science/elevenlabs-to-add-all-22-indian-languages-in-eleven-v4/tldr)
- Others: [Smallest Lightning v3.1 Pro changelog](https://docs.smallest.ai/models/changelog/lightning-v-3-1/2026/7/3) · [Indian-language provider coverage (Edesy)](https://edesy.in/docs/voice-agent/languages/indian-languages)
- Demographics and FX: [India Ageing Report 2023 (Factly)](https://factly.in/data-india-ageing-report-2023-highlights-challenges-of-demographic-shift) · [USD/INR, 16 Oct 2026](https://dollarrupee.in/)
