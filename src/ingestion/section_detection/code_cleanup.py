import re
import json
from dataclasses import replace

from pygments.lexers import guess_lexer
from pygments.util import ClassNotFound

from src.ingestion.page_elements import (
    AnnotationElement,
    CodeBlockElement,
    LinkElement,
    PageElement,
    ParagraphElement,
)


class CodeCleanupUtils:
    from src.config.constants import (
        INGESTION_CODE_SQL_KEYWORD_HITS,
        INGESTION_CODE_SYMBOL_DENSITY_MIN,
        INGESTION_CODE_SYMBOL_DENSITY_STRONG,
        INGESTION_CODE_MIN_LINE_COUNT,
        INGESTION_CODE_DEMOTE_SYMBOL_DENSITY,
        INGESTION_CODE_DEMOTE_SQL_HITS,
        INGESTION_CODE_PROSE_MIN_LINE_LENGTH,
        INGESTION_CODE_PROSE_CONNECTOR_KEYWORDS,
    )
    SQL_KEYWORDS = {
        "select", "from", "where", "group", "by", "order", "having", "join", "left", "right",
        "inner", "outer", "on", "insert", "update", "delete", "create", "table", "with", "recursive",
        "union", "begin", "transaction", "commit", "rollback",
    }

    @staticmethod
    def _normalize(text: str) -> str:
        return "\n".join(line.rstrip() for line in (text or "").splitlines()).strip()

    @staticmethod
    def _word_tokens(text: str) -> list[str]:
        return re.findall(r"[A-Za-z_]+", text.lower())

    @staticmethod
    def _code_symbol_density(text: str) -> float:
        symbol_count = len(re.findall(r"[{}\[\]();=<>+\-*/%]", text))
        return symbol_count / max(1, len(text))

    @staticmethod
    def _sql_keyword_hits(text: str) -> int:
        tokens = CodeCleanupUtils._word_tokens(text)
        return sum(1 for token in tokens if token in CodeCleanupUtils.SQL_KEYWORDS)

    @staticmethod
    def _line_count(text: str) -> int:
        return len([line for line in text.splitlines() if line.strip()])

    @staticmethod
    def _ends_with_statement_terminator(text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        return lines[-1].endswith((";", "}", "]", ")"))

    @staticmethod
    def _looks_like_explanatory_prose(text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False

        sentence_like_lines = sum(
            1 for line in lines if len(line.split()) >= CodeCleanupUtils.INGESTION_CODE_PROSE_MIN_LINE_LENGTH and line.endswith((".", ":"))
        )
        connector_pattern = r"\\b(" + "|".join(CodeCleanupUtils.INGESTION_CODE_PROSE_CONNECTOR_KEYWORDS) + r")\\b"
        prose_connectors = len(re.findall(connector_pattern, text.lower()))

        first_line_explainer = lines[0].endswith(":") and ";" not in lines[0]
        return (sentence_like_lines >= max(1, len(lines) // 2) and prose_connectors >= 1) or first_line_explainer

    @staticmethod
    def _looks_like_citation_block(text: str) -> bool:
        normalized = CodeCleanupUtils._normalize(text)
        if not normalized:
            return False

        first_line = normalized.splitlines()[0].strip().lower()
        bracketed_ref = bool(re.match(r"^\[\d+\]", first_line))

        lowered = normalized.lower()
        has_doi = "doi:" in lowered or bool(re.search(r"\b10\.\d{4,9}/\S+", lowered))
        has_isbn = "isbn" in lowered or bool(re.search(r"\b97[89][-\s\d]{8,20}\b", lowered))
        has_publication_markers = sum(
            1
            for marker in (
                "volume", "number", "pages", "acm", "communications",
                "publisher", "press", "verlag", "isbn", "doi",
                "february", "january", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december",
            )
            if marker in lowered
        )
        has_year = bool(re.search(r"\b(19|20)\d{2}\b", lowered))

        if not bracketed_ref:
            return False

        if has_doi or has_isbn:
            return True

        return has_publication_markers >= 2 and has_year

    @staticmethod
    def _looks_like_running_header_artifact(text: str) -> bool:
        normalized = CodeCleanupUtils._normalize(text)
        if not normalized:
            return False

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return False

        compact = re.sub(r"\s+", " ", normalized)

        if re.search(r"\|\s*\d{1,4}\s*$", compact):
            return True

        if len(lines) == 3 and lines[1] == "|" and re.fullmatch(r"\d{1,4}", lines[2]):
            return True

        if re.match(r"^\d{1,4}\s*\|\s*[A-Za-z]", compact):
            return True

        return False

    @staticmethod
    def _is_confident_code(text: str) -> bool:
        lowered = text.lower()
        if "begin transaction" in lowered and ("commit" in lowered or "rollback" in lowered):
            return True

        sql_hits = CodeCleanupUtils._sql_keyword_hits(text)
        symbol_density = CodeCleanupUtils._code_symbol_density(text)
        line_count = CodeCleanupUtils._line_count(text)

        if sql_hits >= CodeCleanupUtils.INGESTION_CODE_SQL_KEYWORD_HITS and symbol_density >= CodeCleanupUtils.INGESTION_CODE_SYMBOL_DENSITY_MIN:
            return True

        if line_count >= CodeCleanupUtils.INGESTION_CODE_MIN_LINE_COUNT and symbol_density >= CodeCleanupUtils.INGESTION_CODE_SYMBOL_DENSITY_STRONG and CodeCleanupUtils._ends_with_statement_terminator(text):
            return True

        return False

    @staticmethod
    def _should_demote_to_paragraph(text: str) -> bool:
        normalized = CodeCleanupUtils._normalize(text)
        if not normalized:
            return False

        # ordered checks: first match wins (True = demote, False = keep, None = continue)
        demote_checks = [
            ("running_header", lambda t: True if CodeCleanupUtils._looks_like_running_header_artifact(t) else None),
            ("citation_block", lambda t: True if CodeCleanupUtils._looks_like_citation_block(t) else None),
            ("confident_code", lambda t: False if CodeCleanupUtils._is_confident_code(t) else None),
            ("prose_heuristic", lambda t: (
                CodeCleanupUtils._looks_like_explanatory_prose(t)
                and CodeCleanupUtils._code_symbol_density(t) <= CodeCleanupUtils.INGESTION_CODE_DEMOTE_SYMBOL_DENSITY
                and CodeCleanupUtils._sql_keyword_hits(t) <= CodeCleanupUtils.INGESTION_CODE_DEMOTE_SQL_HITS
            )),
        ]

        for _name, check in demote_checks:
            result = check(normalized)
            if result is not None:
                return result

        return False

    @staticmethod
    def demote_prose_like_code_blocks(
        sections: list[list[tuple[int, PageElement]]],
    ) -> list[list[tuple[int, PageElement]]]:
        if not sections:
            return []

        cleaned_sections: list[list[tuple[int, PageElement]]] = []
        for section in sections:
            cleaned_items: list[tuple[int, PageElement]] = []
            for page_number, element in section:
                if isinstance(element, CodeBlockElement) and CodeCleanupUtils._should_demote_to_paragraph(element.text):
                    paragraph = ParagraphElement(
                        page_number=element.page_number,
                        reading_order_index=element.reading_order_index,
                        geometry=element.geometry,
                        text=CodeCleanupUtils._normalize(element.text),
                    )
                    cleaned_items.append((page_number, paragraph))
                    continue

                cleaned_items.append((page_number, element))

            cleaned_sections.append(cleaned_items)

        return cleaned_sections


    @staticmethod
    def _ml_code_proba_batch(texts: list[str]) -> list[float]:
        """Batch ML probability that each line is code — one embed+predict
        pass for every line. Returns 1.0 for all lines if the model is
        unavailable (conservative: don't demote code we couldn't score)."""
        if not texts:
            return []
        try:
            from src.ingestion.ml.train import predict_is_code_proba_batch
            return predict_is_code_proba_batch(texts)
        except Exception:
            return [1.0] * len(texts)

    @staticmethod
    def split_code_blocks_by_ml(
        sections: list[list[tuple[int, PageElement]]],
        threshold: float | None = None,
    ) -> list[list[tuple[int, PageElement]]]:
        """
        For each CodeBlockElement, run ML probability per line.
        Lines with proba >= threshold stay as code.
        Lines with proba < threshold become paragraphs.
        Consecutive runs of the same type are grouped together.

        Every candidate line across every code block is embedded in a single
        batched pass (collect -> embed once -> classify) rather than one HTTP
        request per line. Output is identical to a per-line pass: the same
        lines are scored by the same model against the same threshold.
        """
        if threshold is None:
            from src.config.constants import ML_CODE_LINE_THRESHOLD
            threshold = ML_CODE_LINE_THRESHOLD

        if not sections:
            return []

        # Pass 1 — collect every non-empty code line, in reading order, into
        # one flat list so they can be embedded together in Pass 2.
        all_lines: list[str] = []
        for section in sections:
            for _page_number, element in section:
                if isinstance(element, CodeBlockElement):
                    for line in element.text.splitlines():
                        stripped = line.strip()
                        if stripped:
                            all_lines.append(stripped)

        # Pass 2 — a single batched embed + predict for all lines at once.
        probas = CodeCleanupUtils._ml_code_proba_batch(all_lines)
        # Consumed positionally in Pass 3; Pass 1 and Pass 3 iterate identically
        # (same sections/elements/lines, same non-empty filter) so alignment holds.
        proba_iter = iter(probas)

        # Pass 3 — replay the per-line classification, reading each line's
        # precomputed probability in collection order.
        result_sections: list[list[tuple[int, PageElement]]] = []

        for section in sections:
            result_items: list[tuple[int, PageElement]] = []

            for page_number, element in section:
                if not isinstance(element, CodeBlockElement):
                    result_items.append((page_number, element))
                    continue

                lines = element.text.splitlines()
                if not lines:
                    result_items.append((page_number, element))
                    continue

                # classify each line
                classified: list[tuple[str, str]] = []  # (type, line_text)
                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        # empty lines inherit the type of the previous line
                        prev_type = classified[-1][0] if classified else "code"
                        classified.append((prev_type, line))
                    else:
                        proba = next(proba_iter)
                        line_type = "code" if proba >= threshold else "text"
                        classified.append((line_type, line))

                # group consecutive lines of the same type
                groups: list[tuple[str, list[str]]] = []
                for line_type, line_text in classified:
                    if groups and groups[-1][0] == line_type:
                        groups[-1][1].append(line_text)
                    else:
                        groups.append((line_type, [line_text]))

                for group_type, group_lines in groups:
                    joined = "\n".join(group_lines).strip()
                    if not joined:
                        continue

                    if group_type == "code":
                        result_items.append((page_number, replace(element, text=joined)))
                    else:
                        result_items.append((page_number, ParagraphElement(
                            page_number=element.page_number,
                            reading_order_index=element.reading_order_index,
                            geometry=element.geometry,
                            text=joined,
                        )))

            result_sections.append(result_items)

        return result_sections


class CodeMergeUtils:

    @staticmethod
    def _is_tiny_separator_paragraph(element: PageElement) -> bool:
        if not isinstance(element, ParagraphElement):
            return False

        lines = [line.strip() for line in element.text.splitlines() if line.strip()]
        if len(lines) > 1:
            return False
        if not lines:
            return True

        text = lines[0]
        return len(text) <= 40

    @staticmethod
    def _symbol_density(text: str) -> float:
        symbol_count = len(re.findall(r"[{}\[\]();=<>+\-*/%:,.]", text))
        return symbol_count / max(1, len(text))

    @staticmethod
    def _indent_ratio(text: str) -> float:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return 0.0
        indented = sum(1 for line in lines if line.startswith((" ", "\t")))
        return indented / len(lines)

    @staticmethod
    def _ends_with_open_structure(text: str) -> bool:
        stripped = text.rstrip()
        if not stripped:
            return False
        return stripped.endswith(("{", "(", "[", ":", "\\", ",", "."))

    @staticmethod
    def _starts_with_continuation(text: str) -> bool:
        stripped = text.lstrip()
        if not stripped:
            return False

        lowered = stripped.lower()
        continuation_prefixes = (
            "else",
            "elif",
            "except",
            "catch",
            "finally",
            ".",
            ")",
            "]",
            "}",
            "&&",
            "||",
            "+",
            "-",
            "*",
            "/",
            "%",
            "=>",
            "->",
            "::",
        )
        return lowered.startswith(continuation_prefixes)

    @staticmethod
    def _is_hard_stop_element(element: PageElement) -> bool:
        from src.ingestion.page_elements import (
            CaptionElement,
            DrawingElement,
            HeadingElement,
            ImageElement,
            TableElement,
        )
        return isinstance(
            element,
            (
                HeadingElement,
                CaptionElement,
                ImageElement,
                TableElement,
                DrawingElement,
            ),
        )

    @staticmethod
    def _is_ignorable_separator_element(element: PageElement) -> bool:
        if isinstance(element, (LinkElement, AnnotationElement)):
            return True
        if isinstance(element, ParagraphElement):
            return CodeMergeUtils._is_page_artifact_paragraph(element.text)
        return False

    @staticmethod
    def _is_page_artifact_paragraph(text: str) -> bool:
        compact = re.sub(r"\s+", " ", (text or "")).strip()
        if not compact:
            return True

        if len(compact) > 90:
            return False

        if re.search(r"\|\s*\d{1,4}\s*$", compact):
            return True

        if re.match(r"^\d{1,4}\s*\|\s*chapter\b", compact, flags=re.IGNORECASE):
            return True

        if re.match(r"^\d{1,4}\s*\|\s*[A-Za-z][\w\s\-:,]+$", compact):
            return True

        if re.match(r"^chapter\s+\d+\b", compact, flags=re.IGNORECASE):
            return True

        return False

    @staticmethod
    def _is_cross_page_continuation(previous: CodeBlockElement, current: CodeBlockElement) -> bool:
        prev_text = previous.text or ""
        curr_text = current.text or ""

        if not prev_text.strip() or not curr_text.strip():
            return False

        if CodeMergeUtils._ends_with_open_structure(prev_text) or CodeMergeUtils._starts_with_continuation(curr_text):
            return True

        prev_indent = CodeMergeUtils._indent_ratio(prev_text)
        curr_indent = CodeMergeUtils._indent_ratio(curr_text)
        prev_symbols = CodeMergeUtils._symbol_density(prev_text)
        curr_symbols = CodeMergeUtils._symbol_density(curr_text)

        return abs(prev_indent - curr_indent) <= 0.25 and abs(prev_symbols - curr_symbols) <= 0.06

    @staticmethod
    def combine_split_code_blocks(
        sections: list[list[tuple[int, PageElement]]],
    ) -> list[list[tuple[int, PageElement]]]:
        if not sections:
            return []

        merged_sections: list[list[tuple[int, PageElement]]] = []

        for section in sections:
            merged_items: list[tuple[int, PageElement]] = []
            index = 0
            total_items = len(section)

            while index < total_items:
                page_number, element = section[index]

                if not isinstance(element, CodeBlockElement):
                    merged_items.append((page_number, element))
                    index += 1
                    continue

                base_page = page_number
                base_element = element
                combined_text_parts = [element.text.rstrip("\n")]
                cursor = index + 1
                merged_any = False

                while cursor < total_items:
                    next_page, next_element = section[cursor]

                    if CodeMergeUtils._is_hard_stop_element(next_element):
                        break

                    if CodeMergeUtils._is_ignorable_separator_element(next_element):
                        cursor += 1
                        continue

                    if isinstance(next_element, CodeBlockElement):
                        same_page = next_page == base_page
                        should_merge = same_page or CodeMergeUtils._is_cross_page_continuation(base_element, next_element)
                        if not should_merge:
                            break

                        combined_text_parts.append(next_element.text.rstrip("\n"))
                        base_element = next_element
                        merged_any = True
                        cursor += 1
                        continue

                    if CodeMergeUtils._is_tiny_separator_paragraph(next_element):
                        if cursor + 1 >= total_items:
                            break

                        lookahead_page, lookahead_element = section[cursor + 1]
                        if not isinstance(lookahead_element, CodeBlockElement):
                            break

                        same_page = lookahead_page == base_page
                        should_merge = same_page or CodeMergeUtils._is_cross_page_continuation(base_element, lookahead_element)
                        if not should_merge:
                            break

                        combined_text_parts.append(lookahead_element.text.rstrip("\n"))
                        base_element = lookahead_element
                        merged_any = True
                        cursor += 2
                        continue

                    break

                if merged_any:
                    merged_block = replace(element, text="\n".join(part for part in combined_text_parts if part))
                    merged_items.append((page_number, merged_block))
                    index = cursor
                else:
                    merged_items.append((page_number, element))
                    index += 1

            merged_sections.append(merged_items)

        return merged_sections


class CodeBlockFormatUtils:
    EXTENSION_BY_ALIAS = {
        "python": "py",
        "py": "py",
        "json": "json",
        "javascript": "js",
        "js": "js",
        "typescript": "ts",
        "ts": "ts",
        "java": "java",
        "kotlin": "kt",
        "scala": "scala",
        "go": "go",
        "golang": "go",
        "rust": "rs",
        "c": "c",
        "cpp": "cpp",
        "c++": "cpp",
        "csharp": "cs",
        "c#": "cs",
        "php": "php",
        "ruby": "rb",
        "rb": "rb",
        "sql": "sql",
        "postgresql": "sql",
        "mysql": "sql",
        "sqlite": "sql",
        "html": "html",
        "xml": "xml",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "ini": "ini",
        "bash": "sh",
        "shell": "sh",
        "sh": "sh",
    }

    @staticmethod
    def _guess_lexer(text: str):
        try:
            return guess_lexer(text)
        except ClassNotFound:
            return None
        except Exception:
            return None

    @staticmethod
    def _extension_from_lexer(lexer) -> str:
        if lexer is None:
            return "txt"

        aliases = [str(alias).lower() for alias in getattr(lexer, "aliases", []) if alias]
        for alias in aliases:
            if alias in CodeBlockFormatUtils.EXTENSION_BY_ALIAS:
                return CodeBlockFormatUtils.EXTENSION_BY_ALIAS[alias]

        lexer_name = str(getattr(lexer, "name", "")).lower()
        if lexer_name in CodeBlockFormatUtils.EXTENSION_BY_ALIAS:
            return CodeBlockFormatUtils.EXTENSION_BY_ALIAS[lexer_name]

        return "txt"

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = [line.rstrip() for line in (text or "").splitlines()]
        normalized = "\n".join(lines).strip("\n")
        return normalized

    @staticmethod
    def _format_json(text: str) -> str | None:
        normalized = CodeBlockFormatUtils._normalize_text(text)
        try:
            parsed = json.loads(normalized)
        except Exception:
            return None
        return json.dumps(parsed, indent=2, ensure_ascii=False)

    @staticmethod
    def _format_sql(text: str) -> str:
        normalized = CodeBlockFormatUtils._normalize_text(text)
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return ""

        formatted_lines: list[str] = []
        indent_level = 0

        for raw_line in lines:
            upper = raw_line.upper()

            if upper.startswith(("COMMIT", "ROLLBACK", "END")):
                indent_level = max(0, indent_level - 1)

            formatted_lines.append(f"{'    ' * indent_level}{raw_line}")

            if upper.startswith(("BEGIN", "START TRANSACTION", "CASE")):
                indent_level += 1

        return "\n".join(formatted_lines)

    @staticmethod
    def _format_brace_based(text: str) -> str:
        normalized = CodeBlockFormatUtils._normalize_text(text)
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return ""

        formatted_lines: list[str] = []
        indent_level = 0

        for line in lines:
            stripped = line.strip()

            if stripped.startswith(("}", "]", ")")):
                indent_level = max(0, indent_level - 1)

            formatted_lines.append(f"{'    ' * indent_level}{stripped}")

            if stripped.endswith(("{", "[", "(")):
                indent_level += 1

        return "\n".join(formatted_lines)

    @staticmethod
    def format_for_storage(text: str) -> tuple[str, str]:
        lexer = CodeBlockFormatUtils._guess_lexer(text)
        extension = CodeBlockFormatUtils._extension_from_lexer(lexer)

        formatters = {
            "json": lambda t: CodeBlockFormatUtils._format_json(t) or CodeBlockFormatUtils._format_brace_based(t),
            "sql": lambda t: CodeBlockFormatUtils._format_sql(t),
        }
        formatter = formatters.get(extension, CodeBlockFormatUtils._format_brace_based)
        return formatter(text), extension
