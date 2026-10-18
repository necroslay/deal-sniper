# Product resolver — baseline vs model

## Comparison

| Predictor | n | Accuracy | Macro F1 |
|---|---|---|---|
| `resolver-baseline` | 30 | 0.733 | 0.844 |
| `tfidf-linear` | 30 | 0.867 | 0.871 |
| `hybrid-resolver` | 30 | 0.733 | 0.844 |

---
### `resolver-baseline`

- Accuracy: **0.733**
- Macro F1: **0.844**

| Label | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| `__none__` | 1.000 | 0.579 | 0.733 | 19 |
| `OTHER` | 0.500 | 1.000 | 0.667 | 6 |
| `RX 7900 XTX` | 1.000 | 1.000 | 1.000 | 2 |
| `MacBook Pro M1` | 1.000 | 1.000 | 1.000 | 1 |
| `RTX 4090` | 0.500 | 1.000 | 0.667 | 1 |
| `RX 580` | 1.000 | 1.000 | 1.000 | 1 |
| `RTX 3060` | 0.000 | 0.000 | 0.000 | 0 |

---
### `tfidf-linear`

- Accuracy: **0.867**
- Macro F1: **0.871**

| Label | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| `__none__` | 1.000 | 0.842 | 0.914 | 19 |
| `OTHER` | 1.000 | 0.833 | 0.909 | 6 |
| `RX 7900 XTX` | 1.000 | 1.000 | 1.000 | 2 |
| `MacBook Pro M1` | 0.250 | 1.000 | 0.400 | 1 |
| `RTX 4090` | 1.000 | 1.000 | 1.000 | 1 |
| `RX 580` | 1.000 | 1.000 | 1.000 | 1 |
| `RTX 3080` | 0.000 | 0.000 | 0.000 | 0 |

---
### `hybrid-resolver`

- Accuracy: **0.733**
- Macro F1: **0.844**

| Label | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| `__none__` | 1.000 | 0.579 | 0.733 | 19 |
| `OTHER` | 0.500 | 1.000 | 0.667 | 6 |
| `RX 7900 XTX` | 1.000 | 1.000 | 1.000 | 2 |
| `MacBook Pro M1` | 1.000 | 1.000 | 1.000 | 1 |
| `RTX 4090` | 0.500 | 1.000 | 0.667 | 1 |
| `RX 580` | 1.000 | 1.000 | 1.000 | 1 |
| `RTX 3060` | 0.000 | 0.000 | 0.000 | 0 |
