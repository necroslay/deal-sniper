"""Labeled-dataset construction for the ML layer.

The listings database is a scratch artifact (``*.db`` is gitignored), so labels
are exported to a *self-contained* CSV that carries the text next to the label —
the training and test code never needs the database.

The important design choice here is how rows are assigned to train/val/test.
Splitting randomly would leak: sellers relist, and the same item is posted
across cities and across both marketplaces, so near-identical titles would land
on both sides of the split and inflate the score. Splits are therefore assigned
by a hash of the *normalised title*, which keeps every copy of a title in one
split.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.config import CFG

# Columns written by the label export. The two label columns start blank: they
# are what a human fills in.
EXPORT_COLUMNS = (
    "id", "source", "title", "description",
    "baseline_product", "baseline_flag", "product_label", "guard_label",
)

# Fractions must sum to 1.0; the remainder goes to ``test``.
DEFAULT_TRAIN_FRACTION = 0.70
DEFAULT_VAL_FRACTION = 0.15

_SPLIT_NAMES = ("train", "val", "test")


@dataclass(frozen=True)
class LabeledExample:
    """One labeled title, plus the split it deterministically belongs to."""

    text: str
    label: str
    group: str
    source: str = ""
    listing_id: str = ""


def normalise_title(title: str) -> str:
    """Lower-case, strip punctuation and collapse whitespace.

    Used only to derive the split group, never as model input, so aggressive
    normalisation is safe: it just needs to make relists collide.
    """
    lowered = (title or "").lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def group_key(title: str) -> str:
    """Stable hash of a normalised title that groups relists together."""
    return hashlib.sha1(normalise_title(title).encode("utf-8")).hexdigest()


def assign_split(group: str, seed: int = None,
                 train_fraction: float = DEFAULT_TRAIN_FRACTION,
                 val_fraction: float = DEFAULT_VAL_FRACTION) -> str:
    """Deterministically bucket a group hash into train/val/test."""
    seed = CFG.ml.seed if seed is None else seed
    digest = hashlib.sha256(f"{seed}:{group}".encode("utf-8")).hexdigest()
    bucket = int(digest, 16) % 1000
    train_cut = int(train_fraction * 1000)
    val_cut = int((train_fraction + val_fraction) * 1000)
    if bucket < train_cut:
        return "train"
    if bucket < val_cut:
        return "val"
    return "test"


def build_label_export(rows: Iterable) -> list[dict]:
    """Turn stored listing rows into label-export records (labels blank)."""
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        out.append({
            "id": d.get("id", ""),
            "source": d.get("source", ""),
            "title": d.get("title", ""),
            "description": d.get("description", ""),
            "baseline_product": d.get("product_key", "") or "",
            "baseline_flag": d.get("flag_reason", "") or "",
            "product_label": "",
            "guard_label": "",
        })
    return out


def export_label_csv(rows: Iterable, path: str | Path) -> int:
    """Write a label-export CSV and return the number of rows written."""
    records = build_label_export(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(EXPORT_COLUMNS))
        writer.writeheader()
        writer.writerows(records)
    return len(records)


def load_labeled_csv(path: str | Path, label_column: str,
                     text_columns: Sequence[str] = ("title",),
                     seed: Optional[int] = None) -> list[LabeledExample]:
    """Load a label CSV into examples, skipping rows with a blank label.

    Args:
        path: The labeled CSV.
        label_column: ``"product_label"`` (resolver) or ``"guard_label"`` (guard).
        text_columns: Columns concatenated to form the model input. Defaults to
            the title alone, which is what the rule baselines also see — so the
            comparison is apples-to-apples.
        seed: Split seed; defaults to ``CFG.ml.seed``.
    """
    examples: list[LabeledExample] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = (row.get(label_column) or "").strip()
            if not label:
                continue
            text = " ".join((row.get(c) or "").strip()
                            for c in text_columns).strip()
            if not text:
                continue
            group = group_key(row.get("title") or text)
            examples.append(LabeledExample(
                text=text,
                label=label,
                group=group,
                source=(row.get("source") or "").strip(),
                listing_id=(row.get("id") or "").strip(),
            ))
    return examples


def split_dataset(examples: Sequence[LabeledExample],
                  seed: Optional[int] = None
                  ) -> dict[str, list[LabeledExample]]:
    """Partition into train/val/test by title-group, keeping every label learnable.

    Two properties are guaranteed:

    * **No leakage** — a title never spans splits, because the *group* (a hash
      of the normalised title) is the unit of assignment. A relisted or
      cross-posted item cannot appear in both train and test.
    * **Every label is trainable** — any label with no group in ``train`` has its
      lowest-sorted group promoted whole into train.

    The second is what a purely hashed split gets wrong at small n: a class with
    one or two groups can land entirely in val/test, where no model can predict
    it and it scores a hard zero — an artifact that swamps macro F1. Groups are
    moved whole, so the no-leak property is preserved.
    """
    seed = CFG.ml.seed if seed is None else seed
    buckets: dict[str, list[LabeledExample]] = {name: [] for name in _SPLIT_NAMES}
    for example in examples:
        buckets[assign_split(example.group, seed)].append(example)

    group_label: dict[str, str] = {}
    for example in examples:
        group_label.setdefault(example.group, example.label)

    train_labels = {e.label for e in buckets["train"]}
    for label in sorted(set(group_label.values())):
        if label in train_labels:
            continue
        # Groups are sha1 hex, so `min` picks the same group every run.
        chosen = min(g for g, lab in group_label.items() if lab == label)
        for name in ("val", "test"):
            buckets[name] = [e for e in buckets[name] if e.group != chosen]
        buckets["train"].extend(e for e in examples if e.group == chosen)
        train_labels.add(label)
    return buckets


def label_distribution(examples: Sequence[LabeledExample]) -> dict[str, int]:
    """Count examples per label, largest first."""
    counts: dict[str, int] = {}
    for example in examples:
        counts[example.label] = counts.get(example.label, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def rare_label_map(examples: Sequence[LabeledExample], min_support: int,
                   other_label: str) -> dict[str, str]:
    """Map labels below *min_support* (counted on *examples*, i.e. train) to OTHER.

    A class the training split cannot learn from is not a fair thing to score a
    model on, so ultra-rare labels are folded into ``other_label``. The mapping
    is derived from the training split only and then applied to every split, so
    no information from val/test leaks into the decision.
    """
    counts = label_distribution(examples)
    return {label: other_label
            for label, n in counts.items()
            if n < min_support and label != other_label}


def apply_label_map(examples: Sequence[LabeledExample],
                    mapping: dict[str, str]) -> list[LabeledExample]:
    """Return *examples* with mapped labels, preserving order and groups."""
    if not mapping:
        return list(examples)
    return [LabeledExample(
        text=e.text,
        label=mapping.get(e.label, e.label),
        group=e.group,
        source=e.source,
        listing_id=e.listing_id,
    ) for e in examples]


def main(argv: Optional[list[str]] = None) -> int:
    """Export a labeling sheet from the listings database.

    ``--queue`` exports only the highest-value rows (see ``active_learning``),
    which is the cheap way to spend the first few hundred labels.
    """
    parser = argparse.ArgumentParser(
        description="Export a labeling sheet from the listings database.")
    parser.add_argument("--export", required=True, help="Output CSV path.")
    parser.add_argument("--db", default=None,
                        help="SQLite database (default: app/listings.db).")
    parser.add_argument("--task", choices=["resolver", "guard"], default="resolver")
    parser.add_argument("--queue", action="store_true",
                        help="Export only the active-learning queue.")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)

    from app.db.store import Store

    store = Store(**({"db": args.db} if args.db else {})).connect()
    try:
        rows = store.all_listings()
    finally:
        store.close()

    if args.queue:
        from app.ml.active_learning import export_queue_csv
        written = export_queue_csv(rows, args.export, args.task, args.limit)
        print(f"wrote {written} prioritized row(s) to {args.export}")
    else:
        written = export_label_csv(rows, args.export)
        print(f"wrote {written} row(s) to {args.export}")
        print("Fill in the product_label / guard_label column, then run "
              "`python -m app.eval.harness`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
