'''
Here we need to detect sections.
The input for this module is:
[
    (page_number, PageElements),
    ...
]
'''

import re
from ast import literal_eval
from dataclasses import dataclass, field, replace

from src.ingestion.page_elements import HeadingElement, PageElement, PageElements


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


class SectionUtils:

    @staticmethod
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
            heading_text = re.sub(r"\s+", " ", heading.text).strip()
            print(f"{section_number}, {page_number}, {heading_text}")

    @staticmethod
    def filter_sections(sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        '''
        Display sections and interactively select ranges of section numbers to keep.
        Input format:
            [(start_number, end_number), (start_number, end_number), ...]
        '''
        if not sections:
            print("No sections found.")
            return []

        SectionUtils.display_sections(sections)
        user_input = input(
            "Enter section ranges to keep as [(start, end), ...] (press Enter to keep all): "
        ).strip()

        if not user_input:
            return sections

        try:
            parsed_ranges = literal_eval(user_input)
        except (ValueError, SyntaxError):
            print("Invalid range format. Keeping all sections.")
            return sections

        if not isinstance(parsed_ranges, list):
            print("Input must be a list of tuples. Keeping all sections.")
            return sections

        selected_indices: set[int] = set()
        total_sections = len(sections)

        for item in parsed_ranges:
            if not isinstance(item, tuple) or len(item) != 2:
                continue

            start_number, end_number = item
            if not isinstance(start_number, int) or not isinstance(end_number, int):
                continue

            start = max(1, min(start_number, end_number))
            end = min(total_sections, max(start_number, end_number))
            for section_number in range(start, end + 1):
                selected_indices.add(section_number - 1)

        filtered_sections = [
            section
            for index, section in enumerate(sections)
            if index in selected_indices
        ]

        if not filtered_sections:
            print("No valid ranges selected. Keeping all sections.")
            return sections

        print(f"Selected {len(filtered_sections)} section(s) out of {total_sections}.")
        return filtered_sections