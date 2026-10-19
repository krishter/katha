# katha.life landing page — build brief

**Owner:** Krish · **Written:** 2026-10-15 · **For:** Claude Code
**Status:** approach and visual treatment agreed; two defaults flagged in §9 for Krish to confirm.
**Updated 2026-10-19** to the current product flow (`docs/specs/LANDING_FLOW_UPDATE.md`): she starts every conversation, cards replace the daily quote card, and her words are kept, not her voice. §4–§6 describe the page as it is now.

---

## 1. What we're building and why

Replace the coming-soon page at katha.life with a long, story-led scrolling page that explains what Katha is
by walking the visitor through the journey a family goes through with it, step by step. The goal is
**pilot sign-ups from adult children** (35–55, metro or NRI). It should feel heartfelt, not salesy.

- **Reader:** the adult child ("the child"). **Subject:** their elderly parent ("the parent"). The page alternates between the child's view and the parent's view, and the two meet at the end.
- **Reference for mechanics only:** seasats.com. Borrow its pinned scroll sections, full-screen pauses and short display lines. Do **not** borrow its product-catalogue structure. This is one story, told in order.
- **Narrative device:** *her words becoming an archive.* A single continuous "thread" carries the story: a waveform in the hero turns into words, then a card she approves, then the child's call, then a chapter, then a bookshelf.

## 2. Where it lives and how it's built

- **Replace `coming-soon/index.html`** (static, deployed on Vercel). Before restructuring, confirm which directory the Vercel project serves. Keep the same entry path unless you've verified the change.
- Keep it **a standalone static site**. Do not put it inside the Next.js app in `frontend/`, whose `/` redirects to `/family`. The marketing site and the dashboard deploy independently.
- **Stack:** plain HTML, CSS and JS. Use **GSAP 3 + ScrollTrigger**, pinned to an exact version from cdnjs. No framework and no smooth-scroll library: use native scrolling (pinning is fine, scroll-jacking is not).
- Put illustrations in **inline SVG**, hand-built in code, one file per scene under `coming-soon/art/`, inlined at build time or with `<svg><use>`. **No raster images, stock photos, clipart or AI-generated images.**
- Branch `feature/landing-story-page`, then open a PR. Never commit to `main`.

## 3. Visual system

### Palette (reuse the existing tokens in `coming-soon/index.html`)
saffron `#E8822F` · gold `#F2C14E` / deep gold `#D9A21B` · deep indigo `#221C55` / `#241D5C` · indigo mid `#3A3488` ·
parchment `#F3EDE1` · warm white `#FAF6EE` · rose `#D0566E` · rose soft `#F6DCD8` · steel `#D5D8E0` · WhatsApp green `#2FA36B` (only on phone screens).
Fonts: **Playfair Display** for display lines, **DM Sans** for body (already loaded).

### Colour carries the emotional arc
| Scroll zone | Ground | Style |
|---|---|---|
| Act I (distance) | deep indigo | **Abstract** |
| Act II–IV (her days with Katha) | brightens through a dawn gradient into parchment | **Paper-cut** |
| Act V (archive and future) | darkens back to indigo, warm-lit | **Abstract** |
| Act VI (invitation) | parchment | type-led |

The transition from indigo to parchment is tied to scroll position, not a hard cut between sections.

### Three styles, each with a defined job
Drafts for all three are in `docs/brand/landing/`. Match them:
- **Paper-cut** (`style-paper-cut.svg`): flat fills, a single soft drop-shadow (`dy 6, blur 7, indigo @18%`), light grain overlay (≤7% opacity). Used for scenes with people.
- **Abstract** (`style-abstract.svg`): concentric sunrise bands, a dotted texture (dot pitch 24px), and objects drawn as symbols (clock, glasses, tumbler). Used for time and distance.
- **Single line** (`style-single-line.svg`): a 3px indigo stroke over soft colour blobs. Used **only as the thread**, the continuous line that runs between scenes and draws itself on scroll (`stroke-dashoffset` driven by ScrollTrigger).

