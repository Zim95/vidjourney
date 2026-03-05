PDF Reader:
------------
1. First we need to be able to read a PDF. We need to use pymudf for that. This file is pdf_reader.py
2. Next we need to clean up data. Manage line breaks.
    - Broken line breaks
    - Hyphenated words across lines
    - Headers and footers repeating
    - Page numbers
    - Figure captions
    - Code blocks
    - Tables
    - Images
    - Multi-column layouts

3. Our goal is to find everything. We need the following:
    - Heading - This will be our metadata
    - Page numbers - This will be our metadata
    - Text - Actual Content. These need to be translated to DSL with animation.
    - Images - Actual Content. These need to be translated to DSL with animation just like text. The text following the image will help us understand whats going on in the image.
    - Code Blocks - Actual Content. These need to be translated to DSL with animation just like text. The text around the code block will help us understand whats in the code block.
    - Tables - Need to turn them into images.
    - Hyphenated words across lines - Understand and come up with text representation.
    - Special Characters - Understand and come up with text representation.

4. This will be done in text_cleaner.py
5. Now we split this into chapters in chapter_splitter.py

The idea is to read a pdf and turn it into structures. All of this should happen inside ingestion directory.


Semantic Layer:
----------------
Next we need a semantic layer. Which organizes the json into structured json.
```
{
  "chapters": [
    {
      "title": "...",
      "number": "3",
      "elements": [
        { "type": "heading", ... },
        { "type": "paragraph", ... },
        { "type": "code_block", ... },
        { "type": "image", ... },
        { "type": "caption", ... },
        { "type": "table", ... },
        { "type": "list", ... }
      ]
    }
  ]
}
```
This should be our target json.

Rules for semantic layer:
Overall Goal

Convert layout JSON (blocks, spans, bbox, fonts) into semantic elements.

Output must represent meaning, not layout.

Every element must have: type, text (if applicable), page number.

Preserve original order after reading-order normalization.

Global Preprocessing Rules

Fix hyphenated words split across lines.

Merge lines that belong to the same paragraph based on vertical distance and alignment.

Normalize whitespace.

Remove repeating headers and footers before classification.

Normalize reading order for multi-column layouts before semantic classification.

Reading Order Rules

Cluster blocks by horizontal position (x coordinate).

If two dominant x clusters exist, treat as multi-column.

Sort blocks top-to-bottom within each column.

Merge columns left-to-right.

Only then run semantic classification.

Heading Detection Rules

Collect all unique font sizes across document.

Rank them from largest to smallest.

Largest sizes are candidates for level 1 headings.

Next distinct size down is level 2, and so on.

If text matches numbering patterns like “1”, “1.1”, “1.1.1”, “Chapter 3”, increase heading confidence.

If text is short (less than 10 words) and large font, increase heading confidence.

If text is centered and large font, classify as chapter title.

If text is all caps and large font, classify as heading.

Assign heading level based on relative font size rank.

Extract numbering separately from heading text if present.

Paragraph Detection Rules

Default classification for body-sized font blocks.

Consecutive blocks with same font size and similar left alignment should be merged.

Vertical gap threshold determines paragraph boundary.

Paragraph must not be monospace.

Paragraph must not match list, heading, caption, or code rules.

Store merged full text as one paragraph element.

Code Block Detection Rules

If font family contains “Mono” or similar, classify as code block.

If lines have consistent indentation patterns, classify as code block.

If symbol density is high (many braces, semicolons, operators), increase code confidence.

If block is surrounded by extra vertical spacing, increase confidence.

Merge consecutive code-like blocks.

Preserve exact whitespace and line breaks inside code block.

Do not merge code blocks with paragraph text.

Image Detection Rules

Use extracted image metadata from layout layer.

Create an image semantic element for each detected image.

Preserve bounding box and page number.

Do not treat image as paragraph.

Search for nearby caption below image.

Caption Detection Rules

If text matches patterns like “Figure 3.1”, “Fig.”, “Table 2.1”, classify as caption.

Captions are usually smaller font than body text.

Captions usually appear directly below an image or table.

Attach caption to nearest preceding image or table element.

Do not treat captions as paragraphs.

Table Detection Rules

If text blocks align in grid-like vertical columns, classify as table region.

If many blocks share aligned x coordinates in multiple rows, increase table confidence.

For initial version, convert entire table region into a single table element.

Represent table as image reference or structured rows if reconstruction is possible.

Do not merge table content into paragraphs.

List Detection Rules

Detect bullet markers such as “•”, “-”, “*”.

Detect numbered list patterns like “1.”, “a)”, “(i)”.

Consecutive blocks with similar indentation and bullet pattern form one list.

Determine ordered vs unordered based on marker pattern.

Remove bullet marker from stored text but preserve list order.

Store items as array under one list element.

Header and Footer Detection Rules

If identical or near-identical text appears on more than 70 percent of pages at same vertical position, classify as header or footer.

If text is very close to top margin and repeats, classify as header.

If text is very close to bottom margin and repeats, classify as footer.

