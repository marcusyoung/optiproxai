"""Compare embedding models for the distilled feature classifier.

Uses cached embedding matrices (data/cache/embeddings_*.npy), which are keyed on
the same truncated text list used by training, so no API calls are needed when
the matrices are already cached (as they are for bge-m3 and voyage-4).

Protocol: stratified k-fold cross-validation of the same
MultiOutputClassifier(LogisticRegression) pipeline used in production training.
Held-out accuracy and macro-F1 are reported per dimension and averaged.
The fold splits are identical across models (same seed, same anchor dimension),
so differences are attributable to the embedding space, not the split.

Usage:
    uv run --directory <repo> python scripts/compare_embeddings.py
    uv run --directory <repo> python scripts/compare_embeddings.py --models bge-m3,voyage-4,voyage-4-lite
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedKFold
from sklearn.multioutput import MultiOutputClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, StandardScaler

from optiproxai.config import load_config
from optiproxai.feature_training import (
    EMBEDDING_TEXT_LIMIT,
    VALID_DIMENSION_LABELS,
    build_embedding_client,
    load_or_compute_embeddings,
)
from optiproxai.scorer import SEMANTIC_DIMENSIONS

SMALL_CLASS_THRESHOLD = 100

HEADS = ("linear", "mlp", "histgb")
SEED = 42


def load_dataset(data_path: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Load prompts and labels, mirroring optiproxai.feature_training.load_feature_examples."""
    with open(data_path, encoding="utf-8") as f:
        dataset = json.load(f)

    prompts = [str(item["prompt"]).strip()[:EMBEDDING_TEXT_LIMIT] for item in dataset]
    if not prompts:
        raise ValueError("dataset is empty")

    labels_by_dimension: dict[str, list[str]] = {}
    for dim in SEMANTIC_DIMENSIONS:
        labels = [str(item.get(dim, "")).strip().lower() for item in dataset]
        invalid = sorted(set(labels) - VALID_DIMENSION_LABELS)
        if invalid:
            raise ValueError(f"invalid labels for {dim}: {invalid}")
        labels_by_dimension[dim] = labels
    return prompts, labels_by_dimension


def cache_path_for(cache_dir: Path, model: str, texts: list[str]) -> Path:
    content_hash = hashlib.sha256(
        json.dumps({"model": model, "texts": texts}, sort_keys=True).encode()
    ).hexdigest()[:12]
    return cache_dir / f"embeddings_{content_hash}.npy"


def load_embeddings(
    model: str,
    texts: list[str],
    cache_dir: Path,
    *,
    fetch_missing: bool,
) -> np.ndarray[Any, np.dtype[np.float32]]:
    path = cache_path_for(cache_dir, model, texts)
    if path.exists():
        print(f"  {model}: loading cached {path.name}")
        return cast(np.ndarray[Any, np.dtype[np.float32]], np.load(path))

    if not fetch_missing:
        raise FileNotFoundError(
            f"{model} embeddings not cached ({path.name} missing); "
            "pass --fetch-missing to embed via the configured provider, "
            "or train once with that model to populate the cache"
        )

    try:
        # Override only the model name; provider/base_url/api_key come from config.
        cfg = load_config(overrides={"embedding": {"model": model}})
    except Exception:
        cfg = None
    if cfg is None or cfg.embedding is None or cfg.embedding.model != model:
        raise RuntimeError(
            f"config embedding.model={getattr(getattr(cfg, 'embedding', None), 'model', '(none)')} "
            f"does not match {model}; refusing to fetch mismatched model"
        )
    print(f"  {model}: computing via configured provider ...")
    client, _resolved_model = build_embedding_client()
    return load_or_compute_embeddings(client, texts, cache_dir, model=model)


def encode_targets(
    labels_by_dimension: dict[str, list[str]],
) -> tuple[dict[str, LabelEncoder], np.ndarray[Any, np.dtype[np.int_]]]:
    encoders: dict[str, LabelEncoder] = {}
    columns: list[np.ndarray[Any, np.dtype[np.int_]]] = []
    for dim in SEMANTIC_DIMENSIONS:
        encoder = LabelEncoder()
        columns.append(
            cast(
                np.ndarray[Any, np.dtype[np.int_]],
                encoder.fit_transform(labels_by_dimension[dim]),
            )
        )
        encoders[dim] = encoder
    return encoders, np.column_stack(columns)