### Character sheet (define once in `art/characters.svg` as `<symbol>`s, then reuse)
- **Her (the parent):** grey hair in a bun, round reading glasses, saffron saree with a gold border, warm brown skin. No facial features beyond the glasses. She stays recognisable across every scene she appears in.
- **The child:** adult in their 40s, indigo shirt, shown from behind or in profile, phone in hand, in a city flat window or at an airport gate.
- **The grandchild:** about 9 years old, headphones round the neck (not on: no voice is kept to play). Appears only in the final bookend.

### Objects that carry the emotion
Steel tumbler, reading glasses on a folded newspaper, an old transistor radio, an arched window with morning sun, an armchair, a phone propped against a jar. Rangoli or kolam patterns appear **only** as faint line texture.
Avoid imagery that is clearly from one region; the audience spans languages.

### Guardrails
Keep it restrained, with lots of empty space, so it doesn't read like a children's book. The writing carries the feeling and the art sets the mood. No emoji or icon fonts.

## 4. The scroll, act by act

Copy below is **draft**: use it as written, and mark it with `<!-- COPY: draft -->` so Krish can find and edit it.
"Pin" means the section stays fixed in place while scrolling progresses through its steps.

### Act I — The distance *(Abstract, indigo)*
1. **Hero** (full screen). A faint waveform pulses. Line: *"Amma has told the Bombay Diwali story a dozen times. Nobody has written it down."* Below it, a small line: *Katha keeps your parent's stories, in their own words.* A scroll cue at the bottom.
   *Motion:* the waveform breathes slowly. On scroll, it stretches into the thread line that leads down the page.
2. **Pinned, three lines in sequence:** *You call on Sundays. / She says she's fine. / There are questions you've never thought to ask.* Abstract art: two clocks in different time zones, and a phone with the call ended.
3. **What fades** *(the bridge: why this matters).* Opening line: *"And the answers live in one place."* Then, pinned, one detail per scroll step, each fading as the next appears:
   *The name of the village. / Why they left it. / The song she hummed while cooking. / How Appa really proposed.*
   Closing line, on its own: *"Stories don't vanish all at once. They fade a detail at a time — not because no one cares, but because no one asked."*
   *Motion:* as each line fades, its letters break into dots that drift down and gather at the bottom of the screen.
   *Tone:* gentle, never fear-based. No "before it's too late", no mention of illness or death, and no guilt aimed at the child. The fault is "no one asked", not "you didn't".
4. **Katha, introduced** *(the dawn starts here).* The drifting dots gather into the voice waveform, and the Katha mark (க/क) appears.
   *"Katha asks."*
   *"A warm voice on WhatsApp that listens whenever your parent feels like talking, in their language — and keeps every story they tell."*
   A small line under it, *"Here's how it works,"* leads into Act II. The indigo background starts warming here.

### Act II — The beginning *(dawn → parchment, Paper-cut; a phone mockup pinned on the right on desktop)*
3. **You set it up in about five minutes.** Her name, her language, when you usually call her, a few hints ("Amma grew up in Madurai"). Setup mock: *Her name: Amma · Her language: Tamil · You usually call her: Sundays, 11 am · A few hints*, and the button *Send her the link*. *Art:* the child at a window, phone in hand.
4. **She decides.** *"You send her a link. When she taps it, Katha introduces itself — honestly, as an AI — and asks if she'd like to talk, in her language. She says yes in her own words. If she doesn't, Katha stays silent."* Give this its own full-width moment.
5. **The next morning, she presses the mic and starts talking.** *"It's a voice, not an app. She talks when she feels like it, and Katha always answers."* She leans towards the phone and speaks; the phone is not ringing. The pinned phone plays her voice note first, then Katha's reply, then the "listening…" state. Katha's bubble never comes before hers.

### Act III — The days *(Paper-cut)*
6. **She talks. Katha remembers.** One exchange where Katha brings up something she said last week ("Last time you mentioned the river house…"). Then: *"And before she goes, Katha tells her what it has kept safe."*

