"""
LLM-based storyboard generator.

Takes a section file and asks the LLM to produce a visual storyboard —
describing exactly what appears on screen and in what order.
"""
import requests
from pathlib import Path

from src.config.constants import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
)


PROMPT = """\
You are a storyboard artist for an educational video. You will receive a section from a textbook.

Your job: break it into scenes and describe EXACTLY what the viewer sees on screen for each scene. Think of it like a PowerPoint presentation — each scene is one slide.

The section has these types of content available:
- PARAGRAPH and LIST_ITEM: text content
- IMAGE: a displayable image (file path provided). Code blocks have also been rendered as images.
- TABLE: a displayable table image (file path provided)
- HEADING: a title or chapter heading
- CAPTION: describes a nearby image or table

For each scene, describe:
1. What visuals appear on screen (text, shapes, images, comparisons, lists, diagrams)
2. What the narrator says (voiceover)
3. The order things appear and disappear

Scene types you can use:

TITLE_CARD - A heading or chapter title displayed prominently
QUOTE - A quotation displayed as styled text with attribution
SIDE_BY_SIDE - Two concepts shown next to each other for comparison
BULLET_LIST - Items appearing one by one as narrator reads them
FLOW_DIAGRAM - Boxes and arrows showing how things connect or flow
SHOW_RESOURCE - Display an existing IMAGE or TABLE. Use the exact file path from the section. The narrator explains what the image/table/code shows while it is displayed.
NARRATION_ONLY - Text displayed on screen while narrator reads it

Rules:
1. VOICEOVER must contain the COMPLETE ORIGINAL TEXT — word for word. Do NOT summarize or paraphrase.
2. For BULLET_LIST: VOICEOVER must include the intro paragraph AND every LIST_ITEM's full text.
3. One scene = one visual idea. When the screen should change, start a new scene.
4. Describe what the viewer SEES, not what the text means.
5. If a paragraph is a quotation, make it a QUOTE scene.
6. If the text says "as opposed to" or compares things, make it SIDE_BY_SIDE.
7. If there are LIST_ITEMs, make it a BULLET_LIST.
8. If the text describes parts of a system connecting, make it a FLOW_DIAGRAM.
9. ONLY use SHOW_RESOURCE when the section has a line starting with IMAGE or TABLE. Use the EXACT file path from that line. NEVER invent, guess, or make up file paths. If the section has no IMAGE or TABLE lines, do NOT use SHOW_RESOURCE at all.
10. A CAPTION always belongs with the IMAGE or TABLE it describes.
11. In the SCREEN field, NEVER reference file paths unless they appear in the section. Describe visuals using shapes, text, and labels only.

Output format (plain text only):

---
SCENE <number>
TYPE: <scene type>
VOICEOVER: <the COMPLETE ORIGINAL text for this scene — every word>
SCREEN: <describe exactly what appears on screen, in order>
---

Example:

---
SCENE 1
TYPE: QUOTE
VOICEOVER: The Internet was done so well that most people think of it as a natural resource like the Pacific Ocean, rather than something that was man-made. When was the last time a technology with a scale like that was so error-free? —Alan Kay, in interview with Dr Dobb's Journal (2012)
SCREEN: Quote text fades in centered on screen. Below the quote, "— Alan Kay, Dr Dobb's Journal (2012)" appears after a pause.
---
SCENE 2
TYPE: SIDE_BY_SIDE
VOICEOVER: Many applications today are data-intensive, as opposed to compute-intensive. Raw CPU power is rarely a limiting factor for these applications—bigger problems are usually the amount of data, the complexity of data, and the speed at which it is changing.
SCREEN: Left side shows a box labeled "Data-Intensive" highlighted in blue. Right side shows a box labeled "Compute-Intensive" grayed out. An arrow points from right to left with text "shift".
---
SCENE 3
TYPE: BULLET_LIST
VOICEOVER: A data-intensive application is typically built from standard building blocks that provide commonly needed functionality. For example, many applications need to: Store data so that they, or another application, can find it again later (databases). Remember the result of an expensive operation, to speed up reads (caches). Allow users to search data by keyword or filter it in various ways (search indexes). Send a message to another process, to be handled asynchronously (stream processing). Periodically crunch a large amount of accumulated data (batch processing).
SCREEN: Title "Building Blocks" at top. Bullets appear one by one: "Databases — store data", "Caches — speed up reads", "Search indexes — keyword search", "Stream processing — async messages", "Batch processing — crunch accumulated data".
---

Now create the storyboard for this section:

{section_content}
"""


def _build_prompt(section_content: str) -> str:
    return PROMPT.format(section_content=section_content)


def storyboard_with_ollama(section_content: str) -> str:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [
                {"role": "user", "content": _build_prompt(section_content)},
            ],
            "stream": False,
            "options": {"num_ctx": 8192},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def storyboard_with_gemini(section_content: str) -> str:
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_build_prompt(section_content),
    )
    return response.text


BACKENDS = {
    "ollama": storyboard_with_ollama,
    "gemini": storyboard_with_gemini,
}


def storyboard_section_file(section_file: Path, backend: str = "ollama") -> str:
    content = section_file.read_text(encoding="utf-8", errors="replace")
    fn = BACKENDS.get(backend)
    if fn is None:
        raise ValueError(f"Unknown backend: {backend}. Choose from: {list(BACKENDS.keys())}")
    return fn(content)


if __name__ == "__main__":
    # Usage: python -m src.scene_grouping.llm_storyboard pipeline/sections/section_2.txt --backend ollama
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Storyboard generator")
    parser.add_argument("section_file", type=str, help="Path to a section file")
    parser.add_argument("--backend", type=str, default="ollama", choices=list(BACKENDS.keys()))
    args = parser.parse_args()

    path = Path(args.section_file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    result = storyboard_section_file(path, backend=args.backend)

    output_dir = Path("pipeline/groups/storyboard")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / path.name
    output_file.write_text(result, encoding="utf-8")
    print(f"Wrote: {output_file}")
    print(result)
