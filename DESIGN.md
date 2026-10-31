---
name: Second Look
description: The observer's almanac. Navy book cloth, page-white ink, and one rubric-red reveal.
colors:
  cloth: "#13203a"
  cloth-lift: "#2a3f68"
  page: "#eef1f5"
  ink-soft: "#aab7cd"
  rubric: "#e0402f"
typography:
  display:
    fontFamily: "Archivo, sans-serif"
    fontSize: "120px"
    fontWeight: 900
    lineHeight: 0.9
    letterSpacing: "-0.02em"
    fontVariation: "\"wdth\" 125"
  body:
    fontFamily: "Archivo, sans-serif"
    fontSize: "50px"
    fontWeight: 400
    lineHeight: 1.12
    letterSpacing: "-0.01em"
    fontVariation: "\"wdth\" 100"
  title:
    fontFamily: "Archivo, sans-serif"
    fontSize: "52px"
    fontWeight: 600
    lineHeight: 1.12
    fontFeature: "\"tnum\" 1"
    fontVariation: "\"wdth\" 100"
  label:
    fontFamily: "Archivo, sans-serif"
    fontSize: "52px"
    fontWeight: 500
    lineHeight: 1.12
    fontFeature: "\"tnum\" 1"
    fontVariation: "\"wdth\" 100"
  figure:
    fontFamily: "Archivo, sans-serif"
    fontSize: "30px"
    fontWeight: 500
    fontFeature: "\"tnum\" 1"
spacing:
  phase-gap: "22px"
  line-gap: "24px"
  log-gap: "30px"
  safe-inset: "56px"
  column-gap: "64px"
components:
  wordmark:
    textColor: "{colors.page}"
    typography: "{typography.display}"
  tagline:
    textColor: "{colors.page}"
    typography: "{typography.body}"
  schedule-lead:
    textColor: "{colors.page}"
    typography: "{typography.title}"
  schedule-rest:
    textColor: "{colors.ink-soft}"
    typography: "{typography.label}"
  phase-figure:
    textColor: "{colors.ink-soft}"
    typography: "{typography.figure}"
  moon-disc-unlit:
    backgroundColor: "{colors.cloth-lift}"
    size: "62px"
  moon-disc-lit:
    backgroundColor: "{colors.page}"
    size: "62px"
  reveal:
    backgroundColor: "{colors.rubric}"
  rings:
    backgroundColor: "{colors.page}"
---

# Design System: Second Look

## Overview

**Creative North Star: "The Observer's Almanac"**

Second Look is the same sky checked night after night and written down. The world is built from an almanac's own materials: navy book cloth for the ground, page-white ink for everything read, and a single rubric red that marks the second look. Everything is flat colour on a flat ground; discs and tabular figures carry the content, and nothing glows.

The signature is two looks. In the logo, two page-white rings overlap, and only the lens they share, what you see when you look twice, is red. On the banner, the full Moon that closes the row of phases is the red element. Red always marks the thing the second look reveals, and it is the only red. The mark is symmetric on both axes. The system deliberately refuses the space-channel default of nebula photography, neon type and glowing planets.

Density is low and calm. Each artifact says one thing in large type, with generous navy around it, and lets one precise figure row do the work of decoration.

