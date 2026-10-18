# Switch Katha's voice to Gemini TTS: plan

**Status:** Built (§4–5) on `feature/gemini-tts`; rollout (§6) not started. Decided 2026-10-18: Gemini 3.8 Flash-Lite is Katha's voice. D1 is decided; D2–D4 take the proposed answers. Section 2's tallies are optional: Krish judged the A/B himself.
**Owner:** Krish · **Written:** 2026-10-18
**Branch:** `feature/gemini-tts`, one PR, branched from `feature/gemini-tts-abtest`. Never commit to `main`; see `.claude/rules/git.md`.
**Depends on:** the `feature/cost-to-serve` PR being merged first. Both Gemini branches sit on top of it.
**Background:** `docs/proposals/tts-strategy.md`; `docs/specs/COST_TO_SERVE.md` §10.

---

## 1. Why

- Bulbul v4 Flash has no voice for Malayalam or Odia. In Tamil its voices are ads and narration styles only.
- So today a parent's voice depends on her language: v4 for 14 (language, persona) pairs, v3 for 8, and no voice at all for Assamese and Urdu.
- In the blind A/B (`listening_kit.py abtest --compare sarvam-gemini`), Krish judged Gemini 3.8 Flash TTS clearly more suitable than the production Sarvam voice.

One vendor that speaks 12 of Katha's 13 languages, with one voice per persona, removes the v3/v4 split. It also lets us steer the delivery in plain words ("unhurried, warm and gentle") rather than picking from voices built for call-centre styles.

## 2. Evidence so far, and what the switch still needs

Paste the per-language result from `listening_kit.py abscore <folder>` here:

| Language | Persona | Pairs | Gemini | Sarvam | Same | Sarvam side was |
|---|---|---|---|---|---|---|
| | | | | | | |

**A language switches only once all of these hold** (the same bar §10 set for v4):

- [ ] At least 10 blind pairs in that language, with Gemini preferred in at least as many pairs as Sarvam.
- [ ] Both persona voices heard in that language: `Sulafat` (female) and `Achird` (male). Both were chosen on paper from Google's descriptions. Nobody has heard the male voice in any language.
- [ ] For Tamil, Malayalam and Odia: at least one native listener, aged 55+ if possible. The large IIT-M study had no raters over 65.
- [ ] For Malayalam and Odia: the A/B samples, which Claude drafted, checked by a native speaker.
- [ ] Marathi: confirm Gemini speaks Marathi, not Hindi. The two share a script, and Gemini takes no language parameter.

**Languages that don't pass stay on their current Sarvam voice.** The voice table supports a mix.

## 3. Decisions needed

| # | Decision | Proposed |
|---|---|---|
| D1 | Flash or Flash-Lite | **Decided 2026-10-18: Flash-Lite** (`gemini-3.8-flash-lite-tts`), after Krish compared the two. It covers the same 12 of Katha's 13 languages as Flash (not Urdu). From 1 Jan 2027 it costs ₹5.73 a session against Flash's ₹8.51 (§7). |
| D2 | Families already speaking to Katha | **Switch them once, now**, with a short text to the family beforehand, then pin. Waiting until the pilot grows makes the eventual change bigger. |
| D3 | When Gemini fails (after retries) | **Speak that one reply in Sarvam's voice for the same persona**, rather than sending it as text. It's rare, and a different voice is better than no voice. The ledger records it, so it is visible in metrics. |
| D4 | Urdu | Gemini 3.8 Flash TTS doesn't list Urdu. **Urdu stays as it is**: text, until Sarvam grants beta access. |

## 4. Build

### 4.1 Make the adapter production-ready (`adapters/gemini_tts.py`)

The kit adapter is the base. Before production it needs:

