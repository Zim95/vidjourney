# Important Fitz Functions

## Document Level

- `document.get_toc()` — Returns the table of contents (chapter and section hierarchy).
- `document.metadata` — Returns metadata such as title, author, and creation date.
- `len(document)` — Returns the total number of pages.
- `document.load_page(page_number)` — Loads a page object from the PDF.
- `document.extract_image(xref)` — Extracts raw image bytes for a specific image reference.
- `document.embfile_names()` — Lists embedded files in the PDF, if any.

## Page Level

- `page.get_text("text")` — Extracts plain text from the page.
- `page.get_text("blocks")` — Returns text grouped into layout blocks (useful for paragraph detection).
- `page.get_text("dict")` — Returns structured layout data including blocks, lines, spans, fonts, and positions.
- `page.get_text("rawdict")` — Returns detailed layout data including character-level information.
- `page.get_images()` — Returns metadata for all images present on the page.
- `page.get_drawings()` — Detects vector graphics like lines, rectangles, arrows, and diagram shapes.
- `page.find_tables()` — Attempts to detect table structures and extract rows and columns.
- `page.get_links()` — Returns hyperlinks and internal document references.
- `page.rect` — Returns page dimensions and bounding box.
- `page.get_pixmap()` — Renders the page as an image.
- `page.annots()` — Returns annotations like comments, highlights, or notes.
- `page.first_annot` — Returns the first annotation on the page, if present.
- `page.get_textbox(bbox)` — Extracts text within a specific bounding box.
- `page.search_for(text)` — Finds coordinates of a specific text string on the page.
- `page.get_textpage()` — Creates an internal text representation object for faster repeated extraction.

## Layout / Geometry Terms

- `bbox` (bounding box) — Coordinates of an element on the page.
- `block` — A logical text or image region on the page.
- `line` — A line of text inside a block.
- `span` — A text segment with the same font style.
- `font` / `size` / `flags` — Metadata useful for detecting headings, bold text, or captions.
- `image block type` — Indicates a layout block contains an image instead of text.
- `drawing path objects` — Vector shape paths used in diagrams.