**Key Characteristics:**
- Three achromatic-feeling materials (cloth, page, ink-soft) plus one reserved accent (rubric).
- One variable family, Archivo, used at two widths: expanded and black for the wordmark, normal width for everything else.
- Tabular figures everywhere numbers appear.
- Flat fills only: no gradients, no shadows, no glow, no strokes.
- One red reveal per artifact (the lens where the logo's rings overlap; the full Moon ending the phase row) is the one recurring device.

## Colors

A night-navy ground with blue-tinted neutrals, broken only by one warm red.

### Primary
- **Rubric Red** (`rubric`): the reveal and nothing else. On the logo it is the lens where the two rings overlap; on the banner it is the full Moon that ends the phase row.

### Neutral
- **Book Cloth Navy** (`cloth`): the ground of every artifact, full bleed.
- **Cloth Lift** (`cloth-lift`): the unlit side of a moon disc. One tonal step up from the ground, so an unlit disc still reads as a shape at phone size without needing an outline.
- **Page White** (`page`): the first impression and all primary text: the wordmark, the tagline, the lit part of every disc, the lead line of the schedule.
- **Cloth Ink** (`ink-soft`): secondary text tinted from the cloth rather than grey. Used for the phase-row figures and the second line of the schedule.

### Named Rules
**The One Red Rule.** Each artifact carries exactly one rubric-red element: what the second look reveals (the logo's lens, the banner's full Moon). Red is never a highlight, a button, an underline, a word, or decoration.

**The Tinted Neutral Rule.** Secondary tones are mixed from the cloth's own blue. Never introduce a neutral grey.

## Typography

**Display Font:** Archivo variable (OFL, bundled as `brand/Archivo-Variable.ttf`), width axis 125%, weight 900
**Body Font:** Archivo variable, width axis 100%, weights 400 to 600

**Character:** One family stretched two ways. The wordmark is a heavy, wide grotesque set in capitals and tightly leaded; the text is the same face at normal width, plain and readable. Numbers are always tabular so figures align like a printed table.

### Hierarchy
- **Display** (900, 120px, 0.9, width 125%, uppercase): the wordmark "SECOND LOOK" only, broken onto two lines.
- **Title** (600, 52px, 1.12): the lead line of a schedule or log block, in page white.
- **Body** (400, 50px, 1.12): the single plain sentence under the wordmark. Capped at 30ch with balanced wrapping.
- **Label** (500, 52px, 1.12): the supporting line of the schedule block, in cloth ink.
- **Figure** (500, 30px, tabular): the numbers logged under each disc in the phase row, in cloth ink, centred on the disc.

Sizes are at the banner's native 2560×1440 canvas; scale them with the canvas, not independently.

### Named Rules
**The Tabular Figures Rule.** Every number is set with tabular figures (`tnum`), whether in HTML text or SVG labels.

**The Two Widths Rule.** Expanded width belongs to the wordmark alone. All other text stays at 100% width.

## Layout

Artifacts are fixed-size canvases rasterised to PNG, not responsive pages.

- **Banner** (2560×1440): everything that must be read lives inside YouTube's centred 1546×423 safe area (offset 507px left, 508px top). The safe area is a two-column grid: the wordmark and sentence on the left (flexible column), the almanac log on the right (auto width, right-aligned), vertically centred, with a 64px column gap and 56px side insets. The area outside the safe zone is plain cloth.
- **Logo** (800×800): YouTube crops to a circle and shows it as small as about 48px. The mark sits inside the central disc of the square, with cloth all around.
- **Almanac row:** discs of radius 31px on a 22px gap, figures 46px below each disc's centre-line offset. The row plots seven equal steps of illumination from new to full; each figure is the day of the lunar cycle (0.0 to 14.8), computed rather than typed.
- **Rhythm:** 24px between wordmark and sentence; 30px between the phase row and the schedule.

## Elevation & Depth

None. The system is completely flat: no shadows, no gradients, no blur, no glow. The only overlap is the logo's two rings, drawn over their shared red lens; layering by paint order, not shadow. Tonal separation (cloth vs cloth-lift) does the job a border or shadow would do elsewhere.

### Named Rules
**The Flat Print Rule.** If it could not be printed with flat inks on book cloth, it does not belong here.

## Shapes

Discs, rings and text, nothing else. Circles are true circles; there are no rounded rectangles, cards, pills or frames. No strokes or outlines on any shape except the logo's two rings: elsewhere, edges come from fill contrast alone. The terminator on a phase disc is a geometric half-ellipse, so partial phases stay astronomically correct (lit side on the right, northern-hemisphere view).

## Components

### Wordmark
Two lines, "SECOND / LOOK", page white, display style. Always left-aligned, never set in red, never paired with the ring mark inline.

### Two Looks Mark (signature, the logo)
On an 800×800 cloth square, symmetric about both axes and centred: two page-white rings (radius 170, stroke 40) whose centres sit 164px apart at x 318 and 482. The lens where their outer discs (radius 190) overlap is filled rubric, and the rings are drawn over it, so the lens edges are the rings' own. There is nothing else in the mark. It is legible down to 32px.

### Almanac Phase Row
Seven discs on cloth: unlit side in cloth-lift, lit region in page white, figures in cloth ink below. The final disc, the full Moon, is filled rubric: the banner's one reveal, as the lens is the logo's. Generated from data in SVG; colours are read from the CSS custom properties so the row stays bound to the tokens.

### Schedule Block
Two right-aligned lines under the phase row: the lead line in page white at title weight, the second line in cloth ink at label weight.

## Do's and Don'ts

### Do:
- **Do** use cloth as the full-bleed ground of every artifact.
- **Do** use exactly one rubric element per artifact: the full Moon.
- **Do** use Archivo for all text, expanded and black only for the wordmark.
- **Do** set every number in tabular figures, and compute figures from real data rather than typing decorative numbers.
- **Do** separate tones by fill (cloth vs cloth-lift) instead of outlines.
- **Do** keep everything that must be read inside the platform's safe area.

### Don't:
- **Don't** use red for text, highlights, underlines, icons or a second accent.
- **Don't** add gradients, glow, blur, shadows or strokes. The one exception is the logo's two rings, which are page-white strokes (40 on 800).
- **Don't** fill the logo's rings solid: solid overlapping discs drift toward the Mastercard mark. Keep them outlined.
- **Don't** draw the rings thinner than stroke 40 on the 800px canvas; that is the minimum that still reads at 32px.
- **Don't** use nebula or starfield photography, neon type, or glowing planets.
- **Don't** introduce neutral greys or a second display face.

## Open items

- **Video visuals are not migrated.** Thumbnails (`src/thumbnail.py`) and captions and cards (`src/assemble.py`) still use Anton, Bebas Neue and Montserrat with a yellow `#FFD60A` highlight. They are outside this system until they are redesigned against it; the yellow highlight in particular conflicts with the One Red Rule.
- **Contract divergence, resolved in favour of the build:** the direction contract called for two families (a wide grotesque plus a workhorse sans). The build uses a single variable family at two widths; this file records the build.