- **Model `gemini-3.8-flash-lite-tts`** (D1), held in one constant. The kit adapter uses Flash; production uses Flash-Lite.
- **`"store": false` on every request.** The Interactions API saves each interaction by default (Google's data-logging docs). Parent stories must not be kept at Google.
- **A paid-tier key.** The key used for the A/B is on the free tier. On the free tier Google may use the content to improve its products, and it capped the run at 10 requests a day. Production needs a key from a billing-enabled project. On the paid tier, content is not used for training. Google does keep abuse-monitoring logs "for a limited period"; zero retention is available only on Vertex AI.
- **A language hint in the style string**, e.g. "…speaking Marathi". The model detects the language itself, so this is the only lever for Devanagari languages. Keep the hint only if the Marathi check in §2 shows it helps.
- **Retries and timeouts.**
  - Retry on 429 and 5xx with backoff, using the existing `post_with_retry`.
  - Set the timeout from measured latency (§6 stage 2), not the kit's 60 s.
  - Log the time each call takes.
- **Input cap.** Gemini accepts 8,192 input tokens, so Sarvam's 2,500-character truncation isn't needed. Keep a cap anyway, so a runaway reply can't produce a minutes-long voice note.
- **Unchanged from the kit:** WAV wrapping, `tts_check` with one re-synthesis, and the usage ledger rows (`vendor="google"`, `audio_seconds`, `input_characters`). Pricing is already in `core/pricing.py`.

### 4.2 One TTS entry point, with the voice pinned per parent

Today five call sites use `sarvam_tts.synthesize` directly: `orchestrator.py`, `session_initiator.py`, `webhook.py`, `media/system_speech.py` and the kit.

- **Add `media/tts.py`** with a single entry point: `synthesize(text, profile, *, purpose)`. It reads the parent's pinned voice and sends the call to the right vendor. Move every call site to it.
- **Pin the voice.** Add `user_profiles.tts_voice`: vendor, model and voice, e.g. `google/gemini-3.8-flash-lite-tts/Sulafat`.
  - It is set at onboarding from language and persona, using the voice table.
  - A migration backfills existing parents with the voice they hear today.
  - From then on, changing defaults affects **new families only**. Moving an existing parent is a deliberate, per-family action: a small admin command that records who changed what.
- **Extend the voice table** (`media/voices.py`). `Voice` gains `vendor`. A new setting, `GEMINI_TTS_LANGUAGES`, lists the languages Gemini speaks for Katha. It defaults to all 12 that Gemini supports: every Katha language except Urdu. A (language, persona) pair uses Gemini only if its language is on that list; otherwise it keeps today's Sarvam rule. Removing a language from the list is how one language goes back to Sarvam.
- **Add the flag `TTS_VENDOR=gemini|sarvam`.** It **defaults to `gemini`** (decided 2026-10-18; the paid-tier key is in place). Setting it to `sarvam` is the rollback for new families. Existing parents are rolled back through the admin command.
- **Refuse to boot misconfigured.** `validate_production_config` refuses `TTS_VENDOR=gemini` with an empty `GEMINI_API_KEY`, so a missing key fails the deploy loudly instead of every reply falling back to Sarvam.
- **Fallback (D3).** Retry Gemini first. On a hard failure, speak that reply with the persona's Sarvam voice, and record the purpose as `tts_fallback_vendor`.

### 4.3 Things that depend on the voice

- **System speech and fallback clips.**
  - The S3 key already includes model and voice, so Gemini clips are cached separately, with no collisions.
  - `preload_fallback_audio` must pre-synthesise the Gemini voices: 2 personas × the switched languages.
- **`tts_check` limits.** They were set on Sarvam clips: reject a clip with more than 3 s of silence or fewer than 5 characters a second. An "unhurried" Gemini voice speaks more slowly. Measure the speaking rate and longest pause on the A/B clips, and adjust the limits only if normal Gemini clips get close to them.
- **Persona grammar.** Layer 1 already tells the model which gendered forms to use for Katha. Check that `Sulafat` and `Achird` really sound female and male, so the voice matches the grammar.
- **Admin metrics.** `google_tts` is already a component in `core/pricing.py`. Check that it appears in `by_component` and that `tts_characters` or `audio_seconds` appear in `units`.

### 4.4 Docs and the privacy page

- **Privacy page (`frontend/app/privacy/page.tsx`).**
  - It says "All data is stored in India". That stays true, because Google doesn't store the text with `store: false`.
  - But it says nothing about processing. The reply text is already sent to Anthropic outside India, and will now also go to Google.
  - Add one plain sentence: Katha's replies are processed by AI service providers outside India to generate text and speech, and are not stored or used for training.
  - That sentence is honest, and it is needed whether or not we switch. I'm not a lawyer: confirm the DPDP cross-border wording before the pilot grows.
- **Other docs.** Update `CLAUDE.md` (tech stack), `docs/TECH_DESIGN.md` (which still says Bulbul V3), `.env.example` (`GEMINI_API_KEY`, `TTS_VENDOR`, `GEMINI_TTS_LANGUAGES`) and `docs/DEPLOYMENT.md` (the new secret).

## 5. Tests

Write these before calling the work done; see `.claude/rules/testing.md`.

| Test | Covers |
|---|---|
| `tests/test_gemini_tts.py` (update) | `store: false` in every request body; the language hint; retry on 429 and 5xx; the input cap; WAV wrapping; a ledger row with `audio_seconds` |
| `tests/test_tts_router.py` (new) | Pinned voice wins over defaults; new profile gets Gemini only for languages in `GEMINI_TTS_LANGUAGES`; Urdu unchanged; `TTS_VENDOR=sarvam` gives today's behaviour, byte for byte; default is `gemini`; production refuses `gemini` with no key |
| `tests/test_tts_router.py` | Gemini hard failure → the persona's Sarvam voice, `purpose="tts_fallback_vendor"` |
| Migration test | Backfill gives every existing profile the voice it hears today |
| `tests/test_system_speech.py`, `tests/test_fallback_audio.py` (update) | Gemini voices cached under their own key; preload covers Gemini voices |
| Existing integration tests | Orchestrator, session initiator and webhook paths all go through `media/tts.py` |

The TC-01–TC-10 eval gate doesn't cover speech, but run it anyway: the PR touches the orchestrator.

## 6. Rollout

| Stage | What | Exit condition |
|---|---|---|
| 0 | Merge `feature/cost-to-serve` | Merged; ledger live in production |
| 1 | Build §4 with `TTS_VENDOR=gemini` as the default | Tests and lint pass; PR merged |
| 2 | **Deploy.** Put the paid `GEMINI_API_KEY` in the box's `backend/.env`, then run `./scripts/deploy.sh`. New families get Gemini; existing parents keep their current voice (pinned by the backfill) | Boot check passes; one session on Krish's own profile, moved to Gemini with the admin command, sounds right and arrives without a noticeable delay |
| 3 | **Move existing families** with the admin command, after the D2 heads-up text | Families texted before the switch |
| 4 | **Watch for two weeks** in admin metrics: TTS failures and vendor fallbacks, cost per session, session length, and replies per session | Fallbacks under 1% of clips; no drop in replies per session against the two weeks before |

**Rollback.**
- New families: set `TTS_VENDOR=sarvam`.
- Existing families: run the admin command to put them back on Sarvam. That changes their voice a second time, which is one more reason stage 2 has to be thorough.

## 7. Cost

Same assumptions as the proposal's §4: about 2,300 characters of speech a session, about 2.6 minutes of audio, ₹94.95 to the dollar, and Gemini billed at the measured 32 audio tokens a second.

| | ₹ per session | ₹ per family per year (200 sessions) | Session total (with ₹23.4 on v4 Flash) |
|---|---|---|---|
| Sarvam v4 Flash (today, Hindi and English) | 4.60 | 920 | 23.4 |
| Sarvam v3 (today, Tamil, Malayalam and Odia) | 6.90 | 1,380 | 25.7 |
| Gemini 3.8 Flash, to 31 Dec 2026 | 4.26 | 851 | 23.1 |
| Gemini 3.8 Flash, from 1 Jan 2027 | 8.51 | 1,702 | 27.3 |
| Gemini 3.8 Flash-Lite, to 31 Dec 2026 | 2.86 | 573 | 21.7 |
| **Gemini 3.8 Flash-Lite, from 1 Jan 2027 (chosen, D1)** | **5.73** | **1,145** | **24.5** |

- From January, Flash-Lite adds about ₹1.10 a session, or about ₹225 a family a year, against v4 Flash, and is about ₹1.20 a session cheaper than v3. Until then it is the cheapest option in the table.
- "Unhurried" speech means more audio seconds, so the real figure will be higher than this estimate. Replace it with the measured cost from stage 2, using the ledger's `audio_seconds`.
- The ₹5,000 plan's margin question (`COST_TO_SERVE.md` §16) carries this cost.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Google changes the model, and the voice changes with it | Pin the model ID in `tts_voice`. Re-listen before moving pinned parents to a new model ID |
| The price doubles on 1 Jan 2027, and may change again | Flash-Lite (D1); budget alert on the Cloud billing account; the cost watch in stage 4 |
| Tier 1 rate limits for this model aren't published | Check AI Studio's rate-limit page after billing is enabled. Pilot volume is a handful of calls a minute |
| Latency is unmeasured (calls are not streamed) | Stage 2 exit condition. Voice notes are asynchronous, so a second or two is acceptable; ten is not |
| A second processor outside India | `store: false`, the paid tier, and the privacy page change in §4.4 |
| A Google outage | D3 fallback to the persona's Sarvam voice |

## 9. Acceptance criteria

- [ ] Every `synthesize` call in `backend/` goes through `media/tts.py`.
- [ ] Every Gemini request sends `store: false` and uses the paid-tier key; the free-tier key is revoked.
- [ ] Every profile has a `tts_voice`. Changing `TTS_VENDOR` or the voice table doesn't change any existing parent's voice.
- [ ] With `TTS_VENDOR=sarvam`, behaviour and ledger rows match today's.
- [ ] Every switched language has a filled-in row in §2 and both persona voices heard.
- [ ] Stage 2 exit conditions met and the measured cost recorded in §7.
- [ ] Privacy page, `CLAUDE.md`, `TECH_DESIGN.md`, `.env.example` and `DEPLOYMENT.md` updated.

## 10. Build notes (2026-10-18, `feature/gemini-tts`)

§4 and §5 are built. What the build decided or measured along the way:

- **The pin.** `user_profiles.tts_voice` is `vendor/model/speaker`, e.g. `google/gemini-3.8-flash-lite-tts/Sulafat`. The language comes from the profile, so one Gemini pin covers every language. A Sarvam pin that can't speak the profile's language (the language was changed) is ignored in favour of the default, rather than sending Sarvam a request it rejects.
- **Where the pin is set.** Onboarding sets it for a new profile, and again if the family changes the language or persona during setup. Re-sending setup with neither changed leaves it alone. Any other insert (scripts, seeds) gets it from the column's insert-time default. Migration `a7c2e9f4b8d1` backfills existing parents with the Sarvam voice they hear under the deploy's `TTS_MODEL`.
- **The admin command** is `scripts/set_voice.py`. It does a dry run unless given `--apply`, and `--apply` requires `--by`. Every move writes a `voice_changes` row, and DPDP deletion removes those rows with the user's other data.
- **Language hint: Marathi and Assamese only.** These are the two languages whose script another Katha language shares (Hindi, Bengali). Every other language keeps the exact style the A/B listeners heard. The Marathi check in §2 decides whether the hint stays.
- **Fallback clips and system speech** are keyed by the voice, so a Gemini parent and a Sarvam-pinned parent each hear their own voice. Startup preloads both: about 72 new Gemini clips on the first boot, 6 at a time, then served from S3. If Gemini fails, the Sarvam stand-in is used once and never cached under the Gemini key.
- **Retries.** 3 attempts on a 429, a 5xx, a timeout or a connection error, 1 s then 2 s apart. Sarvam's single retry is unchanged.
- **Measured.** `store: false` is accepted by the live API, tested on Flash-Lite in Hindi, Marathi and Malayalam. A 7-second clip took 2.6–3.9 s to come back; the timeout is 45 s until stage 2 measures full replies. Gemini A/B clips ran 8.7–19.2 characters a second with no pause over 1.3 s, so the `tts_check` limits are unchanged.
- **Metrics.** `units` gains `tts_gemini_clips`, `tts_gemini_audio_minutes`, `tts_vendor_fallbacks` and `tts_vendor_fallback_rate`, and the metrics page shows the fallback rate against the 1% target.
- **Privacy page.** It now says replies are processed outside India by Anthropic and Google, used only to answer each request, and never used for training, with abuse-monitoring copies deleted after a limited period. This wording still needs the DPDP check in §4.4.

**Still open:**
- Stages 2–4 of §6.
- The §2 checks: the male voice (`Achird`) has never been heard; Marathi; and native listeners for Tamil, Malayalam and Odia.
- Revoking the free-tier key once the paid key is live.

---

**Sources:** [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Gemini 3.8 Flash TTS](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-tts) · [Gemini data logging and ZDR](https://ai.google.dev/gemini-api/docs/zdr?hl=en) · [Gemini rate limits and tiers](https://ai.google.dev/gemini-api/docs/rate-limits?authuser=1) · [measured audio tokens per second](https://mxchat.ai/gemini-tts-pricing-tokens-per-second/)
