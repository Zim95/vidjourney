'''
Here we need to detect sections.
The input for this module is:
[
    (page_number, PageElements),
    ...
]
'''

import re
from dataclasses import replace

from src.ingestion.page_elements import HeadingElement, PageElement, PageElements


def _single_line_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _all_elements(elements: PageElements) -> list[PageElement]:
    return [
        *elements.headings,
        *elements.paragraphs,
        *elements.list_items,
        *elements.captions,
        *elements.code_blocks,
        *elements.images,
        *elements.tables,
        *elements.drawings,
        *elements.links,
        *elements.annotations,
        *elements.headers_footers,
        *elements.page_numbers,
    ]


def _format_headings(page_elements: list[tuple[int, PageElements]]) -> None:
    for _page_number, elements in page_elements:
        elements.headings = [
            replace(heading, text=_single_line_text(heading.text))
            for heading in elements.headings
        ]


def get_ordered_elements(page_elements: list[tuple[int, PageElements]]) -> list[tuple[int, PageElement]]:
    '''
    Get all elements from all pages and order them by their reading order.
    This is useful for section detection because sometimes a section might start on one page and continue on the next page,
    and we want to group them together.
    '''
    ordered_items: list[tuple[int, PageElement]] = []
    for page_number, elements in page_elements:
        for element in _all_elements(elements):
            ordered_items.append((page_number, element))

    ordered_items.sort(key=lambda page_element: (int(page_element[1].reading_order_index), int(page_element[0])))

    return ordered_items


def get_heading_indices(ordered_items: list[tuple[int, PageElement]]) -> list[int]:
    '''
    Get the indices of the heading elements in the ordered items list.
    This is useful for section detection because we can use the headings as the boundaries of the sections.
    '''
    return [
        index for index, (_page_number, element) in enumerate(ordered_items)
        if isinstance(element, HeadingElement)
    ]

def group_sections(ordered_items: list[tuple[int, PageElement]], heading_indices: list[int]) -> list[list[tuple[int, PageElement]]]:
    '''
    Group the ordered items into sections based on the heading indices.
    Each section starts with a heading and includes all items until the next heading.
    '''
    if not heading_indices:
        return []

    grouped_sections: list[list[tuple[int, PageElement]]] = []

    for position, heading_index in enumerate(heading_indices):
        next_heading_index = heading_indices[position + 1] if position + 1 < len(heading_indices) else len(ordered_items)
        section_items = ordered_items[heading_index:next_heading_index]
        grouped_sections.append(section_items)

    return grouped_sections


def display_sections(sections: list[list[tuple[int, PageElement]]]) -> None:
    '''
    Display sections as: section_number, page_number, heading
    '''
    for section_number, section_items in enumerate(sections, start=1):
        if not section_items:
            continue

        heading_entry = next(
            ((page_number, element) for page_number, element in section_items if isinstance(element, HeadingElement)),
            None,
        )

        if heading_entry is None:
            continue

        page_number, heading = heading_entry
        print(f"{section_number}, {page_number}, {_single_line_text(heading.text)}")


def detect_sections(page_elements: list[tuple[int, PageElements]]) -> list[list[tuple[int, PageElement]]]:
    '''
    Detect sections based on headings.
    One heading to the next heading is the section.
    '''
    if not page_elements:
        return []

    _format_headings(page_elements)

    # first we order all the elements by their reading order regardless of page number.
    # This is because sometimes a section might start on one page and continue on the next page,
    #   and we want to group them together.
    ordered_items: list[tuple[int, PageElement]] = get_ordered_elements(page_elements)

    # get the indices of the heading elements in the ordered items list.
    heading_indices: list[int] = get_heading_indices(ordered_items)
    
    # group the ordered items into sections based on the heading indices.
    sections: list[list[tuple[int, PageElement]]] = group_sections(ordered_items, heading_indices)

    display_sections(sections)
    return sections