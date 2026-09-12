# Katha mark — prompts for Gemini and ChatGPT

**Concept:** Tamil **க** is the mark. Devanagari **क** sits inside it, clipped to the same silhouette — one blended shape, the two letters separated only by colour.

Reference plates to attach are in `docs/brand/refs/`. They aren't required, but attaching `ref-1-mark.png` sharply improves the hit rate on every prompt below.

| File | What it is |
|---|---|
| `ref-1-mark.png` | The finished mark — க in indigo, क as saffron tint + gold line |
| `ref-2-tamil-ka.png` | Tamil க alone, solid indigo |
| `ref-3-devanagari-ka.png` | Devanagari क alone, in its aligned position |
| `ref-4-construction.png` | All three side by side |

---

## Prompt A — the mark, from scratch

```
Design a single logo mark that blends two letters into one shape.

The dominant form is the Tamil letter க (U+0B95), drawn as a clean, confident,
logo-quality letterform. Its silhouette must stay correct and readable to a
Tamil reader: a horizontal bar across the top, a rectangular counter in the
upper left, a large round counter in the lower left, and on the right a stroke
that descends, bulges outward, and curls back to the left into a tail.

Inside that silhouette, and only inside it, the Devanagari letter क (U+0915)
appears in a second, warmer colour — scaled slightly smaller and positioned so
that its vertical stem sits directly on top of the Tamil letter's right-hand
stem, and its shirorekha aligns with the Tamil letter's top bar. The Devanagari
letter is clipped to the Tamil letter's outline: it must never cross or extend
past the outer edge. It reads as a faint second layer discovered inside the
first, not as a separate letter beside it.

The result is one shape, two letters, distinguished purely by colour.

Colours: deep indigo #211A61 for the Tamil letter, saffron #F77F00 at low
opacity for the Devanagari overlay, with a fine gold #D48500 line tracing the
Devanagari letter's edge so it stays legible as a letter rather than a shadow.
Warm parchment #F2EADD background.

Flat 2D vector style, solid colour, no gradients, no shadows, no 3D, no
texture, no badge frame. The symbol alone — no wordmark, no caption, no mockup.
```

---

## Prompt B — restyle what already exists

*Attach `ref-1-mark.png`.*

```
This is a finished logo mark: the Tamil letter க in indigo, with the Devanagari
letter क overlaid inside it in saffron and gold, clipped so it never crosses
the Tamil letter's outer edge.

Restyle it. Keep the outer silhouette exactly as it is — it is a correct Tamil
க and changing its proportions would turn it into a misspelling. Keep both
counters in place, keep the hook on the right, and keep the overlay clipped
inside the silhouette.

Change only the drawing quality: make the curve transitions flow continuously,
even out the stroke weight, and give the terminals a considered, designed
shape. It should read as drawn by a type designer rather than assembled from
a system font.

Flat vector, solid colours, same palette. No gradients, shadows, 3D or texture.
Symbol only.
```

---

## Prompt C — stylise the Tamil letterform (the one that matters most)

The current mark uses Noto Sans's க — a correct letter, but a stock one. This prompt goes after the shape itself.

*Attach `ref-2-tamil-ka.png`.*

```
Redraw the Tamil letter க (U+0B95) six times as a logo-quality letterform,
arranged in a 3x2 grid on one flat background.

Every version must stay a correct, readable க — same skeleton, same counters,
same hook. This is a type design exercise, not an abstraction exercise: a Tamil
reader must recognise every one of them immediately.

Vary the treatment across the six:
- geometric, built from true circles and straight lines
- humanist, with calligraphic thick-to-thin modulation
- very bold, with tight counters
- light, with wide open counters
- squared terminals
- fully rounded terminals

Flat solid deep indigo #211A61 on warm parchment #F2EADD. No gradients, no 3D,
no shadows, no labels or captions in the image.
```

---

## Prompt D — explore the overlay treatment

*Attach `ref-4-construction.png`, which shows Tamil க, Devanagari क, and the blended result.*

```
The third panel shows the Devanagari letter क placed inside the Tamil letter க
and clipped to the Tamil letter's outline.

Generate four variations of that third panel. In all four the outer silhouette
stays identical — same proportions, same counters, same hook. Vary only how the
Devanagari overlay is expressed inside it:

1. as a thin outline only
2. as a solid block of a contrasting colour
3. as a soft tint with a fine outline on top
4. as a very quiet tonal shift, barely perceptible

Nothing may spill past the outer silhouette. Flat vector, solid colours, no
gradients or shadows. No captions, labels or panel numbers.
```

---

## Follow-ups once something is close

- `Keep this exact silhouette. Make the strokes slightly heavier and open the counters a little.`
- `Keep this exact silhouette. Make the Devanagari overlay much fainter — a hint, not a block.`
- `Keep this exact silhouette. Show it flat black on white with no overlay.`
- `Show this mark at 256, 96, 48 and 32 pixels in a row, unchanged apart from scale.`
- `Place this mark centred in a solid indigo circle with generous padding, flat, no shadow.`

---

## Reject on sight

- **A க that isn't a correct க.** This is the one that matters. Check the counters and the hook against `ref-2-tamil-ka.png` before you fall in love with anything — a near-miss letterform is a misspelling, and it's the failure mode that killed the first three rounds.
- The overlay **spilling outside** the silhouette — that breaks the "one shape" idea entirely
- Gradients, glows, drop shadows, bevels, 3D, paper texture
- A circular badge frame wrapped around the mark
- Mandala, paisley or "ethnic" ornament
- Stroke weights that visibly disagree with each other

---

## What to expect

Output is raster, so nothing here is a final file — the two letters will drift out of alignment and the stroke weights will disagree under scrutiny. What these tools can genuinely contribute is **letterform styling ideas**, which is what Prompt C is for. Bring back anything you like and I'll rebuild it as clean vectors with the alignment held exact.