def build_estimator(head: str, seed: int) -> MultiOutputClassifier:
    """Return a multi-output classifier for the given head name.

    Mirrors the production pipeline for 'linear' (same hyperparameters as
    optiproxai.feature_training.train_feature_classifier). MLP gets a scaler because
    raw embedding distances are scale-sensitive; HistGB is scale-invariant
    (tree-based) and does not need one.
    """
    if head == "linear":
        base = LogisticRegression(
            max_iter=1200,
            C=1.0,
            solver="lbfgs",
            class_weight="balanced",
        )
        return MultiOutputClassifier(base)

    if head == "mlp":
        base = MLPClassifier(
            hidden_layer_sizes=(128, 32),
            activation="relu",
            solver="adam",
            alpha=1e-3,
            max_iter=120,
            early_stopping=True,
            n_iter_no_change=10,
            random_state=seed,
        )
        return MultiOutputClassifier(
            Pipeline([("scale", StandardScaler()), ("mlp", base)])
        )

    if head == "histgb":
        base = HistGradientBoostingClassifier(
            max_iter=150,
            learning_rate=0.08,
            max_leaf_nodes=31,
            min_samples_leaf=20,
            class_weight="balanced",
            early_stopping=cast(
                Any, True
            ),  # sklearn accepts bool|"auto"; stubs type it as str
            n_iter_no_change=15,
            validation_fraction=0.1,
            random_state=seed,
        )
        return MultiOutputClassifier(base)

    raise ValueError(f"unknown head: {head}")


