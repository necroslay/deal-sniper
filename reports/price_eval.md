# Price model — temporal evaluation

Predicting the **asking-price** distribution of comparable live listings (not market value).

## Split

- **Train** on the 200 oldest listings (up to `2026-09-06`).
- **Test** on the 67 newest listings (from `2026-09-07`).
- Split by `date_posted`, so a listing can never appear on both sides.

## Comparison

| Predictor | n | MAE (P50) | MAE after ceiling | Pinball P25 / P50 / P75 | P25–P75 coverage | Capped |
|---|---|---|---|---|---|---|
| `product-quantile-baseline` | 67 | 284 | 284 | 92 / 142 / 140 | 0.30 | 0 |
| `lightgbm-quantile` | 67 | 404 | 404 | 117 / 202 / 243 | 0.36 | 0 |

Lower MAE / pinball is better. Coverage is the share of test asking prices inside the P25–P75 band; a well-calibrated band should sit near **0.50**. `Capped` counts test rows with a new-retail reference whose band was clamped at that price.
