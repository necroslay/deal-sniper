"""Evaluation harness for the ML layer.

The harness is deliberately dependency-light: it imports numpy but never
scikit-learn, so it can score the deterministic baselines on a machine with no
ML stack installed.
"""
