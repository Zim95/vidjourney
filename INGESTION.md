# Ingestion — How I Pull a PDF Apart Without Crying

*Video-narration draft. First-person. Beats marked with `—` are natural pause points where b-roll or a clip can sit. Bracketed `[laugh track]` style cues are mine; remove if they don't fit your vibe.*

---

## Why this lives on your desktop, not in the cloud

Before I get into the pipeline itself — the question I keep getting first: **why is this a desktop app and not a website?**

Because the cheapest, fastest, most honest version of this tool runs on a single machine. The moment you slap it behind a web service, you start paying real money to solve fake problems.

Look at what this thing does. PyMuPDF chewing through 500 pages of geometry. An LLM classifying every paragraph. A text-to-speech engine making narration. A forced-aligner figuring out which word lands where. A 2D animation engine rendering 1080p60. FFmpeg duct-taping it all together. Every stage wants memory, CPU, sometimes GPU, and a *lot* of disk.

I could pay AWS to host that. Or — and hear me out — I could **already own a computer that does it for free**.

There's a second reason though, and it's the real one. **The whole pipeline is a debugger.** Every stage writes its output to a folder on disk. `sections/`, `groups/`, `timelines/`, `render/` — when something looks wrong in the final video, I walk backwards through eight folders and *see* where the truth diverged. That kind of visibility on a desktop is free. On the web you'd be paying someone to hide it from you.

— so: desktop. Single process tree. All your scratch files visible. You can yell at them. We can talk about a web frontend later, but it's a frontend over a local engine, not a replacement for it.

Now, the pipeline.

---

## What ingestion is actually solving

A PDF is a lie.

It *looks* like a document — paragraphs, headings, code blocks, a figure with a caption. To a program, it's a flat stream of drawing operations. "Place this glyph at this position. Then this one. Then this one." The structure you see is something your brain reconstructed from font sizes and whitespace, and the PDF format thinks that's *your* problem.

Ingestion's job is to climb back up that ladder. Take the flat glyph stream and rebuild the structure that was there when the author wrote the book. Heading. Paragraph. List item. Image. Code block. Table. In the right reading order. Cleaned of the junk PDFs always carry — page numbers, running headers, hyphenated breaks, citation brackets, weird ligatures.

By the end of ingestion I have a folder of `section_N.txt` files. Plain text. Looks like this:

```
HEADING Reliable, Scalable, and Maintainable Applications
PARAGRAPH The Internet was done so well that most people think...
LIST_ITEM Store data so they can find it again later
LIST_ITEM Remember the result of an expensive operation
IMAGE pipeline/sections/resources/images/1_24_images_1.jpeg
```

That's the contract. Everything downstream — the grouper, the timeline builder, the narrator, the renderer — treats the section file as *the* truth about the document.

If ingestion is wrong, everything is wrong, and you won't know where it went wrong.

So I spend a stupid amount of time making sure ingestion is right.

— let me walk you through it.

## The library — fitz, no question

I use **PyMuPDF**. Everyone still calls it `fitz` because that's its import name. It's a Python wrapper around MuPDF, the C library originally built as a PDF viewer.

Why fitz over pdfplumber, pdfminer, all the rest?

**One — it's stupid fast.** Like *order of magnitude* faster than `pdfminer.six` for the same extraction. Seconds versus tens of seconds. When you're iterating on heuristics, this matters. A lot.

**Two — it gives me geometry.** Other libraries hand you text. Fitz hands you text *plus the bounding box, the font name, the font size, the page number, the block structure*. All that metadata is what lets me say "this is a heading" — because a heading is just text that happens to be 1.25× bigger than the body. Without geometry I'd be guessing. I refuse to guess.

**Three — it has the escape hatches.** `page.get_drawings()`. `page.find_tables()`. `page.get_links()`. `document.extract_image(xref)`. Stuff the other libraries don't expose, or expose badly.

The little crib sheet of fitz calls I actually use is in [`src/ingestion/important_functions.md`](src/ingestion/important_functions.md). If you've never seen `page.get_text("dict")` before, prepare to fall in love.

