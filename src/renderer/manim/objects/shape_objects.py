from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from manim import (
    BLUE_C, BOLD, Circle, GRAY_B, ITALIC, Mobject, ORANGE,
    Rectangle, RoundedRectangle, Square, Text, VGroup, WHITE,
)

from .object_base import ObjectBase


@dataclass
class ShapeObject(ObjectBase):
    border_color: object = WHITE
    fill_color: object | None = None
    text: str | None = None
    text_color: object = WHITE

    def set_border(self, color: object = WHITE) -> ShapeObject:
        self.border_color = color
        return self

    def set_fill(self, color: object | None = None) -> ShapeObject:
        self.fill_color = color
        return self

    def set_text(self, text: str | None = None, text_color: object = WHITE) -> ShapeObject:
        self.text = text
        self.text_color = text_color
        return self

    def set_size(self, _size: float) -> ShapeObject:
        raise NotImplementedError

    def _apply_text(self, shape_mobject: Mobject) -> Mobject:
        if self.text is None:
            return shape_mobject

        label = Text(self.text, color=self.text_color, font="Arial")
        label.scale_to_fit_width(max(shape_mobject.width * 0.8, 0.2))
        label.move_to(shape_mobject.get_center())
        return VGroup(shape_mobject, label)

    def draw(self) -> Mobject:
        raise NotImplementedError

    @classmethod
    def build(cls, config: dict[str, Any]) -> ShapeObject:
        instance = cls()
        position = cls._point2d(config.get("position"), default=(0.0, 0.0))
        size = cls._number(config.get("size"), default=1.6)
        instance.set_position(position[0], position[1])
        instance.set_size(size)
        instance.set_border(config.get("border_color", WHITE))
        instance.set_fill(config.get("fill_color"))
        instance.set_text(config.get("text"), config.get("text_color", WHITE))
        return instance


@dataclass
class CircleShape(ShapeObject):
    radius: float = 0.8

    def set_size(self, size: float) -> CircleShape:
        self.radius = size
        return self

    def draw(self) -> Mobject:
        circle = Circle(radius=self.radius, stroke_color=self.border_color)
        if self.fill_color is None:
            circle.set_fill(opacity=0)
        else:
            circle.set_fill(self.fill_color, opacity=1)
        return self._move_to_position(self._apply_text(circle))


@dataclass
class SquareShape(ShapeObject):
    length: float = 1.6

    def set_size(self, size: float) -> SquareShape:
        self.length = size
        return self

    def draw(self) -> Mobject:
        square = Square(side_length=self.length, stroke_color=self.border_color)
        if self.fill_color is None:
            square.set_fill(opacity=0)
        else:
            square.set_fill(self.fill_color, opacity=1)
        return self._move_to_position(self._apply_text(square))


@dataclass
class RectangleShape(ShapeObject):
    breadth: float = 1.0

    def set_size(self, size: float) -> RectangleShape:
        self.breadth = size
        return self

    def draw(self) -> Mobject:
        rectangle = Rectangle(width=self.breadth, height=self.breadth * 2, stroke_color=self.border_color)
        if self.fill_color is None:
            rectangle.set_fill(opacity=0)
        else:
            rectangle.set_fill(self.fill_color, opacity=1)
        return self._move_to_position(self._apply_text(rectangle))


@dataclass
class HeadingShape(ShapeObject):
    """Bold white text, no background. Size = target width in manim units."""
    target_width: float = 10.0  # ~70% of the 14.22 frame width

    def set_size(self, size: float) -> HeadingShape:
        self.target_width = size
        return self

    def draw(self) -> Mobject:
        text = Text(self.text or "", color=self.text_color, weight=BOLD, font="Arial")
        if text.width > 0:
            text.scale_to_fit_width(self.target_width)
        return self._move_to_position(text)


