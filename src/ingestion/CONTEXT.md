Ingestion Basic:
----------------
1. Read using Fitz.
2. Identify each structure - Headings, Text, lines, image, pagenumber, headerfooter, etc.
3. All important functions of fitz are in important_functions.md
4. Normalize coordinates
    - PDF coordinates differ across pages.
    - Store everything in a normalized structure: page_number, bbox (x0, y0, x1, y1), width, height etc. So that we can have a relative layout position. 
        - Example, 12% from left rather than (0, 76).
        - Because (0, 76) might mean one position for one page size and a different position for another page size.
    -This lets us detect: headers, footers, captions, objects near images etc.
5. Then maintain the global read order.
    - Every page has its own reading order. The order in which elements appear in the pdf.
    - When we combine all the pages, we need to maintain a global reading order.
    - Each page starts its own reading order from 0.
    - If we combine pages directly, page 2 can also have index 0, 1, 2...
        which overlaps with page 1.
    - Stride is a fixed gap we reserve for each page.
    - Example with stride = 100000:
        - page 1 uses 0..99999
        - page 2 uses 100000..199999
        - page 3 uses 200000..299999
    - This guarantees later pages always have larger reading-order values
        than earlier pages.


Section Detection:
-------------------
1. So we have identified the page elements of each page from ingestion basic.
2. Next we need to get all the headings of each page.
3. Then build sections. From one heading to the next is one section.
4. Once we detect sections, we can let the user decide which ones to keep and which ones to remove.
5. For now, we can ask for the required sections in the terminal. Since there are a lot of sections, we should be able to give it groups. Kind of like, keep section (1, 5), (10, 12).
    - Basically pass in tuples.
6. For development purposes, we can keep the section detection for DDIA book in a file and use that directly.
7. Lets group all elements from one header to the next into a section. Lets number the sections.
8. The same page might have multiple sections right? So we are not getting elements from the same page into sections. Thats incorrect.
    We need to use read_order_index, all elements starting from the reading order of heading including the heading itself all the way to the reading order of another heading.


Noise Removal:
-------------------------
1. Header and Footer may have noise. Unwanted images, and all that may act as noise.
2. We need to eliminate those things from each chapter, only keep whats required in the chapter.
3. Can happen parallely for each chapter.
4. Noise removal strategy.
    For each page:
    - Remove repeating header blocks
    - Remove repeating footer blocks
    - Remove page numbers
    - Remove decorative vectors
    - Fix hyphenation
    - Reflow multi-columns
    - Tag captions (don’t delete) ----> Captions should be tagged to images, tables, etc.


Output of Section:
-------------------
1. We create a directory inside pipeline called sections.
    - Here we store each section with elements in their reading order.
    - The format should be
        ```
        section_number: <section_number>
        page_number: <page_number>

        HEADING <text>
        PARAGRAPH <text>
        IMAGE <location>

        etc. and so on.... in whatever reading order they appear.
        ```
2. We should also create directories: pipeline/sections/resources.
    - Inside this we need to create images, code_blocks, drawings, tables, etc.
    - These are our extracted resources.
    - The format should be <section_number>_<page_number>_<resource_name>.<extension>
        example, 25_22_image.png This is the image.
    - We need to refer to these locations inside our sections file.
        Example,
            ```
            IMAGE pipeline/sections/resources/images/25_22_image.png
            ```


Fixes:
-------
1. Detect actual image using xref. Extract and store actual image.
2. Our code detection, detects all code but also detects some text as code.
    - We found that pygments can help us out.
    - Installing pygments for the same.

CHATGPT HELP with code detection:
---------------------------------
Start with:

✔ Pygments + heuristics

Then later add:

✔ Tree-Sitter for language grammar confirmation

This will make your code detection far more precise with minimal effort.


Turns out we can use something like this:
if is_code_candidate:
    if pygments_confirms or tree_sitter_confirms:
        label “code_block”
    else:
        label “text”

Example, 
from tree_sitter import Language, Parser

Language.build_library(
  'build/my-languages.so',
  ['tree-sitter-python', 'tree-sitter-java', …]
)