Beat 7 (the quote card that reached the child after every conversation) was removed on 2026-10-19. The card now appears in Act IV, after she has approved it.

### Act IV — Her story, her say *(the emotional peak, Paper-cut)*
8. **Something worth sharing takes shape.** *"From what she's told Katha, it picks one thing you might not know, in her words, translated into English."* Transcript fragments gather into a **card**, drawn like the real one (`memory_cards/generator.py`, `render_conversation_card`): the hook as the header (*Did you know Amma could hear Appa's cycle bell long before she saw him?*), her words, an *On your next call* box with the question itself (*Where was Appa cycling home from?*), and the footer *Shared with Amma's permission · translated from Tamil*. Label it *Illustrative*.
9. **Full-screen moment:** *"Katha reads it to her first, in her language. No card is sent to you until she says yes. Your family can't change a word. It's her story. It stays hers."* Art: her glasses on the card, and Katha's voice note beside them, because it is read *to* her. Once the dashboard shows only what she approved (LANDING_FLOW_UPDATE D1), the second line may become *"Nothing reaches you until she says yes."*
10. **Then it reaches you.** *"By email and on WhatsApp, a few hours before you usually call. Up to twice a week."* Small line: *"For US numbers, by email only for now."* *Motion:* the card lifts out of her phone, follows the thread, and lands in the child's phone; an envelope lands beside it. This runs after beat 9, never before.
11. **You call. You ask.** *"Or reply with a question of your own. Katha will ask her in a coming conversation, and tell her it came from you."* Small line: *"If she'd like to share her answer, it comes back to you the same way — once she's said yes."* *Art:* the child at the city-flat window at night, phone at the ear. The scene comes before the text so the thread reaches it first.

### Act V — The archive *(darkening to indigo, Abstract)*
12. **Eight chapters fill in** as you scroll. No sub-line until D1 ships; then *"Everything she agrees to share is kept for your family, chapter by chapter of her life."* Use the eight life domains from `TECH_DESIGN.md` §2.2: Childhood & Home · Family & Ancestors · Education & Coming of Age · Career & Work Life · Love, Marriage & Family · Historical Events Witnessed · Hobbies, Passions & Talents · Wisdom & Life Lessons. If the dashboard uses shorter labels, use those instead. Book spines rise on a shelf; each spine lights up as its label appears.
13. **Bookend:** years later, the grandchild reads one of her cards on a phone, headphones round the neck. Silence, then one line: *"Her words. Her stories. Yours to keep."*

### Act VI — Invitation *(parchment, type-led)*
14. *"We're inviting a small number of families to try Katha with us."* Sign-up form (§5).
15. **Trust strip:** six short statements with simple line icons, 3 × 2 on desktop and 2 columns on phones (§6 lists what's allowed).
16. **Footer:** © 2026 Katha · a product of ONENOTSEVEN LABS (107 Labs) · Privacy · "Katha is a companion, not therapy or medical care."

## 5. Pilot sign-up form

- Keep the existing Formspree endpoint (`https://formspree.io/f/maqrorwa`) and the existing fetch-based submit with inline status message.
- **Fields:** your name · email · your WhatsApp number (optional) · parent's preferred language (select: Tamil, Hindi, Malayalam, Kannada, Telugu, Bengali, Marathi, Gujarati, English, Other) · city where your parent lives · where you live relative to them (same home / same city / different city / abroad) · when you usually call your parent (optional, `call_time`, placeholder *e.g. Sundays around 11*) · anything you'd like us to know (optional textarea).
- Button label: **"Request a pilot spot"**. Note under it: *"We'll write to you personally. We'll only contact your parent after you both agree."*
- Use real `<label>` elements, required-field validation, and work at phone width with no horizontal scrolling.

## 6. Honesty rules (non-negotiable)

- **No testimonials, user counts or outcome claims.** Remove "128 stories and counting" and "Shared with 4 family members" from the old page.
- Every name, quote and story fragment in the art is labelled *Illustrative*.
- **No claims about loneliness or health benefits.** No statistics unless Krish supplies a cited source.
- **Trust strip may say only:** "Her stories are stored in India." · "You, or she, can delete everything, anytime." · "She can stop whenever she likes." · "We never use her stories to train AI." · "She hears every card before you do." · "She can keep anything just between her and Katha."
  Do **not** say data "never leaves India". Speech and AI processing use third-party services.
- **Language:** she speaks in her own language. Family-facing stories are in English for now (MVP scope). Copy must not suggest stories are written up in her language.

Added 2026-10-19 (`LANDING_FLOW_UPDATE.md` §5):
1. Never say Katha calls, rings, buzzes, reminds or messages her first. She starts every conversation.
2. Never say her voice is kept, played or heard later. Use "her words".
3. Card frequency is "up to twice a week". Never "every day" or "after every conversation".
   - 3a. Cards reach US (+1) numbers by email only while the card template is Marketing. Don't promise WhatsApp to every child.
4. Never say the card is in her language. It is English, labelled as translated. Only the read-back is in her language.
5. Never promise the child an answer to their question, or say when it will be asked.
6. Don't say "no one edits it". Say the family can't change it.
7. "Nothing reaches you until she says yes" only once the dashboard shows approved content only (D1). Until then, "No card is sent to you until she says yes".
8. Don't describe first-person Stories or a book as something families get now.

**Depends on cards being on.** Beat 6's "kept safe" line and the trust line "She can keep anything just between her and Katha" are true only for families with conversation cards switched on (the recap and private marking run only then). Every new pilot family should get cards on; if that changes, change these lines.

## 7. Motion, accessibility, performance

- **`prefers-reduced-motion`:** no pinning and no scroll-driven animation. Sections stack and every illustration shows in its final state. The page must read completely without JS.
- **Mobile first:** most visitors arrive from a forwarded WhatsApp link. At ≤768px, nothing pins side by side: the phone mockup sits inline above its text. Use 16px side gutters and no horizontal page scroll.
- Text contrast at least 4.5:1, including on the indigo sections. Visible focus states and a skip-to-form link.
- **Budget:** LCP < 2.5s on simulated Slow 4G; page weight under 600 KB excluding fonts. Load fonts with `display=swap`. Load GSAP with `defer`.
- `<html lang="en">`. Decorative SVGs get `aria-hidden="true"`; scene SVGs get `role="img"` plus an `aria-label`.
- **Meta:** title, description, and an Open Graph / WhatsApp link-preview image (1200×630, a static render of the hero art). Keep the existing favicon or mark.

## 8. Done means

- [ ] All 16 beats implemented with art in the agreed styles and the colour arc working end to end.
- [ ] The thread line draws continuously from the hero to the bookshelf on desktop.
- [ ] Playwright screenshots at 390×844 and 1440×900 for every act, plus a reduced-motion run, attached to the PR.
- [ ] Form submits to Formspree and shows success and error states (test with one real submission).
- [ ] Lighthouse mobile: Performance ≥ 85, Accessibility ≥ 95.
- [ ] Every draft copy line marked; every *Illustrative* label in place; none of the banned claims from §6.
- [ ] PR description lists anything you couldn't match from the style drafts.

## 9. Open items for Krish

1. **Sound (default chosen: include).** Add an optional "Hear Katha" button in Act II, muted until pressed. It plays `coming-soon/audio/katha-sample.mp3`, a short voice sample Krish generates and approves (scripted greeting, in the voice new families hear: Gemini, the default persona voice in `media/tts.py`, not Bulbul). **If the file is missing, hide the button.**
2. **Sign-up channel (default chosen: form only).** No WhatsApp click-to-chat link until Krish provides a number for it.
3. ~~**Check product claims against what's live before launch.**~~ Done 2026-10-19 (`LANDING_FLOW_UPDATE.md`): the page now describes cards approved after a read-back, sent up to twice a week, and a parent who starts each conversation.
4. **Final copy pass:** all `COPY: draft` lines.

## 10. Out of scope

Pricing, the family dashboard, login, blog or press pages, Hindi/Tamil versions of the site, analytics beyond Vercel's built-in.