@dataclass
class QuoteShape(ShapeObject):
    """Italic white quote with optional attribution below.

    Renders:
        "<quote text>"
                    — <author>, <other info>

    `text` carries either just the quote, or "<quote>|||<attribution>". When
    attribution is present, the quote is shown on top centered, and the
    attribution sits below right-aligned with a leading em-dash.
    """
    target_width: float = 10.0
    attribution_height: float = 0.45

    def set_size(self, size: float) -> QuoteShape:
        self.target_width = size
        return self

    def _wrap_quote(self, text: str, max_chars: int = 56) -> str:
        """Soft-wrap quote text so very long quotes break into multiple lines."""
        words = text.split()
        lines: list[str] = []
        line: list[str] = []
        line_len = 0
        for w in words:
            if line and line_len + len(w) + 1 > max_chars:
                lines.append(" ".join(line))
                line = [w]
                line_len = len(w)
            else:
                line.append(w)
                line_len += len(w) + (1 if len(line) > 1 else 0)
        if line:
            lines.append(" ".join(line))
        return "\n".join(lines)

    def draw(self) -> Mobject:
        raw = self.text or ""
        if "|||" in raw:
            quote_str, attribution = raw.split("|||", 1)
        else:
            quote_str, attribution = raw, ""
        quote_str = quote_str.strip().strip('"')
        attribution = attribution.strip()

        if not quote_str:
            return self._move_to_position(Text("", color=self.text_color))

        # The quote — italic, wrapped, centered
        wrapped = self._wrap_quote(quote_str)
        quoted_display = f'"{wrapped}"'
        quote_text = Text(quoted_display, color=self.text_color, slant=ITALIC, font="Arial")
        if quote_text.width > self.target_width:
            quote_text.scale_to_fit_width(self.target_width)

        if not attribution:
            return self._move_to_position(quote_text)

        # Attribution — em-dash + author info, smaller, sits below the quote
        attribution_display = f"— {attribution}"
        attr_text = Text(attribution_display, color=self.text_color, slant=ITALIC, font="Arial")
        attr_text.scale_to_fit_height(self.attribution_height)
        if attr_text.width > self.target_width * 0.7:
            attr_text.scale_to_fit_width(self.target_width * 0.7)

        # Stack: quote on top, attribution below, slight gap between
        target_x, target_y = self.position
        gap = 0.3
        quote_text.move_to([target_x, target_y + (attr_text.height + gap) / 2, 0])
        # Right-align the attribution under the quote's right edge
        attr_x = target_x + quote_text.width / 2
        attr_text.move_to([attr_x - attr_text.width / 2, target_y - (quote_text.height + gap) / 2, 0])

        return VGroup(quote_text, attr_text)


@dataclass
class ListItemShape(ShapeObject):
    """Bullet item — left-anchored bold white text with a leading bullet.

    Position represents the LEFT edge of the text, not the center, so items
    in a vertical list line up cleanly regardless of length.
    """
    target_width: float = 11.0  # max width (clamped)
    text_height: float = 0.5

    def set_size(self, size: float) -> ListItemShape:
        self.target_width = size
        return self

    def draw(self) -> Mobject:
        body = self.text or ""
        full = f"•  {body}" if body else "•"
        text = Text(full, color=self.text_color, font="Arial", weight=BOLD)
        text.scale_to_fit_height(self.text_height)
        # Clamp horizontal width if too long
        if text.width > self.target_width:
            text.scale_to_fit_width(self.target_width)
        # Position represents the LEFT edge — shift so left edge sits at position.x
        target_x, target_y = self.position
        text.move_to([target_x + text.width / 2, target_y, 0])
        return text


@dataclass
class ConceptCardShape(ShapeObject):
    """Full-frame concept card — bold title at top + multi-line body below.

    `text` carries "title|||body". Title sits near the top, body wraps to fit
    within `target_width` and stacks below the title. Used as the default
    visual for prose paragraphs that don't listify and aren't quotes.

    The card is centered around `position` (typically (0,0)) so the title is
    above center and the body is below it.
    """
    target_width: float = 11.0
    title_height: float = 0.9
    body_text_height: float = 0.45
    title_y_offset: float = 1.6   # vertical offset from card center to title baseline
    body_y_offset: float = -0.2   # vertical offset from card center to body top

    def set_size(self, size: float) -> ConceptCardShape:
        self.target_width = size
        return self

    def _wrap_body(self, body: str, max_chars: int = 60) -> str:
        """Soft-wrap body text into ~max_chars lines for display."""
        words = body.split()
        lines: list[str] = []
        line: list[str] = []
        line_len = 0
        for w in words:
            if line and line_len + len(w) + 1 > max_chars:
                lines.append(" ".join(line))
                line = [w]
                line_len = len(w)
            else:
                line.append(w)
                line_len += len(w) + (1 if len(line) > 1 else 0)
        if line:
            lines.append(" ".join(line))
        return "\n".join(lines)

    def draw(self) -> Mobject:
        raw = self.text or ""
        if "|||" in raw:
            title_str, body_str = raw.split("|||", 1)
        else:
            title_str, body_str = raw, ""
        title_str = title_str.strip()
        body_str = body_str.strip()

        title = Text(title_str, color=self.text_color, font="Arial", weight=BOLD)
        if title.width > 0:
            title.scale_to_fit_height(self.title_height)
            if title.width > self.target_width:
                title.scale_to_fit_width(self.target_width)

        body = Text(self._wrap_body(body_str), color=self.text_color, font="Arial")
        if body.width > 0:
            body.scale_to_fit_height(min(self.body_text_height * 3, body.height))  # cap height
            if body.width > self.target_width:
                body.scale_to_fit_width(self.target_width)

        # Position title and body relative to the card's anchor (self.position)
        target_x, target_y = self.position
        title.move_to([target_x, target_y + self.title_y_offset, 0])
        body.move_to([target_x, target_y + self.body_y_offset - body.height / 2, 0])

        return VGroup(title, body)


