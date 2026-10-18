"""Train the resolver and guard models and persist them for enrichment.

Usage::

    python -m app.ml.train --task resolver
    python -m app.ml.train --task guard --labels data/labels/guard.csv

Writes ``models/<task>_model.pkl`` (gitignored; rebuilt on demand). Training and
evaluation share the split and class-collapse helpers, so a freshly trained
model's numbers cannot silently diverge from what the harness reports.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from app.config import CFG
from app.ml import dataset as ds


def train_resolver(examples: Sequence[ds.LabeledExample], seed: Optional[int] = None):
    """Fit the TF-IDF resolver on the training split (rare labels collapsed)."""
    from app.ml.tfidf_model import TfidfPredictor

    seed = CFG.ml.seed if seed is None else seed
    splits = ds.split_dataset(examples, seed)
    mapping = ds.rare_label_map(splits["train"], CFG.ml.min_class_support,
                                CFG.ml.other_label)
    train = ds.apply_label_map(splits["train"], mapping)
    model = TfidfPredictor.build(seed=seed)
    return model.fit([e.text for e in train], [e.label for e in train])


def train_guard(examples: Sequence[ds.LabeledExample], seed: Optional[int] = None):
    """Fit the rule-informed guard on the training split."""
    from app.ml.guard_model import GuardModel

    seed = CFG.ml.seed if seed is None else seed
    splits = ds.split_dataset(examples, seed)
    train = splits["train"]
    model = GuardModel.build(seed=seed)
    return model.fit([e.text for e in train], [e.label for e in train])


TRAINERS = {"resolver": train_resolver, "guard": train_guard}
LABEL_COLUMN = {"resolver": "product_label", "guard": "guard_label"}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train a resolver/guard model on a labeled CSV.")
    parser.add_argument("--task", choices=["resolver", "guard"], default="resolver")
    parser.add_argument("--labels", default=None,
                        help="Labeled CSV (default: data/labels/deals.csv).")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    labels_path = (Path(args.labels) if args.labels
                   else CFG.ml.labels_dir / "deals.csv")
    if not labels_path.exists():
        print(f"No labeled data at {labels_path}.")
        return 2

    examples = ds.load_labeled_csv(labels_path, LABEL_COLUMN[args.task])
    if not examples:
        print(f"{labels_path} has no rows with a "
              f"{LABEL_COLUMN[args.task]} value.")
        return 2

    model = TRAINERS[args.task](examples, args.seed)
    path = model.save(CFG.ml.models_dir / f"{args.task}_model.pkl")

    train_size = len(ds.split_dataset(examples, args.seed)["train"])
    print(f"trained {args.task} model on {train_size} training example(s) "
          f"({len(examples)} labeled total)")
    print(f"saved {path}")
    print(f"evaluate:  python -m app.eval.harness --task {args.task} "
          f"--labels {labels_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