Exclude headers and footers from main content elements.

Footnote Detection Rules

If small font text appears at bottom of page and separated by horizontal line, classify as footnote.

Footnotes should not merge with paragraph text.

Store as separate footnote element with reference if detectable.

Inline Emphasis Rules

If span has bold font within paragraph, mark that segment as emphasized.

If span is italic, mark as emphasis.

Preserve inline formatting metadata inside paragraph element.

Hyphenation Rules

If a line ends with hyphen and next line starts with lowercase letter, merge and remove hyphen.

If hyphen is followed by uppercase or space, treat as real hyphen.

Do not break words incorrectly during merge.

Special Character Rules

Normalize unicode quotes to standard quotes.

Preserve mathematical symbols.

Preserve programming symbols exactly in code blocks.

Replace non-printable characters.

Semantic Element Structure Rules

Every element must include: type and page.

Text-based elements must include: text.

Heading must include: level and optional numbering.

Image must include: bbox and optional caption.

List must include: ordered flag and items array.

Code block must preserve raw formatting.

Table must include either structured rows or image reference.

Chapter Grouping Rules

Use top-level heading (largest font) as chapter boundary.

Group all following elements under chapter until next chapter-level heading.

Store chapter title and chapter number separately.

Validation Rules

No layout-specific fields (like spans or raw bbox arrays for text blocks) should leak into semantic layer except where needed (image/table).

Paragraphs must not contain header/footer text.

Code blocks must not contain merged paragraph text.

Captions must not be standalone if attached to image.

Preserve original content order after reading-order normalization.

Final Goal

Output must represent clean document structure.

AI layer should only see semantic elements, not layout artifacts.

Semantic JSON must be deterministic and reproducible.

No AI used at this stage. Only rule-based logic.

This gives Copilot a clear deterministic transformation spec from layout JSON to semantic JSON.

Next steps:
-----------
––––––––––––––––––
STEP 1: Validate Semantic Integrity
––––––––––––––––––

Before AI touches it:

Ensure headings form a proper hierarchy (no level jumps like H1 → H4).

Ensure paragraphs are not empty or extremely short fragments.

Ensure code blocks are not accidentally merged with text.

Ensure images have captions attached.

Ensure header/footer noise is completely removed.

Ensure reading order is correct for multi-column pages.

If this fails, AI will hallucinate structure.

Only move forward if this layer is stable.

––––––––––––––––––
STEP 2: Add Structural Metadata Layer
––––––––––––––––––

AI performs better when context is explicit.

Enhance each semantic element with:

chapter_title

section_path (array like ["3", "3.2", "3.2.1"])

index_in_section

previous_heading

next_heading

You are not adding AI.
You are adding context compression.

Now every paragraph knows exactly where it belongs.

That dramatically reduces hallucination.

––––––––––––––––––
STEP 3: Chunking Strategy
––––––––––––––––––

Do NOT send entire chapters blindly.

You need chunking rules.

Chunk by:

Heading boundaries (preferred)

Max token size limit

Logical block grouping

Ideal chunk:

One section heading

Its paragraphs

Its code blocks

Its images (as references)

Never split in middle of code block.
Never split inside list.

Chunk = semantic atomic unit.

––––––––––––––––––
STEP 4: Define AI Contract
––––––––––––––––––

Before feeding AI, define what AI is supposed to do.

Examples:

Generate DSL

Summarize section

Convert to knowledge graph

Extract definitions

Generate Q&A

Rewrite into blog format

Convert to animation instructions

Without a strict contract, AI output becomes unstable.

Define:

Input schema
Output schema
Transformation rules

AI should behave like a compiler pass, not a chat model.

––––––––––––––––––
STEP 5: Deterministic Prompt Template
––––––––––––––––––

You now create a system prompt like:

"You are a compiler that converts semantic JSON into DSL animation instructions.
You must not invent content.
You must not reorder elements.
Only transform what exists."

No creativity.
No expansion.
No explanation.
Pure transformation.

––––––––––––––––––
STEP 6: Small Batch Testing
––––––––––––––––––

Feed:

One simple section

One section with code

One section with image + caption

One section with lists

Inspect failure modes.

Common failures:

Code reformatted

List flattened

Captions merged into paragraph

Headings rewritten

Extra explanation inserted

You fix via prompt constraints, not code changes.

––––––––––––––––––
So are you ready?

You are ready for controlled AI transformation.

You are NOT ready for:

Full book ingestion

Blind DSL generation

Autonomous processing

This is where systems usually collapse.

––––––––––––––––––
Mental Model

You just built the parser.

Now you're building the compiler backend.

Semantic JSON = AST
AI = transformation engine
DSL = bytecode

Treat it like a programming language pipeline.

Flow so far:
------------
pdf_reader --> text_cleaner --> chapter_splitter --> semantic_layer --> semantic_validator --> metadata_layer --> chapter_title --> chunker --> ai_contract


All Chapters:
-------------
Why is our ingestion engine only producing output for a single chapter? Dont make changes just identify.

Then tell me what we would have to do to get all chapters.