import re
import fitz
from statistics import mean, pstdev


class TableDetectionUtils:
    from src.config.constants import (
        INGESTION_TABLE_Y_TOLERANCE,
        INGESTION_TABLE_X_CLUSTER_TOLERANCE,
        INGESTION_TABLE_ROW_SPACING_VARIANCE,
        INGESTION_TABLE_WIDTH_RATIO,
        INGESTION_TABLE_SCORE_THRESHOLD,
    )
    Y_TOLERANCE = INGESTION_TABLE_Y_TOLERANCE
    X_CLUSTER_TOLERANCE = INGESTION_TABLE_X_CLUSTER_TOLERANCE

    @staticmethod
    def _words_in_bbox(page: fitz.Page, bbox: tuple[float, float, float, float]) -> list[tuple]:
        x0, y0, x1, y1 = bbox
        return [
            word
            for word in page.get_text("words")
            if float(word[0]) >= x0 and float(word[1]) >= y0 and float(word[2]) <= x1 and float(word[3]) <= y1
        ]

    @staticmethod
    def _group_lines(words: list[tuple]) -> list[list[tuple]]:
        if not words:
            return []

        sorted_words = sorted(words, key=lambda word: (float(word[1]), float(word[0])))
        lines: list[list[tuple]] = []

        for word in sorted_words:
            y_center = (float(word[1]) + float(word[3])) / 2.0
            matching_line = next(
                (
                    line
                    for line in lines
                    if abs(y_center - mean(((float(item[1]) + float(item[3])) / 2.0) for item in line)) <= TableDetectionUtils.Y_TOLERANCE
                ),
                None,
            )

            if matching_line is None:
                lines.append([word])
            else:
                matching_line.append(word)

        return [sorted(line, key=lambda item: float(item[0])) for line in lines]

    @staticmethod
    def _cluster_count(values: list[float], tolerance: float) -> int:
        if not values:
            return 0
        clusters: list[list[float]] = []
        for value in sorted(values):
            target_cluster = next(
                (cluster for cluster in clusters if abs(value - mean(cluster)) <= tolerance),
                None,
            )
            (target_cluster.append(value) if target_cluster is not None else clusters.append([value]))
        return len(clusters)

    @staticmethod
    def _safe_ratio(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator > 0 else 0.0

    @staticmethod
    def _metrics(page: fitz.Page, bbox: tuple[float, float, float, float]) -> dict[str, float]:
        words = TableDetectionUtils._words_in_bbox(page, bbox)
        lines = TableDetectionUtils._group_lines(words)
        line_count = len(lines)

        page_width = float(page.rect.width)
        bbox_width = max(1.0, float(bbox[2]) - float(bbox[0]))
        bbox_height = max(1.0, float(bbox[3]) - float(bbox[1]))

        line_widths = [
            max(0.0, max(float(word[2]) for word in line) - min(float(word[0]) for word in line))
            for line in lines if line
        ]
        avg_line_width = mean(line_widths) if line_widths else 0.0
        width_ratio = TableDetectionUtils._safe_ratio(avg_line_width, max(1.0, page_width))

        all_word_count = sum(len(line) for line in lines)
        word_count_by_line = [len(line) for line in lines if line]
        word_count_variance = pstdev(word_count_by_line) if len(word_count_by_line) >= 2 else 0.0

        x_starts = [float(line[0][0]) for line in lines if line]
        x_ends = [float(line[-1][2]) for line in lines if line]
        same_left_alignment = TableDetectionUtils._cluster_count(x_starts, TableDetectionUtils.X_CLUSTER_TOLERANCE) <= 1
        right_edge_clusters = TableDetectionUtils._cluster_count(x_ends, TableDetectionUtils.X_CLUSTER_TOLERANCE)

        internal_x_positions = [
            float(word[0])
            for line in lines
            for index, word in enumerate(line)
            if index > 0
        ]
        aligned_columns = TableDetectionUtils._cluster_count(internal_x_positions, TableDetectionUtils.X_CLUSTER_TOLERANCE)

        line_y_centers = [
            mean(((float(word[1]) + float(word[3])) / 2.0) for word in line)
            for line in lines if line
        ]
        row_gaps = [
            line_y_centers[index + 1] - line_y_centers[index]
            for index in range(len(line_y_centers) - 1)
        ]
        row_spacing_variance = pstdev(row_gaps) if len(row_gaps) >= 2 else 0.0

        text_blob = " ".join(str(word[4]) for word in words)
        stopword_hits = len(re.findall(r"\b(the|and|or|to|of|in|is|that|for|with|on|as|by|from)\b", text_blob.lower()))
        stopword_ratio = TableDetectionUtils._safe_ratio(stopword_hits, max(1, len(re.findall(r"\b\w+\b", text_blob))))

        sentence_punctuation_hits = len(re.findall(r"[\.;:!?]", text_blob))
        sentence_punctuation_ratio = TableDetectionUtils._safe_ratio(sentence_punctuation_hits, max(1, len(text_blob)))

        number_hits = len(re.findall(r"\d", text_blob))
        alnum_hits = len(re.findall(r"[A-Za-z0-9]", text_blob))
        numeric_ratio = TableDetectionUtils._safe_ratio(number_hits, max(1, alnum_hits))

        drawing_items = page.get_drawings()
        has_grid_lines = any(
            fitz.Rect(item.get("rect", fitz.Rect(0, 0, 0, 0))).intersects(fitz.Rect(*bbox))
            for item in drawing_items
        )

        return {
            "line_count": float(line_count),
            "aligned_columns": float(aligned_columns),
            "width_ratio": width_ratio,
            "stopword_ratio": stopword_ratio,
            "sentence_punctuation_ratio": sentence_punctuation_ratio,
            "word_count_variance": word_count_variance,
            "same_left_alignment": 1.0 if same_left_alignment else 0.0,
            "right_edge_clusters": float(right_edge_clusters),
            "row_spacing_variance": row_spacing_variance,
            "numeric_ratio": numeric_ratio,
            "has_grid_lines": 1.0 if has_grid_lines else 0.0,
            "word_count": float(all_word_count),
            "bbox_density": TableDetectionUtils._safe_ratio(float(all_word_count), bbox_width * bbox_height),
        }

    @staticmethod
    def evaluate(page: fitz.Page, bbox: tuple[float, float, float, float]) -> tuple[bool, float, list[str]]:
        metrics = TableDetectionUtils._metrics(page, bbox)

        reject_rules: dict[str, callable] = {
            "insufficient_columns": lambda m: m["aligned_columns"] <= 1,
            "insufficient_rows": lambda m: m["line_count"] < 3,
            "flowing_paragraph": lambda m: m["width_ratio"] > TableDetectionUtils.INGESTION_TABLE_WIDTH_RATIO and m["aligned_columns"] <= 1,
        }

        reject_reasons = [name for name, rule in reject_rules.items() if rule(metrics)]
        if reject_reasons:
            return False, -1.0, reject_reasons

        positive_rules: dict[str, tuple[float, callable]] = {
            "aligned_columns": (3.0, lambda m: m["aligned_columns"] >= 2),
            "consistent_rows": (2.0, lambda m: m["line_count"] >= 3 and m["row_spacing_variance"] <= TableDetectionUtils.INGESTION_TABLE_ROW_SPACING_VARIANCE),
            "right_edge_alignment": (1.0, lambda m: m["right_edge_clusters"] >= 2),
            "numeric_heavy": (1.0, lambda m: m["numeric_ratio"] >= 0.08),
            "grid_lines": (4.0, lambda m: m["has_grid_lines"] > 0),
        }

        penalty_rules: dict[str, tuple[float, callable]] = {
            "single_left_alignment": (-3.0, lambda m: m["same_left_alignment"] > 0),
            "wide_lines": (-2.0, lambda m: m["width_ratio"] > TableDetectionUtils.INGESTION_TABLE_WIDTH_RATIO),
            "sentence_punctuation_heavy": (-2.0, lambda m: m["sentence_punctuation_ratio"] > 0.03),
            "high_stopword_ratio": (-2.0, lambda m: m["stopword_ratio"] > 0.35),
            "high_word_count_variance": (-1.0, lambda m: m["word_count_variance"] > 6.0),
        }

        score = sum(weight for _name, (weight, rule) in positive_rules.items() if rule(metrics))
        score += sum(weight for _name, (weight, rule) in penalty_rules.items() if rule(metrics))

        return score >= TableDetectionUtils.INGESTION_TABLE_SCORE_THRESHOLD, score, []
