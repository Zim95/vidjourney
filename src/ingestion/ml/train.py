import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, accuracy_score

from src.ingestion.ml.utils import build_code_training_data, extract_hand_crafted_features, N_HAND_CRAFTED
from src.ingestion.ml.embed import get_embeddings
from src.config.constants import ML_MODELS_DIR, ML_MODEL_FILENAME, ML_N_ESTIMATORS, ML_TEST_SIZE, ML_RANDOM_STATE

MODELS_DIR = ML_MODELS_DIR
MODEL_PATH = MODELS_DIR / ML_MODEL_FILENAME


def train_code_rf() -> None:
    '''
    Train a Random Forest classifier to classify code vs text blocks.
    Uses hand-crafted features + Ollama nomic-embed-text embeddings.
    '''
    X, y = build_code_training_data()
    print(f"Dataset: {len(y)} samples, {sum(y)} code, {len(y) - sum(y)} text")

    # cross-validation before final fit
    clf = RandomForestClassifier(
        n_estimators=ML_N_ESTIMATORS,
        class_weight="balanced",
        n_jobs=-1,
        random_state=ML_RANDOM_STATE,
    )
    cv_scores = cross_val_score(clf, X, y, cv=5, scoring="f1")
    print(f"Cross-validation F1: {cv_scores.mean():.3f} (+/- {cv_scores.std():.3f})")

    # train/test split for detailed report
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=ML_TEST_SIZE, random_state=ML_RANDOM_STATE, stratify=y,
    )

    clf.fit(X_train, y_train)

    preds = clf.predict(X_test)
    print(f"Test Accuracy: {accuracy_score(y_test, preds):.3f}")
    print(classification_report(y_test, preds, target_names=["text", "code"]))

    # feature importances (top 15)
    embedding_dim = X.shape[1] - N_HAND_CRAFTED
    feature_names = [
        "symbol_density", "indent_ratio", "short_line_ratio", "num_lines",
        "mean_words_per_line", "connector_count", "starts_with_ref",
        "has_braces", "has_semicolons", "ends_with_terminator", "sentence_end_ratio",
    ] + [f"emb_{i}" for i in range(embedding_dim)]

    importances = clf.feature_importances_
    top_indices = np.argsort(importances)[-15:][::-1]
    print("Top 15 features:")
    for idx in top_indices:
        print(f"  {feature_names[idx]}: {importances[idx]:.4f}")

    # retrain on full dataset for production model
    clf.fit(X, y)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")


# --- Inference ---

_cached_model = None


def _load_model():
    global _cached_model
    if _cached_model is None:
        _cached_model = joblib.load(MODEL_PATH)
    return _cached_model


def predict_is_code(text: str) -> bool:
    """Predict whether a text snippet is code using the trained RF model."""
    clf = _load_model()
    hand_crafted = np.array([extract_hand_crafted_features(text)])
    embeddings = get_embeddings([text])
    if embeddings[0] is None:
        return True  # if embedding fails, don't demote (conservative)
    emb_array = np.array([embeddings[0]])
    features = np.hstack([hand_crafted, emb_array])
    return bool(clf.predict(features)[0] == 1)


def predict_is_code_proba(text: str) -> float:
    """Return the probability that a text snippet is code (0.0 to 1.0)."""
    clf = _load_model()
    hand_crafted = np.array([extract_hand_crafted_features(text)])
    embeddings = get_embeddings([text])
    if embeddings[0] is None:
        return 1.0  # if embedding fails, assume code (conservative)
    emb_array = np.array([embeddings[0]])
    features = np.hstack([hand_crafted, emb_array])
    return float(clf.predict_proba(features)[0][1])


if __name__ == "__main__":
    # Usage: python -m src.ingestion.ml.train
    train_code_rf()
