"""Quantile price models: predict the P25/P50/P75 of comparable asking prices.

Two predictors behind one interface so they can be compared honestly:

* ``ProductQuantileBaseline`` — per-product empirical quantiles. A strong
  baseline: with a handful of comps per product it is often all you need, and any
  model has to beat it to justify a dependency.
* ``QuantilePriceModel`` — LightGBM quantile regression over the engineered
  features, so condition / age / title can shift the band *within* a product.

Framing (deliberate): these predict the distribution of **asking** prices among
comparable live listings — **not** market value. Asking prices are wishes, not
transactions; the archived valuation engine reached that conclusion the hard way.
"Underpriced relative to its peers" is the claim this supports, and the only one.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional, Protocol, Sequence

import numpy as np

from app.ml.features import PriceRow

QUANTILES = (0.25, 0.50, 0.75)


class PricePredictor(Protocol):
    """Anything that fits on price rows and emits a (low, mid, high) band."""

    name: str

    def fit(self, rows: Sequence[PriceRow]) -> "PricePredictor": ...

    def predict_band(self, rows: Sequence[PriceRow]
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...


class ProductQuantileBaseline:
    """Per-product empirical quantiles, with a global fallback for thin products."""

    name = "product-quantile-baseline"

    def __init__(self, quantiles: Sequence[float] = QUANTILES,
                 min_rows: int = 3):
        self.quantiles = tuple(quantiles)
        self.min_rows = min_rows
        self._by_product: dict[str, np.ndarray] = {}
        self._global: Optional[np.ndarray] = None

    def fit(self, rows: Sequence[PriceRow]) -> "ProductQuantileBaseline":
        prices: dict[str, list[float]] = {}
        for row in rows:
            prices.setdefault(row.product, []).append(row.price_cad)
        self._global = (np.quantile([r.price_cad for r in rows], self.quantiles)
                        if rows else None)
        self._by_product = {
            product: np.quantile(values, self.quantiles)
            for product, values in prices.items()
            if len(values) >= self.min_rows
        }
        return self

    def predict_band(self, rows: Sequence[PriceRow]
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        band = np.empty((3, len(rows)), dtype=float)
        for column, row in enumerate(rows):
            values = self._by_product.get(row.product)
            if values is None:
                values = self._global
            if values is None:                       # nothing was fitted
                values = np.repeat(row.price_cad, 3)
            band[:, column] = [values[0], values[len(values) // 2], values[-1]]
        return band[0], band[1], band[2]

    def has_band(self, product: str) -> bool:
        """True if *product* had enough comps for its own band (no global fallback)."""
        return product in self._by_product

    @property
    def products_covered(self) -> int:
        """How many products had enough comps for their own band."""
        return len(self._by_product)


class QuantilePriceModel:
    """LightGBM quantile regression over the engineered features.

    One boosted model per quantile. Hyper-parameters are deliberately small
    (few leaves, low depth) because the training set is hundreds of rows, not
    thousands — a deep forest would memorise it.
    """

    name = "lightgbm-quantile"

    NUMERIC = ("age_days", "title_len", "desc_len", "n_images", "is_new")
    CATEGORICAL = ("product", "source", "category")

    # Small-data defaults. With a few hundred rows (and products carrying only a
    # handful of comps each) anything richer memorises the training set: 15+
    # leaves or a few hundred trees was measurably *worse* than the simple
    # per-product baseline. These are chosen for the data size, not tuned on the
    # evaluation split.
    def __init__(self, quantiles: Sequence[float] = QUANTILES, seed: int = 42,
                 n_estimators: int = 150, num_leaves: int = 7,
                 learning_rate: float = 0.05):
        self.quantiles = tuple(quantiles)
        self.seed = seed
        self.n_estimators = n_estimators
        self.num_leaves = num_leaves
        self.learning_rate = learning_rate
        self._models: list = []
        self._categories: dict[str, list] = {}

    def _frame(self, rows: Sequence[PriceRow], categories=None):
        """Build the feature frame, with categorical dtypes fixed by training.

        At predict time the categories come from training, so a product unseen in
        training becomes NaN (treated as missing) instead of raising.
        """
        import pandas as pd

        data = {}
        for column in self.CATEGORICAL:
            values = pd.Series([getattr(row, column) for row in rows],
                               dtype="object")
            cats = categories.get(column) if categories else None
            if cats is not None:
                # A product unseen in training must become *missing*, not raise.
                # Blanks are mapped to NaN before the categorical is built, which
                # also avoids constructing a Categorical from values outside its
                # categories (deprecated in pandas 4).
                values = values.where(values.isin(cats), other=np.nan)
            data[column] = pd.Categorical(values, categories=cats)
        for column in self.NUMERIC:
            data[column] = np.array([float(getattr(row, column)) for row in rows],
                                    dtype=float)
        return pd.DataFrame(data)

    def fit(self, rows: Sequence[PriceRow]) -> "QuantilePriceModel":
        from lightgbm import LGBMRegressor

        frame = self._frame(rows)
        self._categories = {column: list(frame[column].cat.categories)
                            for column in self.CATEGORICAL}
        target = np.array([row.price_cad for row in rows], dtype=float)

        self._models = []
        for quantile in self.quantiles:
            model = LGBMRegressor(
                objective="quantile", alpha=quantile,
                n_estimators=self.n_estimators, num_leaves=self.num_leaves,
                learning_rate=self.learning_rate, min_child_samples=10,
                subsample=0.9, subsample_freq=1,
                random_state=self.seed, verbose=-1)
            model.fit(frame, target,
                      categorical_feature=list(self.CATEGORICAL))
            self._models.append(model)
        return self

    def predict_band(self, rows: Sequence[PriceRow]
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if not self._models:
            raise RuntimeError("QuantilePriceModel must be fitted before use")
        frame = self._frame(rows, self._categories)
        stacked = np.vstack([model.predict(frame) for model in self._models])
        stacked = np.sort(stacked, axis=0)      # keep low <= mid <= high
        return stacked[0], stacked[len(stacked) // 2], stacked[-1]

    # ── persistence ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump({"models": self._models, "categories": self._categories,
                         "quantiles": self.quantiles, "seed": self.seed}, handle)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "QuantilePriceModel":
        with Path(path).open("rb") as handle:
            payload = pickle.load(handle)
        model = cls(quantiles=payload["quantiles"], seed=payload["seed"])
        model._models = payload["models"]
        model._categories = payload["categories"]
        return model
