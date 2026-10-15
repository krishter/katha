# katha.life landing page — build brief

**Owner:** Krish · **Written:** 2026-10-15 · **For:** Claude Code
**Status:** approach and visual treatment agreed; two defaults flagged in §9 for Krish to confirm.

---

## 1. What we're building and why

Replace the coming-soon page at katha.life with a long, story-led scrolling page that explains what Katha is
by walking the visitor through the journey a family goes through with it, step by step. The goal is
**pilot sign-ups from adult children** (35–55, metro or NRI). It should feel heartfelt, not salesy.

- **Reader:** the adult child ("the child"). **Subject:** their elderly parent ("the parent"). The page alternates between the child's view and the parent's view, and the two meet at the end.
- **Reference for mechanics only:** seasats.com. Borrow its pinned scroll sections, full-screen pauses and short display lines. Do **not** borrow its product-catalogue structure. This is one story, told in order.
- **Narrative device:** *one voice note becoming a book.* A single continuous "thread" carries the story: a waveform in the hero turns into words, then a quote card, then a first-person story, then a chapter, then a bookshelf.

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
- **The grandchild:** about 9 years old, headphones on. Appears only in the final bookend.

### Objects that carry the emotion
Steel tumbler, reading glasses on a folded newspaper, an old transistor radio, an arched window with morning sun, an armchair, a phone propped against a jar. Rangoli or kolam patterns appear **only** as faint line texture.
Avoid imagery that is clearly from one region; the audience spans languages.

### Guardrails
Keep it restrained, with lots of empty space, so it doesn't read like a children's book. The writing carries the feeling and the art sets the mood. No emoji or icon fonts.

## 4. The scroll, act by act

Copy below is **draft**: use it as written, and mark it with `<!-- COPY: draft -->` so Krish can find and edit it.
"Pin" means the section stays fixed in place while scrolling progresses through its steps.

### Act I — The distance *(Abstract, indigo)*
1. **Hero** (full screen). A faint waveform pulses. Line: *"Amma has told the Bombay Diwali story a dozen times. Nobody has written it down."* Below it, a small line: *Katha keeps your parent's stories, in their own voice.* A scroll cue at the bottom.
   *Motion:* the waveform breathes slowly. On scroll, it stretches into the thread line that leads down the page.
2. **Pinned, three lines in sequence:** *You call on Sundays. / She says she's fine. / There are questions you've never thought to ask.* Abstract art: two clocks in different time zones, and a phone with the call ended.

### Act II — The beginning *(dawn → parchment, Paper-cut; a phone mockup pinned on the right on desktop)*
3. **You set it up in about five minutes.** Her name, the time she likes to talk, her language, a few hints ("Appa grew up in Madurai"). *Art:* the child at a window, phone in hand.
4. **She decides.** *"Katha asks her separately, in her language. She says yes in her own words. If she doesn't, Katha stays silent."* Give this its own full-width moment.
5. **10:30 the next morning, her phone buzzes.** The style-draft scene. *"It's a voice, not an app."* The pinned phone plays a short WhatsApp exchange: Katha's voice note, then her reply, then the "listening…" state.

### Act III — The days *(Paper-cut)*
6. **She talks. Katha remembers.** One exchange where Katha brings up something she said last week ("Last time you mentioned the river house…").
7. **A quote card reaches your WhatsApp** a few minutes after she finishes. *Motion:* the card lifts out of her phone, follows the thread, and lands in the child's phone (two scenes linked by the line).

### Act IV — Her story, her say *(the emotional peak, Paper-cut)*
8. **A story takes shape.** Transcript fragments rearrange into a short story page written in first person ("When I was nine, we lived near the river…"). Label it *Illustrative*.
9. **Full-screen moment:** *"She reads it first. Nothing reaches the family until she says yes. And no one edits it. It's her story. It stays hers."* Just type, with one small paper-cut detail: her glasses resting on the page.

### Act V — The archive *(darkening to indigo, Abstract)*
10. **Eight chapters fill in** as you scroll: Childhood, Family, Education, Work, Love & marriage, Places, Beliefs, Wisdom. Book spines rise on a shelf; each spine lights up as its label appears. Take the exact chapter names from the dashboard / `TECH_DESIGN.md` §2.2. Don't invent new ones.
11. **Bookend:** years later, the grandchild presses play and her voice plays. Silence, then one line: *"Their voice. Their words. Yours to keep."*