@dataclass
class ListTitleShape(ShapeObject):
    """Display-only header sitting above a bullet stack.

    Smaller than HeadingShape (which fills the whole frame) — just enough to
    caption "what these bullets are about". Bold, centered, no pill.
    """
    target_width: float = 10.0
    text_height: float = 0.55

    def set_size(self, size: float) -> ListTitleShape:
        self.target_width = size
        return self

    def draw(self) -> Mobject:
        body = self.text or ""
        text = Text(body, color=self.text_color, font="Arial", weight=BOLD)
        if text.width > 0:
            text.scale_to_fit_height(self.text_height)
            if text.width > self.target_width:
                text.scale_to_fit_width(self.target_width)
        return self._move_to_position(text)


# Default border colors per kind — kept here so all entity shapes share the convention
ENTITY_CONCRETE_BORDER = BLUE_C
ENTITY_ABSTRACT_BORDER = GRAY_B
ENTITY_ACTION_BORDER = ORANGE


def _build_pill(text: Text, min_width: float, padding_x: float,
                padding_y: float, border_color, fill_color, corner_radius: float) -> Mobject:
    """Shared helper: text inside a rounded-corner rectangle, returned as a VGroup."""
    width = max(text.width + padding_x * 2, min_width)
    height = text.height + padding_y * 2
    box = RoundedRectangle(
        width=width,
        height=height,
        corner_radius=corner_radius,
        stroke_color=border_color,
        stroke_width=4,
    )
    if fill_color is None:
        box.set_fill(opacity=0)
    else:
        box.set_fill(fill_color, opacity=1)
    text.move_to(box.get_center())
    return VGroup(box, text)


@dataclass
class AutoRectangleShape(ShapeObject):
    """Concrete entity fallback — bold text inside a rounded blue-bordered pill.

    Used when no Iconify icon is available for a concrete entity.
    """
    min_width: float = 2.5
    text_height: float = 0.7
    padding_x: float = 0.3
    padding_y: float = 0.12
    corner_radius: float = 0.2

    def set_size(self, size: float) -> AutoRectangleShape:
        self.min_width = max(size, self.min_width)
        return self

    def draw(self) -> Mobject:
        body = self.text or ""
        label = Text(body, color=self.text_color, font="Arial", weight=BOLD)
        if label.width > 0:
            label.scale_to_fit_height(self.text_height)
        border = self.border_color if self.border_color is not WHITE else ENTITY_CONCRETE_BORDER
        result = _build_pill(label, self.min_width, self.padding_x, self.padding_y,
                             border, self.fill_color, self.corner_radius)
        return self._move_to_position(result)


@dataclass
class EntityAbstractShape(ShapeObject):
    """Abstract concept (reliability, scalability) — bold italic text inside a rounded gray pill."""
    min_width: float = 2.5
    text_height: float = 0.7
    padding_x: float = 0.3
    padding_y: float = 0.12
    corner_radius: float = 0.2

    def set_size(self, size: float) -> EntityAbstractShape:
        self.min_width = max(size, self.min_width)
        return self

    def draw(self) -> Mobject:
        body = self.text or ""
        label = Text(body, color=self.text_color, font="Arial", weight=BOLD, slant=ITALIC)
        if label.width > 0:
            label.scale_to_fit_height(self.text_height)
        border = self.border_color if self.border_color is not WHITE else ENTITY_ABSTRACT_BORDER
        result = _build_pill(label, self.min_width, self.padding_x, self.padding_y,
                             border, self.fill_color, self.corner_radius)
        return self._move_to_position(result)


@dataclass
class EntityActionShape(ShapeObject):
    """Action / process (query, replicate) — bold text + ▸ marker inside a rounded orange pill."""
    min_width: float = 2.5
    text_height: float = 0.7
    padding_x: float = 0.3
    padding_y: float = 0.12
    corner_radius: float = 0.2

    def set_size(self, size: float) -> EntityActionShape:
        self.min_width = max(size, self.min_width)
        return self

    def draw(self) -> Mobject:
        body = self.text or ""
        full = f"▸ {body}" if body else "▸"
        label = Text(full, color=self.text_color, font="Arial", weight=BOLD)
        if label.width > 0:
            label.scale_to_fit_height(self.text_height)
        border = self.border_color if self.border_color is not WHITE else ENTITY_ACTION_BORDER
        result = _build_pill(label, self.min_width, self.padding_x, self.padding_y,
                             border, self.fill_color, self.corner_radius)
        return self._move_to_position(result)