## Step one — open the document, chop into chunks

```python
with open_document(pdf_path) as document:
    total_pages = len(document)
```

That's the opening sequence. One line. Now I divide the pages into chunks for parallel processing. Config says four workers; for a 200-page book that's four chunks of 50 pages each, going through a `ProcessPoolExecutor`.

Why processes and not threads? Because the work is CPU-bound. Parsing PDFs, classifying elements, building data structures — Python's GIL would put all four threads in a single-file line waiting for each other. Threading in Python for CPU-bound work is a meme. Processes get me actual parallelism on actual cores.

Trade-off: each worker re-opens the PDF. Microsecond cost. Fitz doesn't care. Moving on.

— [`ingest_pdf.py:108`](src/ingestion/ingest_pdf.py#L108) if you want to look at the code.

## Step two — every page becomes a `PageElements`

For each page I call `PageElements.detect_elements(page)`. This is where the geometric reconstruction happens. Output: one dataclass with twelve typed lists.

```
headings, paragraphs, list_items, captions, code_blocks,
images, tables, drawings, links, annotations,
headers_footers, page_numbers
```

Every element on the page lands in *exactly one* of those buckets. Every element gets:

- a `page_number`
- a `reading_order_index` — where it sits in the reading flow
- a `geometry` — both the raw bbox **and** a normalized bbox

That normalized bbox is doing serious work. Let me explain.

### Normalized coordinates — small idea, huge payoff

A PDF doesn't have one page size. Different pages can be different sizes. The same paragraph might be at `(100, 150)` on page 1 and `(95, 140)` on page 2 just because the page boundaries shifted by five points.

So instead of storing absolute coordinates and dealing with the fallout, I store both. The absolute bbox is there if I need it, but the *normalized* version is the ratio: "this paragraph starts 16.7% from the left, 18.8% from the top, takes 33% of the width, 6% of the height."

That number is the same on a 600-point page or a 1200-point page. Page size becomes irrelevant.

Why does this matter? Because now I can write rules like:

> "If a text block is in the top 8% of the page, it's a header. Bottom 8%? Footer."

That rule works on any page size, on any PDF. Written once. Generalises forever. In code at [`_append_non_image_block`](src/ingestion/page_elements.py#L368).

— normalized coordinates are the most boring, most important decision in the whole module. They're the reason none of my heuristics are hardcoded to a specific page size.

### Reading order — local vs. global, the stride trick

Each fitz page gives me elements in *its own* reading order, starting from zero. Top of page is `0`, then `1`, then `2`.

That's fine until I want to combine pages. Page 1 has indices `0..50`. Page 2 *also* has indices `0..30`. They collide. There's no global "this came before that" anymore.

Solution: **stride per page**. Page 1's reading order lives in `0..99999`. Page 2's lives in `100000..199999`. Page 3's in `200000..299999`. Each page gets a hundred-thousand-index reservation. The actual element count per page is way below that — like, two hundred max — so I never collide. A single sort on the global index puts everything in correct document order across the whole book.

— [`ingest_pdf.py:73`](src/ingestion/ingest_pdf.py#L73). Hundred thousand is wildly more than I need. I refuse to optimise this. It works.

## Step three — classify each block

For each non-image text block fitz hands me, I have to decide: heading? paragraph? list item? caption? code? page number? header? footer?

I compute the **body font size first** — the median font size across all the text on the page. That's my baseline. Then for each block I run an ordered classification:

1. **Page number?** Text is just digits, short? ("47", "238")
2. **Header or footer?** Block is in the top 8% or bottom 8% of the normalized page?
3. **Caption?** Text starts with "Figure", "Fig.", or "Table"?
4. **List item?** Text starts with `-`, `•`, `*`, or `<digit>.`/`<digit>)`?
5. **Code block?** Code-detection score says yes
6. **Heading?** Max font in this block is ≥ 1.25× the body font?
7. **Default → paragraph.**

First rule that matches wins. Notice that **heading is last**. That's deliberate. Captions and list items are often the same font size as the body, but they have *other* signals — the prefix word — that should win first. If I checked heading-by-font-size up front, "Figure 1-4" would get classified as a heading every time, because figure captions are sometimes set in slightly bigger type.

Page-number and header/footer detection are also a bit special — they're *independent* tags. A block can be both a page-number candidate *and* a paragraph fragment; the page-number tag just makes it skippable later.

— rules at [`page_elements.py:393-475`](src/ingestion/page_elements.py#L393). It is a switch statement wearing a trench coat.

## Step four — detecting code (the long sad story)

OK. Code detection. This deserves its own arc because I tried *so many things.*

**Attempt one — Tree-Sitter.** "Just parse the text with a real language grammar and see if it's syntactically valid." Sounds clever. In practice: falsely accepts text that lexes okay, falsely rejects code with one syntax error, requires a separate grammar per language, adds a heavy native dependency.

> *Tree-Sitter is in this story until it isn't.*

**Attempt two — Pygments.** "OK, Pygments is built to lex code. Let it tell us if a block is code." Pygments was *too eager*. It lexes prose into "names" and "operators" and confidently informs you that your paragraph about the Pacific Ocean is, in fact, code.

> *narrator: it was not code.*

**Attempt three — keyword heuristics.** Hand-rolled keyword counting. Tightening the threshold killed real code. Loosening it admitted footers. There is no good number.

What actually works — and it's almost embarrassing — is this:

```python
score = 0
if monospace_font:        score += 3
if indent_ratio > 0.3:    score += 2
if short_line_ratio > 0.6: score += 2
if symbol_density > 0.05: score += 2
if line_count >= 3:        score += 1
return score >= 5
```

That's it. Five inputs. Score-based. Threshold is five.

The single biggest signal is **monospace font**. Code in technical books is almost always set in Courier, Consolas, or Menlo. If a block uses one of those fonts, that's three points right out of the gate — *more than half* of what it needs to qualify. Indentation, short lines, and symbol density are the corroborating evidence. Multi-line gives a little bonus. Threshold five.

Boring. Robust. Actually works.

— [`page_elements.py:211-231`](src/ingestion/page_elements.py#L211). The 30 lines that taught me to stop being clever.

### The ML safety net

After the score-based detection, a Random Forest classifier ([`src/ingestion/ml/`](src/ingestion/ml/)) runs on each line of each code block. It uses about a dozen hand-crafted features — symbol density, indent ratio, short-line ratio, semicolon presence, brace presence, sentence-ending punctuation ratio, prose-connector keyword count — concatenated with a nomic-embed-text embedding from Ollama.

For each line in a code block, the model returns "probability this is actually code". Lines below threshold get split off into a paragraph. So when a textbook embeds an explanation sentence inside a code listing — *which happens all the time* — the ML splitter catches it and pulls it out.

Training set is about 100 hand-labeled snippets from DDIA, sitting in [`src/ingestion/ml/training_code_snippets/`](src/ingestion/ml/training_code_snippets/). Model fits in seconds. Inference is milliseconds per line.

— at one point I had Tree-Sitter *and* Pygments *and* ML all stacked. Killed Tree-Sitter and Pygments. Kept the score detector and the ML splitter. Two layers. That's the architecture. Touch grass.

## Step five — the non-text stuff

Text blocks are most of the work, but pages have other things on them.

**Images.** Fitz exposes them as blocks of type 1. I store the `xref` (the PDF's internal reference) so I can extract real pixel data via `document.extract_image(xref)`. The fallback is `block.get("image")` — fitz's already-extracted bytes. Real bytes win because rendered bytes sometimes come back colour-shifted.

**Drawings.** Vector graphics — `page.get_drawings()`. Most are decorative (a horizontal rule, a tiny corner ornament, a separator). Those get filtered out later for being microscopic. The ones that survive are actual diagrams.

**Tables.** This is the cursed one. Fitz's `page.find_tables()` returns table objects with bounding boxes and row/column counts. We *try* structured extraction — turn the table into CSV-style rows. On real technical books, this falls apart. Header detects, body cells scramble, formatting is too irregular for the heuristic.

So the strategy I landed on: **store tables as cropped images.** Find the table bbox, crop the page to that region as a PNG, save it. The video pipeline then shows the table image during narration of the surrounding text. Not searchable, not interactive, *correct.* Which is more than the CSV path was giving me.

Region selection is in [`section_writer.py:_resolve_table_clip_from_candidates`](src/ingestion/section_detection/section_writer.py#L221). It expands fitz's initial guess, scores candidate regions for "table-likeness" (aligned columns, row count, numeric density), picks the best one. A follow-up trim step walks the bottom of the crop and chops off prose that crept in.

**Links** and **annotations** I extract but treat as metadata. They get dropped during preclean. They're not visual content.

— at this point I have an array of `PageElement` objects per page, all typed, all with geometry. Now I assemble sections.

## Step six — sections, from heading to heading

A *section* is what I call the unit between two consecutive headings. The first heading on page 24 starts section 2. The next heading on page 27 starts section 3. Everything between them — paragraphs, images, code, list items, captions — belongs to section 2.

Implementation is laughable. Sort all elements globally by reading order. Find the indices of every `HeadingElement`. Slice the global stream between consecutive heading indices.

```python
for position, heading_index in enumerate(self.heading_indices):
    next_heading_index = (
        self.heading_indices[position + 1]
        if position + 1 < len(self.heading_indices)
        else len(self.ordered_items)
    )
    section_items = self.ordered_items[heading_index:next_heading_index]
    grouped_sections.append(section_items)
```

[`sections.py:71`](src/ingestion/section_detection/sections.py#L71). The reason it works cleanly is the stride trick from earlier — global reading order is monotonic, so "from heading N to heading N+1" is a *literal Python slice*. No reshuffling. No edge cases.

— for DDIA I get 224 sections out of ~500 pages.

## Step seven — filtering

DDIA has front matter, back matter, preface, acknowledgements, indices. None of that gets narrated.

The interactive version (commented out for dev) asks for ranges like `[(15, 238), (245, 260)]`. The currently active version is hardcoded to keep sections 15 through 238 — the actual chapter content.

```python
parsed_ranges = [(15, 238)]
```

That's [`section_utils.py:207`](src/ingestion/section_detection/section_utils.py#L207). The dev shortcut. When this ships, the prompt comes back and the user picks. For now I'm iterating on one book, the range is baked in, and yes I know that's "this is fine" energy, please move on.

## Step eight — preclean

Now I have my section list. Time to delete the junk.

**Things I delete entirely:**

- Links — not visual content
- Headers and footers (already tagged in step three)
- Page numbers (already tagged)
- Tiny decorative drawings — less than 0.2% of normalized page area, or two-or-fewer drawing items
- Page-artifact paragraphs that snuck through — `"Chapter 1 | 47"`, `"Page 23 | Foundations"`
- Bibliography entries — paragraphs starting with `[N]` containing a year *and* either a DOI/ISBN or two publication markers

**Things I transform:**

- **Hyphenated line breaks** — `"informa-\n  tion"` becomes `"information"`. Looks pedantic. Matters enormously for narration accuracy. Try saying "informaaaaaaaa— tion" out loud.
- **Inline citations** — `"as discussed [15] in Chapter 3"` becomes `"as discussed in Chapter 3"`. Citations do not read aloud.
- **Misclassified captions** — a paragraph starting with "Figure 1-4" that got tagged paragraph (because it was body-font sized) gets retagged caption.

Skip rules: [`section_utils.py:_should_skip`](src/ingestion/section_detection/section_utils.py#L256). Transforms: [`section_utils.py:_transform_element`](src/ingestion/section_detection/section_utils.py#L277).

Preclean is conservative on purpose. If something *might* be content, I keep it and let later stages decide.

## Step nine — code cleanup: demote + split

After preclean, some `CodeBlockElement`s are still wrong. Two passes correct them.

**Pass one — demote.** A code block whose text looks like a citation (`[44] Martin Thompson: "Memory Barriers"...`) or a running header (`309 | Reliable...`) or explanatory prose (sentences ending in `.` or `:` with prose connectors like "because", "however") gets *demoted* back to a paragraph. The logic short-circuits — if it's confidently code (multi-line, high symbol density, ends in `;` or `}`), we keep it as code. Otherwise we run the demotion checks.

[`code_cleanup.py:152`](src/ingestion/section_detection/code_cleanup.py#L152).

**Pass two — split.** The ML model. For each surviving code block, run the Random Forest on each line. Lines that score below threshold get split out into their own paragraph elements. Consecutive code lines stay grouped. Consecutive prose lines stay grouped. A block that was `[code, code, code, prose, prose, code, code]` becomes three elements: code, paragraph, code.

[`code_cleanup.py:213`](src/ingestion/section_detection/code_cleanup.py#L213).

— this catches the "explanation paragraph wedged in the middle of a code listing" pattern. DDIA does this *a lot*. Without the splitter, the explanation gets rendered as a syntax-highlighted PNG of confused English, which is — funny, actually, but bad UX.

## Step ten — merging back what the PDF split

PDFs love splitting things up. One paragraph can become two `ParagraphElement`s because there happened to be a blank line in the middle. Or it crossed a page break. Or it wrapped around a figure. One code listing can become three blocks for the same reason.

I undo all of that.

**Code merging** ([`CodeMergeUtils`](src/ingestion/section_detection/code_cleanup.py#L287)) — for each section, walk through elements. If I see two code blocks separated only by a tiny paragraph fragment (under 40 chars) or by ignorable elements (links, annotations), I merge them. Bonus signals: if the first block ends with an open structure (`{`, `(`, `[`, `:`, `,`), or the next block starts with a continuation token (`else`, `elif`, `except`, `&&`, `.`), the merge is more likely. Hard stops: a heading, a caption, a real paragraph, an image or table sitting between the two blocks — don't merge.

**Paragraph merging** ([`ParagraphMergeUtils`](src/ingestion/section_detection/paragraph_utils.py#L44)) — same idea for prose. Consecutive paragraph elements with nothing structural between them get joined. Hyphenation across the join is fixed — `"informa-"` plus `"tion"` becomes `"information"`. Tiny separator paragraphs (sub-40-char fragments, usually page-number remnants that survived earlier filtering) are skipped, not deleted — they just don't break the merge.

— this is where the quote-attribution bug from earlier in this project lives, actually. The paragraph merger is *too* enthusiastic sometimes: it joins a paragraph ending with `"—Alan Kay, in interview (2012)"` with the next paragraph, which is a totally different topic, and the result is one big merged blob. I fixed it downstream in the scene grouper. The *right* fix is to add an attribution-stop rule here. On the list.

## Step eleven — multi-column reflow (only when needed)

Most pages are single-column. Some — an index page, a sidebar layout — are multi-column. Fitz reads multi-column pages in raw page order, which means it jumps you between columns and the narrative shatters.

My fix is *conservative*. For each page I check if it *looks* multi-column: text-like elements starting at both far-left (`x ≤ 0.45`) and far-right (`x ≥ 0.55`) of the normalized width, with at least two on each side. If yes, I re-sort that page's elements top-to-bottom, left-to-right, snapping y-coordinates to a coarse grid so adjacent column lines actually sort together.

If the page looks single-column — which is most pages — I leave the existing reading order alone.

I learned this the hard way. Being clever about reflow on single-column pages broke more than it fixed. The first version of this was overcomplicated and confidently scrambled half the book. So now: only reflow when reflow is obviously needed.

Logic at [`section_utils.py:_is_likely_multi_column_page`](src/ingestion/section_detection/section_utils.py#L96).

## Step twelve — writing it out

Clean list of sections, each a list of typed elements. Time to put it on disk.

The section writer ([`section_writer.py`](src/ingestion/section_detection/section_writer.py)) takes one section and produces one `section_N.txt` file. For each element in reading order, it writes a line:

```
HEADING Reliable, Scalable, and Maintainable Applications
PARAGRAPH The Internet was done so well that most people think...
LIST_ITEM Store data so they can find it again later (databases)
LIST_ITEM Remember the result of an expensive operation
IMAGE pipeline/sections/resources/images/1_24_images_1.jpeg
CAPTION Figure 1-1. An example of an architecture...
```

Images, code blocks, tables — not inlined. They get extracted to `pipeline/sections/resources/`:

- `resources/images/<section>_<page>_images_<n>.<ext>` — actual bytes from the PDF, via `document.extract_image(xref)`
- `resources/code_blocks/<section>_<page>_code_blocks_<n>.txt` — code text, indented via Pygments
- `resources/code_block_images/<section>_<page>_code_block_images_<n>.png` — syntax-highlighted PNG render of the code, via Pygments' `ImageFormatter`
- `resources/tables/<section>_<page>_tables_<n>.png` — cropped table region
- `resources/drawings/<section>_<page>_drawings_<n>.txt` — metadata stub (we don't render decorative drawings)

The section file refers to those resource paths verbatim. Downstream picks them up and uses them.

Code blocks are interesting — I store the *text* but display the *image*. The image is syntax-highlighted, looks like a proper code listing. Pygments auto-guesses the lexer from the code content. Files get a `.txt` extension because I couldn't reliably detect Python vs Java vs SQL well enough to assign real extensions. So `.txt` everywhere. Pygments doesn't care; its auto-guess works on the bytes.

— and that's it. End of ingestion. The next stage — scene grouping — reads from `pipeline/sections/` and never touches the PDF again.

## What's tuned for one specific book

Honest disclosure: a lot of thresholds in here are calibrated against *Designing Data-Intensive Applications*. I've been using it as my test bench.

- The hardcoded section range `[(15, 238)]` is "DDIA's chapter content"
- The body-font heading multiplier (1.25×) works for DDIA's typography
- The table detection scoring is tuned on DDIA's table styles
- The bibliography filter is tuned for DDIA's citation format

Some of these already live in [`configuration.cfg`](configuration.cfg) — table tolerances, code line minimums, score thresholds. The rest should follow. Adding a per-document `book.cfg` overlay would mean I drop a PDF and a tiny config in, instead of editing constants in code. That's on the list. After everything else on the list.

## How fast is it actually

For DDIA — about 500 pages, parallelised across 4 process workers:

- Open + chunk + extract: a couple of minutes
- Section detect → filter → preclean: seconds
- Code cleanup + ML splitter: tens of seconds (ML inference via Ollama embeddings is the slowest part — each block calls out and waits)
- Paragraph merge → reflow → write: seconds
- **Total: under five minutes** from PDF to 224 section files

Most of the time is the ML splitter calling Ollama one block at a time. If I batched those embeddings, the splitter would basically vanish.

## What still trips it up

- **Quote attribution merging**, see above. Fix is a "stop merging at `—Author, source (year)`" rule in `ParagraphMergeUtils`. Currently band-aided in the scene grouper.
- **Cross-page tables.** A table spanning pages becomes two separate `TableElement`s and we don't currently stitch them. Niche but happens.
- **Decorative figures** that score above the drawing-size filter sometimes survive. Manual delete for now.
- **Headers in the middle of the page margin.** Some books put a stylized chapter header in the *center* of the top margin and the 8% rule misses it. DDIA doesn't do this. Other books will.

Listing these out means I haven't forgotten about them. We'll come back.

---

That's ingestion. PDF in, structured `section_*.txt` files out, resources alongside. Single machine, single process tree, every intermediate file on disk so I can yell at it when something looks weird.

Next up: **scene grouping** — how the LLM looks at each section and decides which paragraphs anchor which resources, which paragraphs are hiding an enumerated list, and which become standalone narrated beats. That's where the project stops feeling like a parser and starts feeling like a director.

But that's a different video.
