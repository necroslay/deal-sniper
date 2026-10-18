"""Machine-learning layer for the deal-sniper pipeline.

Opt-in and gated behind ``CFG.ml.enabled``. The modules here build a labeled
dataset from the listings database, train text classifiers (a resolver and a
listing guard), and expose them through one enrichment seam
(``app.engines.enrich``) so the scraper never has to know a model exists.
"""
