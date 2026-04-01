import os
import re
import json
import numpy as np
from typing import List, Tuple

from src.ingestion.ml.embed import get_embeddings
from src.config.constants import ML_TRAINING_DATA_DIR


CONNECTOR_KEYWORDS = (
    "which", "because", "therefore", "works", "calculate",
    "efficiently", "load", "find", "however", "although",
    "moreover", "furthermore", "consequently", "thus",
)


def extract_hand_crafted_features(text: str) -> List[float]:
    """
    Extract lightweight numeric features from a text snippet.
    These mirror the heuristics used in CodeDetection (page_elements.py)
    plus additional signals for prose vs code discrimination.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    num_lines = len(lines)

    # symbol density
    symbols = r"[{}\[\]();=<>+\-*/%:,.]"
    symbol_count = len(re.findall(symbols, text))
    symbol_density = symbol_count / max(len(text), 1)

    # indentation ratio
    indented = sum(1 for line in lines if line.startswith(("  ", "\t")))
    indent_ratio = indented / max(num_lines, 1)

    # short line ratio (lines <= 60 chars)
    short = sum(1 for line in lines if len(line.strip()) <= 60)
    short_line_ratio = short / max(num_lines, 1)

    # mean word count per line
    word_counts = [len(line.split()) for line in lines] if lines else [0]
    mean_words_per_line = sum(word_counts) / max(len(word_counts), 1)

    # connector keyword count (prose signal)
    lowered = text.lower()
    connector_count = sum(1 for kw in CONNECTOR_KEYWORDS if kw in lowered)

    # starts with bracket reference (citation signal)
    starts_with_ref = 1.0 if re.match(r"^\s*\[\d+\]", text) else 0.0

    # has semicolons or braces (code signal)
    has_braces = 1.0 if re.search(r"[{}]", text) else 0.0
    has_semicolons = 1.0 if ";" in text else 0.0

    # ends with statement terminator
    stripped = text.rstrip()
    ends_with_terminator = 1.0 if stripped and stripped[-1] in (";", "}", ")", "]") else 0.0

    # sentence-ending punctuation ratio (prose signal)
    sentence_ends = sum(1 for line in lines if line.rstrip().endswith((".", "?", "!")))
    sentence_end_ratio = sentence_ends / max(num_lines, 1)

    return [
        symbol_density,
        indent_ratio,
        short_line_ratio,
        float(num_lines),
        mean_words_per_line,
        float(connector_count),
        starts_with_ref,
        has_braces,
        has_semicolons,
        ends_with_terminator,
        sentence_end_ratio,
    ]

N_HAND_CRAFTED = 11


def label_code_training_data() -> None:
    '''
    This is a utility function to manually label code blocks detected by heuristics.
    We do this to train our model overtime.
    The accuracy of the model will keep improving as we label more data and retrain the model.
    '''
    CURRENT_DIRECTORY = os.getcwd()
    CODE_DIRECTORY = os.path.join(CURRENT_DIRECTORY, 'pipeline/sections/resources/code_blocks/')
    OUTPUT_DIRECTORY = str(ML_TRAINING_DATA_DIR)

    # already processed files
    processed_files = set(os.listdir(OUTPUT_DIRECTORY))

    # get the code files detected by heuristics
    code_files: list = os.listdir(CODE_DIRECTORY)

    # read each code file and label the lines as code or not code
    i = 0
    while i < len(code_files):
        if f"{code_files[i]}_labeled.json" in processed_files:
            i += 1
            continue
        file_path = os.path.join(CODE_DIRECTORY, code_files[i])
        print("Filename: ", code_files[i])
        code_block_labels: list[dict] = []
        if os.path.isfile(file_path):
            with open(file_path, 'r') as f:
                lines = f.readlines()
                for line in lines:
                    ip = input(f"{line} - Is this code(c) or text(t)?")
                    label = 'code' if ip.lower() == 'c' else 'text'
                    code_block_labels.append({'text': line, 'label': label})
        redo = input("Redo this file? (y/n)")
        if redo.lower() == 'y':
            continue
        output_file_path = os.path.join(OUTPUT_DIRECTORY, f"{code_files[i]}_labeled.json")
        with open(output_file_path, 'w') as f:
            f.write(json.dumps(code_block_labels, indent=4))
        i += 1


def build_code_training_data(batch_size: int = 64) -> Tuple[np.ndarray, np.ndarray]:
    """
    Load all labeled training examples from `src/ml/training_code_snippets/`,
    extract hand-crafted features + Ollama embeddings, and return feature matrix X and labels y.

    Returns:
        X: numpy array of shape (n_samples, n_hand_crafted + embedding_dim)
        y: numpy array of shape (n_samples,)
    """
    LABELED_CODE_DIRECTORY = str(ML_TRAINING_DATA_DIR)

    all_texts: List[str] = []
    all_labels: List[int] = []

    dataset_files = [
        f for f in os.listdir(LABELED_CODE_DIRECTORY)
        if os.path.isfile(os.path.join(LABELED_CODE_DIRECTORY, f))
    ]
    for dataset_file in dataset_files:
        file_path = os.path.join(LABELED_CODE_DIRECTORY, dataset_file)
        with open(file_path, 'r') as f:
            dataset = json.load(f)

        for item in dataset:
            text = item["text"].strip()
            if not text:
                continue
            all_texts.append(text)
            all_labels.append(1 if item["label"] == "code" else 0)

    # hand-crafted features
    hand_crafted = np.array([extract_hand_crafted_features(t) for t in all_texts])

    # Ollama embeddings in batches
    X_parts = [hand_crafted]
    y_filtered = list(all_labels)

    embedding_vectors: List[List[float]] = []
    failed_indices: set = set()

    for i in range(0, len(all_texts), batch_size):
        batch_texts = all_texts[i:i + batch_size]
        embeddings = get_embeddings(batch_texts)
        for j, emb in enumerate(embeddings):
            if emb is None:
                failed_indices.add(i + j)
                embedding_vectors.append([])
            else:
                embedding_vectors.append(emb)

    # filter out failed embeddings
    if failed_indices:
        print(f"Warning: {len(failed_indices)} embeddings failed, excluding from training")
        valid_mask = [i not in failed_indices for i in range(len(all_texts))]
        hand_crafted = hand_crafted[valid_mask]
        embedding_array = np.array([v for i, v in enumerate(embedding_vectors) if i not in failed_indices])
        y_filtered = [l for i, l in enumerate(all_labels) if i not in failed_indices]
    else:
        embedding_array = np.array(embedding_vectors)

    X = np.hstack([hand_crafted, embedding_array])
    y = np.array(y_filtered)

    print(f"Features: {N_HAND_CRAFTED} hand-crafted + {embedding_array.shape[1]} embedding = {X.shape[1]} total")

    return X, y