parser = Parser()
parser.set_language(PY_LANGUAGE)
tree = parser.parse(bytes(code_str, "utf8"))

If parse tree has proper structure, it’s likely code.


ChaptGPT Code detection pipeline:
---------------------------------
1️⃣ Ready-to-Use Function

Heuristics + Pygments Hybrid Detector

Install first:

pip install pygments

Function:

import re
from pygments.lexers import guess_lexer
from pygments.util import ClassNotFound
from pygments.token import Token


SYMBOL_CHARS = r"[{}\[\]();=<>+\-*/%]"
CODE_KEYWORDS = {
    "if","else","for","while","return","class","def","public","private",
    "static","void","int","float","double","true","false","null",
    "SELECT","FROM","WHERE","JOIN","INSERT","UPDATE","DELETE",
}


def _heuristic_score(text: str) -> float:
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return 0.0

    total_chars = len(text)
    symbol_count = len(re.findall(SYMBOL_CHARS, text))
    digit_count = len(re.findall(r"\d", text))

    # Indentation pattern (common in code)
    indented_lines = sum(1 for l in lines if l.startswith((" ", "\t")))

    # Bracket balance
    bracket_pairs = [("(", ")"), ("{", "}"), ("[", "]")]
    balanced = sum(text.count(a) == text.count(b) for a, b in bracket_pairs)

    # Keyword hits
    keyword_hits = sum(1 for w in re.findall(r"[A-Za-z_]+", text) if w in CODE_KEYWORDS)

    score = 0.0
    score += min(symbol_count / max(total_chars,1), 0.2)
    score += min(digit_count / max(total_chars,1), 0.1)
    score += min(indented_lines / len(lines), 0.2)
    score += 0.2 if balanced >= 2 else 0.0
    score += min(keyword_hits / 10, 0.3)

    return score


def _pygments_score(text: str) -> float:
    try:
        lexer = guess_lexer(text)
        tokens = list(lexer.get_tokens(text))
    except ClassNotFound:
        return 0.0

    code_tokens = 0
    total_tokens = 0

    for tok_type, tok_val in tokens:
        total_tokens += 1
        if tok_type in Token.Name or tok_type in Token.Keyword or tok_type in Token.Operator:
            code_tokens += 1

    if total_tokens == 0:
        return 0.0

    return code_tokens / total_tokens


def is_code_block(text: str,
                  heuristic_weight: float = 0.4,
                  pygments_weight: float = 0.6,
                  threshold: float = 0.45) -> bool:
    """
    Hybrid code detector.
    Returns True if text likely represents code.
    """

    if len(text.strip()) < 20:
        return False

    h_score = _heuristic_score(text)
    p_score = _pygments_score(text)

    final_score = h_score * heuristic_weight + p_score * pygments_weight
    return final_score >= threshold
2️⃣ Threshold Tuning Guide

You should tune based on your PDFs.

If TOO MUCH TEXT becomes code

Increase threshold:

threshold = 0.55 → stricter
threshold = 0.65 → very strict

If code is being MISSED

Lower threshold:

threshold = 0.35 → more sensitive
threshold = 0.25 → very sensitive

If prose with punctuation is misdetected

Increase pygments weight:

heuristic_weight = 0.3
pygments_weight = 0.7

Pygments understands language structure better.

If small snippets get missed

Reduce minimum length check:

Change:
if len(text.strip()) < 20

to:
if len(text.strip()) < 8

Recommended presets

Strict Academic Books (like DDIA):
threshold = 0.55
heuristic_weight = 0.3
pygments_weight = 0.7

Programming Books:
threshold = 0.40
heuristic_weight = 0.5
pygments_weight = 0.5

3️⃣ Tree-Sitter Integration (Grammar-Level Detection)

Tree-Sitter confirms real syntax trees → very accurate.

Install:

pip install tree_sitter

You also need grammars (example Python):

git clone https://github.com/tree-sitter/tree-sitter-python

Build grammar library
from tree_sitter import Language

Language.build_library(
    "build/my-languages.so",
    ["tree-sitter-python"]
)
Parse with Tree-Sitter
from tree_sitter import Language, Parser

PY_LANGUAGE = Language("build/my-languages.so", "python")

