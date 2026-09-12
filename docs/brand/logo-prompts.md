# Katha — Logo Generation Prompts

**Concept:** "The Line and the Loop" — abstract fusion of Devanagari **क** and Tamil **க**, with the *shirorekha* (Devanagari's horizontal top bar) doing the storytelling work.

---

## 0. Read this before you paste anything

Image models — ChatGPT/DALL·E, Nano Banana (Gemini), Midjourney — **cannot reliably render Indic scripts.** If you write "combine the Devanagari letter क with the Tamil letter க," you will get plausible-looking nonsense glyphs. Two workarounds, both used below:

1. **Never name the letters. Describe the strokes geometrically.** The model draws shapes well; it just can't spell.
2. **For Nano Banana, feed it a reference image.** Gemini's image editing is far stronger than its text-to-image when scripts are involved. Type क and க large and clean (Noto Serif Devanagari / Noto Serif Tamil), white background, side by side, screenshot it, and upload that with Prompt A.

Also: treat everything these tools produce as a **sketch, not a logo.** You'll need a vector redraw (Illustrator / Figma / a designer) before this is usable at 32px or on a business card. Generated logos have wobbly curves and inconsistent stroke weights that only show up when you scale them.

---

## 1. The construction brief (hand this to a human designer too)

A single mark built from three elements:

- **A horizontal bar across the top.** This is the *shirorekha* — the line that binds letters into a word in Devanagari. It extends slightly past the mark on the right, so it reads as a line of text that continues: a story still being told.
- **A vertical stem** dropping straight down from the right portion of that bar. (This is क's stem.)
- **One continuous curved stroke** on the left, descending from the bar, hooking down and left, looping back on itself, and rising into a tapered terminal. This stroke is the skeleton of Tamil க. Together with the bar and stem, the whole thing reads as क.

The loop's enclosed counter (the negative space inside it) should be a clean, generous, slightly asymmetric shape — read as a breath or a small open page, never as a perfect circle.

**Why it works:** both letters share the same vocabulary — a curve, a vertical, a horizontal. Neither script is wearing the other as a costume. And the shirorekha *already is* a narrative device, so "storytelling" costs almost no extra ink.

**Style:** rounded terminals, humanist rather than geometric, one consistent stroke weight with slight modulation — as if drawn with a stylus in a single confident gesture. Warm, not corporate.

**Palette** (matches the existing katha.life site):

| Role | Hex |
|---|---|
| Saffron | `#F77F00` |
| Gold | `#D48500` |
| Deep indigo | `#211A61` |
| Indigo mid | `#30358B` |
| Parchment | `#F2EADD` |
| Warm white | `#F9F4EE` |

**Hard constraint:** the primary use is a **small circular WhatsApp avatar.** If it doesn't resolve at 32×32px, it has failed where it matters most.

---

## 2. Prompt A — Nano Banana / Gemini (with reference image) ← recommended

> *Upload your reference screenshot of the two glyphs first, then paste this.*

```
The reference image shows two letterforms from Indian scripts. Do not reproduce
them literally and do not treat this as text — use them only as structural
inspiration for an abstract logo mark.

Design a single flat vector-style logo mark built from three strokes:

1. A horizontal bar across the top of the mark, extending slightly past the
   composition on the right side, as if it were a written line continuing
   onward.
2. A vertical stroke dropping straight down from the right portion of that
   bar, ending in a soft rounded terminal.
3. One continuous curved stroke on the left, descending from the bar, hooking
   down and to the left, looping back on itself, and rising into a gently
   tapered tail. The loop encloses a clean, generous, slightly asymmetric
   open space.

All three strokes share a single consistent weight with subtle modulation,
with rounded terminals — as if drawn in one confident gesture with a stylus.
The overall silhouette is compact and roughly square, balanced enough to sit
inside a circle.

Style: modern Indian brand identity. Warm and humanist, not geometric or
corporate. Flat solid color, no gradients, no shading, no 3D, no outline.
Deep indigo (#211A61) mark on a warm parchment background (#F2EADD).

The mark must remain legible at 32 pixels. Absolutely no text, no letters,
no words, no script characters anywhere in the image.
```

---

## 3. Prompt B — ChatGPT / DALL·E (pure text-to-image)

```
A minimal flat vector logo mark for a brand called Katha, an Indian
storytelling company. Abstract calligraphic symbol composed of exactly three
strokes of equal weight with rounded terminals:

a horizontal bar across the top that extends past the mark on the right; a
vertical stem descending from the right end of that bar; and a single flowing
curved stroke on the left that drops from the bar, hooks downward and left,
loops back on itself, and tapers into a rising tail. The loop encloses one
clean open counter shape.

Solid deep indigo (#211A61) on flat warm parchment (#F2EADD). Centered,
generous margins, roughly square silhouette. Modern Indian brand identity
design — warm, humanist, calligraphic, confident. Flat 2D only.

No text. No letters. No characters. No gradients, shadows, 3D, bevels,
mockups, or paper texture. Single symbol only.
```

---

## 4. Prompt C — exploration sheet (generate variety, then pick)

```
A logo exploration sheet on a flat warm parchment background: six variations
of a single abstract calligraphic mark, arranged in a 3x2 grid, evenly spaced.

Each variation is built from a horizontal top bar, a vertical stem descending
from its right end, and one continuous looping curve on the left — but each
varies the proportions: vary the loop from tight to open, the stroke weight
from light to bold, the top bar from short to far-extended, and the tail from
tucked to sweeping.

All flat solid deep indigo on parchment. No text, no labels, no letters,
no numbers, no gradients, no 3D.
```

---

## 5. Canva

Canva behaves differently from the other two, and it's worth knowing how before you paste anything.

**Two different Canva surfaces:**

- **AI Logo Generator** — one free-text prompt box plus a **Style** dropdown (3D Render, Graphic Design Vector, Illustration, Sketch B&W, Sketch Color). Canva's own guidance says *words at the beginning of the prompt are weighted more heavily than those at the end*, and asks for business name, field, and brand colours. Free accounts get ~20 prompts/month; Pro/Teams/Edu get ~500 per member.
- **Dream Lab** — Canva's general text-to-image tool. Accepts **reference images** and follow-up refinement instructions ("make the stroke thicker"), and returns four options per prompt.

**Use Dream Lab, not the Logo Generator, for this concept.** The Logo Generator is tuned toward template-shaped business logos and will fight you on an abstract custom mark — and it tends to bolt on a text wordmark whether or not you asked. Dream Lab's reference-image support is the same lever that makes Nano Banana work here. Both prompts are below.

### Prompt D — Canva AI Logo Generator

Set **Style → Graphic Design Vector**. Note the mark description comes *first*, because Canva front-weights the prompt:

```
Abstract calligraphic symbol made of three strokes: a horizontal bar across
the top that extends past the mark on the right, a vertical stem dropping
straight down from that bar's right end, and one flowing curve on the left
that hooks downward, loops back on itself, and tapers into a rising tail.
Equal stroke weight throughout, rounded ends, one enclosed open space inside
the loop. Flat solid deep indigo on warm parchment.

Logo for Katha, an Indian storytelling and memory-keeping brand. Modern
Indian brand identity, warm and humanist, not corporate. Brand colours deep
indigo #211A61, saffron #F77F00, parchment #F2EADD.

Symbol only. No text, no letters, no characters, no gradients, no 3D.
```

If you *do* want the wordmark version, swap the last line for:

```
Symbol on the left with the word "Katha" in an elegant serif to its right,
in English letters only. No other text.
```

### Prompt E — Canva Dream Lab (with reference image)

Upload your screenshot of the two glyphs as a reference, set reference influence low-to-medium (you want structural inspiration, not a copy), then:

```
Abstract calligraphic logo mark inspired by the stroke structure in the
reference image, not a literal copy of it. Three strokes: a horizontal bar
across the top extending past the mark on the right; a vertical stem
descending from that bar's right end; and one continuous curve on the left
that drops from the bar, hooks down and left, loops back on itself, and
tapers into a rising tail, enclosing one clean open counter.

Single consistent stroke weight with rounded terminals, drawn in one
confident gesture. Compact roughly square silhouette. Flat vector style,
solid deep indigo #211A61 on flat warm parchment #F2EADD.

Modern Indian brand identity — warm, humanist, calligraphic. No text, no
letters, no script characters, no gradients, no shadows, no 3D, no mockup.
```

Then refine with follow-ups in the same thread: `thicken the strokes slightly`, `open up the loop`, `extend the top bar further to the right`, `remove everything except the symbol`.

### Two Canva-specific cautions

- **Output is raster, not vector.** Whatever Canva generates is a picture of a logo. You'll still need a redraw in Illustrator or Figma before this works on a 32px avatar or in print.
- **Check the licensing before you commit.** Katha is now a registered entity (Udyam), so at some point you may want exclusive rights to the mark. Canva's terms place restrictions on claiming exclusive or trademark rights over designs built from its template and stock elements — read the current terms for whichever surface you use rather than assuming a generated mark is cleanly yours.

---

## 6. Follow-up prompts once you have one you like

Paste these as edits on the chosen image (Nano Banana handles these well):

- **Simplify:** `Reduce this mark to its essential strokes. Remove any decorative detail. Increase the stroke weight slightly and open up the enclosed counter so it stays clear at very small sizes.`
- **Small-size test:** `Show this exact mark at four sizes on one parchment background — 256px, 96px, 48px, and 32px — in a horizontal row, unchanged apart from scale.`
- **Avatar lockup:** `Place this mark centered inside a solid circle, saffron #F77F00 background with the mark in warm white #F9F4EE, with generous padding. Flat, no shadow.`
- **Monochrome check:** `Render this mark in pure black on pure white, flat, no anti-aliasing tricks, no gradients.`
- **Warmth variant:** `Recolor: mark in saffron #F77F00 on deep indigo #211A61 background. Keep the shapes identical.`
- **Wordmark:** `Place this mark to the left of the word "Katha" set in an elegant high-contrast serif, both in deep indigo on parchment, optically aligned, generous spacing between mark and word.`

---

## 7. Things to reject on sight

- Any rendered text or pseudo-Indic squiggles that look like letters
- Gradients, drop shadows, glows, embossing, 3D
- An open book, a quill, a lamp, or a lotus bolted on — you already decided against the book, and the shirorekha carries the story
- Circular badge frames with the mark trapped inside (kills small-size legibility)
- Mandala patterns, paisley, "ethnic" ornament — Katha's users are contemporary Indians, not a heritage exhibit
- Anything where the stroke weights visibly disagree with each other

---

## 8. A caveat worth keeping

There's a widely-repeated story that South Indian scripts became round because a stylus drawing straight lines would split a palm leaf along its grain. Paleographers actually debate this. It's a lovely thing to say about the mark's softness in a brand deck — just don't state it as established fact.
