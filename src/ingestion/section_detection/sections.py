import re
from dataclasses import dataclass, field, replace

from src.ingestion.page_elements import (
    HeadingElement,
    PageElement,
    PageElements,
)


@dataclass
class Sections:
    page_elements: list[tuple[int, PageElements]] = field(default_factory=list)
    ordered_items: list[tuple[int, PageElement]] = field(default_factory=list)
    heading_indices: list[int] = field(default_factory=list)
    sections: list[list[tuple[int, PageElement]]] = field(default_factory=list)

    def _single_line_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _all_elements(self, elements: PageElements) -> list[PageElement]:
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

    def _format_headings(self) -> None:
        for _page_number, elements in self.page_elements:
            elements.headings = [
                replace(heading, text=self._single_line_text(heading.text))
                for heading in elements.headings
            ]

    def get_ordered_elements(self) -> list[tuple[int, PageElement]]:
        '''
        Get all elements from all pages and order them by their reading order.
        This is useful for section detection because sometimes a section might start on one page and continue on the next page,
        and we want to group them together.
        '''
        ordered_items: list[tuple[int, PageElement]] = []
        for page_number, elements in self.page_elements:
            for element in self._all_elements(elements):
                ordered_items.append((page_number, element))

        ordered_items.sort(key=lambda page_element: (int(page_element[1].reading_order_index), int(page_element[0])))

        self.ordered_items = ordered_items
        return self.ordered_items

    def get_heading_indices(self) -> list[int]:
        '''
        Get the indices of the heading elements in the ordered items list.
        This is useful for section detection because we can use the headings as the boundaries of the sections.
        '''
        self.heading_indices = [
            index for index, (_page_number, element) in enumerate(self.ordered_items)
            if isinstance(element, HeadingElement)
        ]
        return self.heading_indices

    def group_sections(self) -> list[list[tuple[int, PageElement]]]:
        '''
        Group the ordered items into sections based on the heading indices.
        Each section starts with a heading and includes all items until the next heading.
        '''
        if not self.heading_indices:
            self.sections = []
            return []

        grouped_sections: list[list[tuple[int, PageElement]]] = []

        for position, heading_index in enumerate(self.heading_indices):
            next_heading_index = self.heading_indices[position + 1] if position + 1 < len(self.heading_indices) else len(self.ordered_items)
            section_items = self.ordered_items[heading_index:next_heading_index]
            grouped_sections.append(section_items)

        self.sections = grouped_sections
        return self.sections

    def detect_sections(self) -> list[list[tuple[int, PageElement]]]:
        '''
        Detect sections based on headings.
        One heading to the next heading is the section.
        '''
        if not self.page_elements:
            self.ordered_items = []
            self.heading_indices = []
            self.sections = []
            return []

        self._format_headings()

        # first we order all the elements by their reading order regardless of page number.
        # This is because sometimes a section might start on one page and continue on the next page,
        #   and we want to group them together.
        self.get_ordered_elements()

        # get the indices of the heading elements in the ordered items list.
        self.get_heading_indices()

        # group the ordered items into sections based on the heading indices.
        self.group_sections()

        return self.sections