def tree_sitter_is_python(text: str) -> bool:
    parser = Parser()
    parser.set_language(PY_LANGUAGE)

    tree = parser.parse(bytes(text, "utf8"))
    root = tree.root_node

    # Heuristic: valid code usually has multiple structured nodes
    return root.child_count > 3 and not root.has_error
Combine with Hybrid Detector
def is_code_advanced(text: str) -> bool:
    if is_code_block(text):
        return True

    if tree_sitter_is_python(text):
        return True

    return False
🔥 Best Architecture for Your Pipeline

PDF Ingestion
→ Heuristic Filter (fast)
→ Pygments Confirmation
→ Tree-Sitter Confirmation
→ Label as CODE_BLOCK

This gives:

⚡ Fast filtering
🧠 Language awareness
🌳 True syntax validation

🧠 Why This Matters for Your Project

You’re building:

PDF → IR → Scene Mapping → Visual Engine

If code detection is wrong:

❌ Code becomes narration
❌ Narration becomes code scenes
❌ Visual pipeline breaks

This layer protects your entire system.


Outcome:
--------
1. Things got worse with Tree Sitter. We are sitting that one out.
2. Just using our old heuristics and pygments.
3. Update: pygments also made things worse. Removing pygments.
4. Heuristics was the problem all along.
5. Removed heuristics.


Fixes:
--------
We changed our heuristics this is our algorithm now:
- `CodeDetection` is now score-based (no ML, no Tree-Sitter, no Pygments).
- Inputs used:
    - Monospace font signal (`courier`, `mono`, `consolas`, `menlo`)
    - Indentation ratio across non-empty lines
    - Short-line ratio (line length <= 60)
    - Symbol density (`{}[]();=<>+-*/%:,.` per character)
    - Multi-line bonus (>= 3 non-empty lines)
- Scoring:
    - Monospace: `+3`
    - Indentation ratio > `0.3`: `+2`
    - Short-line ratio > `0.6`: `+2`
    - Symbol density > `0.05`: `+2`
    - Multi-line block: `+1`
- Decision rule:
    - If total score >= `5`, classify as `CODE_BLOCK`.
    - Else classify as normal text (`PARAGRAPH` / other applicable type).
- Why this version:
    - More stable than recent hybrid experiments.
    - Easier to debug and tune with explicit thresholds.

Combining Split code across multiple blocks within same page or diff pages:
---------------------------------------------------------------------------
- Goal:
    - Merge fragmented code chunks that belong to one logical snippet before section writing.
- Where to run:
    - Run after `preclean_sections` and before final `reflow_sections` output write.
- Same-page merge rules:
    - Consider only consecutive elements in reading order.
    - If both are `CODE_BLOCK`, merge directly.
    - If `CODE_BLOCK` + tiny separator (`PARAGRAPH` with <= 1 short line), treat as split and merge.
    - Do not merge when separator looks like a real prose sentence/paragraph.
- Cross-page merge rules:
    - Check last code-like element of page N and first code-like element of page N+1 in the same section.
    - Merge when indentation/symbol style is consistent and neither side looks like a complete standalone block boundary.
    - Prefer merge when previous block ends with open structure (`{`, `(`, `[`, `:`, `\\`) or next block starts as continuation (`else`, `elif`, `except`, `catch`, `finally`, chained method/operator line).
- Hard stop conditions (do not merge):
    - New heading/subheading detected between blocks.
    - Figure/table/image/caption element appears between candidate code blocks.
    - Large prose paragraph between blocks.
    - Page transition enters a new section.
- Merge action:
    - Concatenate with newline preservation.
    - Keep earliest block metadata (`section_number`, first `page_number`, first `reading_order_index`).
    - Append origin info internally (source pages / element ids) for traceability.
- Validation checks:
    - Ensure merged block score still passes `CodeDetection` threshold.
    - Compare merge count per section and log suspiciously large merges for manual review.

Guessing the lexer and then adding indentation to the code also detecting extension:
-------------------------------------------------------------------------------------
We need to use pygments to guess_lexer. Our code_blocks have lost indentation, we need to indent them.

Also if possible we could use the actual extensions. HOw does that sound?