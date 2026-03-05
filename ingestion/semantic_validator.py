from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from .semantic_layer import SemanticDocument, SemanticElement


logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    chapter_index: int | None = None
    element_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "chapter_index": self.chapter_index,
            "element_index": self.element_index,
        }
        return {key: value for key, value in data.items() if value is not None}


@dataclass
class SemanticValidationReport:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


class SemanticIntegrityValidator:
    _MIN_PARAGRAPH_LENGTH = 20

    def validate(self, semantic_document: SemanticDocument) -> SemanticValidationReport:
        logger.info("Starting semantic integrity validation", extra={"chapter_count": len(semantic_document.chapters)})
        issues: list[ValidationIssue] = []

        for chapter_index, chapter in enumerate(semantic_document.chapters):
            issues.extend(self._check_heading_hierarchy(chapter_index, chapter.elements))
            issues.extend(self._check_paragraph_quality(chapter_index, chapter.elements))
            issues.extend(self._check_code_block_integrity(chapter_index, chapter.elements))
            issues.extend(self._check_image_captions(chapter_index, chapter.elements))
            issues.extend(self._check_header_footer_noise(chapter_index, chapter.elements))
            issues.extend(self._check_ordering(chapter_index, chapter.elements))

        report = SemanticValidationReport(valid=not any(issue.severity == "error" for issue in issues), issues=issues)
        logger.info(
            "Completed semantic integrity validation",
            extra={"valid": report.valid, "issue_count": len(report.issues)},
        )
        return report

    def _check_heading_hierarchy(self, chapter_index: int, elements: list[SemanticElement]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        previous_level: int | None = None
        for element_index, element in enumerate(elements):
            if element.type != "heading" or element.level is None:
                continue
            if previous_level is not None and element.level - previous_level > 1:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="heading_level_jump",
                        message=f"Heading level jump detected: H{previous_level} -> H{element.level}",
                        chapter_index=chapter_index,
                        element_index=element_index,
                    )
                )
            previous_level = element.level
        return issues

    def _check_paragraph_quality(self, chapter_index: int, elements: list[SemanticElement]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for element_index, element in enumerate(elements):
            if element.type != "paragraph":
                continue
            text = (element.text or "").strip()
            if not text:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="empty_paragraph",
                        message="Empty paragraph detected",
                        chapter_index=chapter_index,
                        element_index=element_index,
                    )
                )
                continue
            if len(text) < self._MIN_PARAGRAPH_LENGTH:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="short_paragraph",
                        message=f"Very short paragraph fragment detected (length={len(text)})",
                        chapter_index=chapter_index,
                        element_index=element_index,
                    )
                )
        return issues

    @staticmethod
    def _check_code_block_integrity(chapter_index: int, elements: list[SemanticElement]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        code_tokens = ("{", "}", "def ", "class ", "=>", "==", ";")
        prose_tokens = (" therefore ", " however ", " in this chapter ", " we will ")
        for element_index, element in enumerate(elements):
            if element.type != "code_block":
                continue
            text = (element.text or "")
            has_code_like = any(token in text for token in code_tokens)
            has_prose_like = any(token in text.lower() for token in prose_tokens)
            if has_prose_like and not has_code_like:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="code_block_prose_mix",
                        message="Code block appears to contain prose text",
                        chapter_index=chapter_index,
                        element_index=element_index,
                    )
                )
        return issues

    @staticmethod
    def _check_image_captions(chapter_index: int, elements: list[SemanticElement]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for element_index, element in enumerate(elements):
            if element.type not in {"image", "table"}:
                continue
            if not element.caption:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="missing_caption",
                        message=f"{element.type.capitalize()} has no attached caption",
                        chapter_index=chapter_index,
                        element_index=element_index,
                    )
                )
        return issues

    @staticmethod
    def _check_header_footer_noise(chapter_index: int, elements: list[SemanticElement]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for element_index, element in enumerate(elements):
            text = (element.text or "").strip().lower()
            if not text:
                continue
            if text.startswith("copyright") or text.startswith("all rights reserved"):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="header_footer_noise",
                        message="Possible header/footer noise detected in semantic content",
                        chapter_index=chapter_index,
                        element_index=element_index,
                    )
                )
        return issues

    @staticmethod
    def _check_ordering(chapter_index: int, elements: list[SemanticElement]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        previous_page = 0
        for element_index, element in enumerate(elements):
            if element.page < previous_page:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="reading_order_violation",
                        message="Element page order is not monotonic",
                        chapter_index=chapter_index,
                        element_index=element_index,
                    )
                )
            previous_page = max(previous_page, element.page)
        return issues


def validate_semantic_document(semantic_document: SemanticDocument) -> SemanticValidationReport:
    logger.debug("validate_semantic_document helper called")
    return SemanticIntegrityValidator().validate(semantic_document)