def cross_validate(
    X: np.ndarray[Any, np.dtype[np.float32]],
    y: np.ndarray[Any, np.dtype[np.int_]],
    anchor: np.ndarray[Any, np.dtype[np.int_]],
    n_splits: int,
    seed: int,
    head: str,
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    acc: dict[str, list[float]] = {dim: [] for dim in SEMANTIC_DIMENSIONS}
    macro_f1: dict[str, list[float]] = {dim: [] for dim in SEMANTIC_DIMENSIONS}

    for train_idx, test_idx in skf.split(X, anchor):
        clf = build_estimator(head, seed)
        clf.fit(X[train_idx], y[train_idx])
        pred = clf.predict(X[test_idx])
        for i, dim in enumerate(SEMANTIC_DIMENSIONS):
            acc[dim].append(accuracy_score(y[test_idx][:, i], pred[:, i]))
            macro_f1[dim].append(
                f1_score(
                    y[test_idx][:, i],
                    pred[:, i],
                    average="macro",
                    zero_division=cast(
                        Any, 0
                    ),  # same convention as feature_training.py
                )
            )
    return acc, macro_f1


def min_class_support(labels_by_dimension: dict[str, list[str]]) -> dict[str, int]:
    from collections import Counter

    return {
        dim: min(Counter(labels).values())
        for dim, labels in labels_by_dimension.items()
    }


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare embedding models via stratified CV on cached embeddings"
    )
    parser.add_argument(
        "--data",
        default="data/distilled_feature_dataset.json",
        help="Training data JSON",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/cache",
        help="Embedding cache directory",
    )
    parser.add_argument(
        "--models",
        default="bge-m3,voyage-4",
        help="Comma-separated embedding models to compare",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--anchor",
        default="agenticTask",
        help="Dimension used to stratify folds (must be in SEMANTIC_DIMENSIONS)",
    )
    parser.add_argument(
        "--heads",
        default="linear",
        help=f"Comma-separated classifier heads to compare: {', '.join(HEADS)}",
    )
    parser.add_argument(
        "--fetch-missing",
        action="store_true",
        help="Embed uncached models via the configured provider (may incur API cost)",
    )
    args = parser.parse_args(argv)

    if args.anchor not in SEMANTIC_DIMENSIONS:
        parser.error(f"--anchor must be one of {', '.join(SEMANTIC_DIMENSIONS)}")

    heads = [h.strip() for h in args.heads.split(",") if h.strip()]
    unknown = sorted(set(heads) - set(HEADS))
    if unknown:
        parser.error(
            f"unknown head(s): {', '.join(unknown)}; choose from {', '.join(HEADS)}"
        )

    prompts, labels_by_dimension = load_dataset(Path(args.data))
    n = len(prompts)
    print(
        f"Loaded {n} prompts; folds={args.folds} seed={args.seed} "
        f"anchor={args.anchor} heads={','.join(heads)}"
    )

    encoders, y = encode_targets(labels_by_dimension)
    anchor = encoders[args.anchor].transform(labels_by_dimension[args.anchor])

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        parser.error("--models is empty")

    results: dict[str, tuple[dict[str, float], dict[str, float]]] = {}
    for model in models:
        try:
            X = load_embeddings(
                model,
                prompts,
                Path(args.cache_dir),
                fetch_missing=args.fetch_missing,
            )
        except Exception as exc:
            print(f"  {model}: SKIPPED ({exc})")
            continue
        if X.shape[0] != n:
            print(
                f"  {model}: SKIPPED (row count {X.shape[0]} != dataset {n}); "
                "cache keyed on a different text list"
            )
            continue
        for head in heads:
            key = f"{model} [{head}]"
            acc, macro_f1 = cross_validate(X, y, anchor, args.folds, args.seed, head)
            results[key] = (
                {dim: float(np.mean(v)) for dim, v in acc.items()},
                {dim: float(np.mean(v)) for dim, v in macro_f1.items()},
            )
            print(f"  {key}: evaluated")

    if not results:
        print("No models evaluated; nothing to compare.")
        return 1

    print("\n--- Per-dimension held-out results (mean over folds) ---")
    header = "dimension                 " + "".join(
        f"  {m[:16]:>16} acc / f1" for m in results
    )
    print(header)
    print("-" * len(header))

    support = min_class_support(labels_by_dimension)
    acc_sums = {m: 0.0 for m in results}
    f1_sums = {m: 0.0 for m in results}

    for dim in SEMANTIC_DIMENSIONS:
        cells = []
        for model in results:
            acc, macro_f1 = results[model]
            cells.append(f"  {_fmt(acc[dim]):>7} / {_fmt(macro_f1[dim]):>7}")
            acc_sums[model] += acc[dim]
            f1_sums[model] += macro_f1[dim]
        marker = ""
        if support[dim] < SMALL_CLASS_THRESHOLD:
            marker = f"  [min-class={support[dim]}]"
        print(f"{dim:<24}" + "".join(cells) + marker)

    print("-" * len(header))
    cells = []
    for model in results:
        n_dims = len(SEMANTIC_DIMENSIONS)
        cells.append(
            f"  {_fmt(acc_sums[model] / n_dims):>7} / {_fmt(f1_sums[model] / n_dims):>7}"
        )
    print(f"{'MEAN':<24}" + "".join(cells))

    print("\n--- Per-dimension winner (accuracy) ---")
    dims_list = list(results.keys())
    a, b = dims_list[0], dims_list[1]
    wins = {m: 0 for m in results}
    for dim in SEMANTIC_DIMENSIONS:
        best = max(results, key=lambda m: results[m][0][dim])
        wins[best] += 1
        winner = "=" if results[a][0][dim] == results[b][0][dim] else best
        print(f"  {dim:<24} {winner}")
    print(f"  {'TOTAL':<24} " + "  ".join(f"{m}: {wins[m]}" for m in results))

    if len(results) == 2:
        a, b = dims_list[0], dims_list[1]
        n_dims = len(SEMANTIC_DIMENSIONS)
        acc_a = acc_sums[a] / n_dims
        acc_b = acc_sums[b] / n_dims
        f1_a = f1_sums[a] / n_dims
        f1_b = f1_sums[b] / n_dims
        print("\n--- Verdict ---")
        print(
            f"  {a}: acc={_fmt(acc_a)} macroF1={_fmt(f1_a)} | "
            f"{b}: acc={_fmt(acc_b)} macroF1={_fmt(f1_b)}"
        )
        print(
            f"  {b} vs {a}: accuracy {acc_b - acc_a:+.3f}, macro-F1 {f1_b - f1_a:+.3f}"
        )

    if len(results) > 2:
        n_dims = len(SEMANTIC_DIMENSIONS)
        print("\n--- Ranking by mean accuracy ---")
        for key in sorted(results, key=lambda k: -(acc_sums[k] / n_dims)):
            print(
                f"  {key:<40} acc={_fmt(acc_sums[key] / n_dims)} "
                f"macroF1={_fmt(f1_sums[key] / n_dims)}"
            )

    print("\nNote: dims flagged [min-class=N] have a class with <100 samples;")
    print("their per-dimension scores are noisy — weight the MEAN row more.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
