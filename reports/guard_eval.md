# Listing guard — baseline vs model

## Comparison

| Predictor | n | Accuracy | Macro F1 |
|---|---|---|---|
| `guard-baseline` | 35 | 0.829 | 0.790 |
| `guard-model` | 35 | 0.829 | 0.816 |
| `hybrid-guard` | 35 | 0.857 | 0.849 |

---
### `guard-baseline`

- Accuracy: **0.829**
- Macro F1: **0.790**

| Label | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| `keep` | 0.786 | 1.000 | 0.880 | 22 |
| `reject` | 1.000 | 0.538 | 0.700 | 13 |

---
### `guard-model`

- Accuracy: **0.829**
- Macro F1: **0.816**

| Label | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| `keep` | 0.864 | 0.864 | 0.864 | 22 |
| `reject` | 0.769 | 0.769 | 0.769 | 13 |

- Precision@recall>=0.95: **0.684** at threshold `0.3602` (recall 1.000, target reached: True)

---
### `hybrid-guard`

- Accuracy: **0.857**
- Macro F1: **0.849**

| Label | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| `keep` | 0.905 | 0.864 | 0.884 | 22 |
| `reject` | 0.786 | 0.846 | 0.815 | 13 |

- Precision@recall>=0.95: **0.684** at threshold `0.3602` (recall 1.000, target reached: True)
