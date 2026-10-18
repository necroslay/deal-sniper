# Price model — temporal evaluation

Predicting the **asking-price** distribution of comparable live listings (not market value).

## Split

- **Train** on the 434 oldest listings (up to `2026-10-03`).
- **Test** on the 144 newest listings (from `2026-10-03`).
- Split by `date_posted`, so a listing can never appear on both sides.

## Comparison

| Predictor | n | MAE (P50) | MAE after ceiling | Pinball P25 / P50 / P75 | P25–P75 coverage | Capped |
|---|---|---|---|---|---|---|
| `product-quantile-baseline` | 144 | 324 | 324 | 102 / 162 / 159 | 0.49 | 0 |
| `lightgbm-quantile` | 144 | 432 | 432 | 140 / 216 / 210 | 0.44 | 0 |

Lower MAE / pinball is better. Coverage is the share of test asking prices inside the P25–P75 band; a well-calibrated band should sit near **0.50**. `Capped` counts test rows with a new-retail reference whose band was clamped at that price.