### Act VI — Invitation *(parchment, type-led)*
12. *"We're inviting a small number of families to try Katha with us."* Sign-up form (§5).
13. **Trust strip:** four short statements with simple line icons (§6 lists what's allowed).
14. **Footer:** © 2026 Katha · a product of ONENOTSEVEN LABS (107 Labs) · Privacy · "Katha is a companion, not therapy or medical care."

## 5. Pilot sign-up form

- Keep the existing Formspree endpoint (`https://formspree.io/f/maqrorwa`) and the existing fetch-based submit with inline status message.
- **Fields:** your name · email · your WhatsApp number (optional) · parent's preferred language (select: Tamil, Hindi, Malayalam, Kannada, Telugu, Bengali, Marathi, Gujarati, English, Other) · city where your parent lives · where you live relative to them (same home / same city / different city / abroad) · anything you'd like us to know (optional textarea).
- Button label: **"Request a pilot spot"**. Note under it: *"We'll write to you personally. We'll only contact your parent after you both agree."*
- Use real `<label>` elements, required-field validation, and work at phone width with no horizontal scrolling.

## 6. Honesty rules (non-negotiable)

- **No testimonials, user counts or outcome claims.** Remove "128 stories and counting" and "Shared with 4 family members" from the old page.
- Every name, quote and story fragment in the art is labelled *Illustrative*.
- **No claims about loneliness or health benefits.** No statistics unless Krish supplies a cited source.
- **Trust strip may say only:** "Her stories are stored in India." · "You, or she, can delete everything, anytime." · "She can stop whenever she likes." · "We never use her stories to train AI."
  Do **not** say data "never leaves India". Speech and AI processing use third-party services.
- **Language:** she speaks in her own language. Family-facing stories are in English for now (MVP scope). Copy must not suggest stories are written up in her language.

## 7. Motion, accessibility, performance

- **`prefers-reduced-motion`:** no pinning and no scroll-driven animation. Sections stack and every illustration shows in its final state. The page must read completely without JS.
- **Mobile first:** most visitors arrive from a forwarded WhatsApp link. At ≤768px, nothing pins side by side: the phone mockup sits inline above its text. Use 16px side gutters and no horizontal page scroll.
- Text contrast at least 4.5:1, including on the indigo sections. Visible focus states and a skip-to-form link.
- **Budget:** LCP < 2.5s on simulated Slow 4G; page weight under 600 KB excluding fonts. Load fonts with `display=swap`. Load GSAP with `defer`.
- `<html lang="en">`. Decorative SVGs get `aria-hidden="true"`; scene SVGs get `role="img"` plus an `aria-label`.
- **Meta:** title, description, and an Open Graph / WhatsApp link-preview image (1200×630, a static render of the hero art). Keep the existing favicon or mark.

## 8. Done means

- [ ] All 14 beats implemented with art in the agreed styles and the colour arc working end to end.
- [ ] The thread line draws continuously from the hero to the bookshelf on desktop.
- [ ] Playwright screenshots at 390×844 and 1440×900 for every act, plus a reduced-motion run, attached to the PR.
- [ ] Form submits to Formspree and shows success and error states (test with one real submission).
- [ ] Lighthouse mobile: Performance ≥ 85, Accessibility ≥ 95.
- [ ] Every draft copy line marked; every *Illustrative* label in place; none of the banned claims from §6.
- [ ] PR description lists anything you couldn't match from the style drafts.

## 9. Open items for Krish

1. **Sound (default chosen: include).** Add an optional "Hear Katha" button in Act II, muted until pressed. It plays `coming-soon/audio/katha-sample.mp3`, a short voice sample Krish generates and approves (Bulbul voice, scripted greeting). **If the file is missing, hide the button.**
2. **Sign-up channel (default chosen: form only).** No WhatsApp click-to-chat link until Krish provides a number for it.
3. **Check product claims against what's live before launch:** parent approval before the family sees a story (decided 2026-10-11; confirm it's built), quote card delivered after each session, and Katha's voice notes in her language.
4. **Final copy pass:** all `COPY: draft` lines.

## 10. Out of scope

Pricing, the family dashboard, login, blog or press pages, Hindi/Tamil versions of the site, analytics beyond Vercel's built-in.
