"""Tests for ml/dataset.py — exports, leakage-safe splits, rare-label folding."""

from __future__ import annotations

from app.db.store import Store
from app.ml import dataset as ds


class TestTitleNormalisation:
    def test_normalise_strips_punctuation_and_case(self):
        assert ds.normalise_title("RTX-3080!! 10GB") == "rtx 3080 10gb"

    def test_group_key_ignores_case_and_punctuation(self):
        assert ds.group_key("RTX-3080!!") == ds.group_key("rtx 3080")

    def test_group_key_ignores_extra_whitespace(self):
        assert ds.group_key("  RTX   3080 ") == ds.group_key("rtx 3080")


class TestSplits:
    def test_assign_split_is_deterministic(self):
        assert ds.assign_split("abc", seed=1) == ds.assign_split("abc", seed=1)

    def test_all_three_splits_are_reachable(self):
        assigned = {ds.assign_split(str(i)) for i in range(200)}
        assert assigned == {"train", "val", "test"}

    def test_a_title_never_leaks_across_splits(self):
        """Same normalised title => same split, however it is spelled."""
        group = ds.group_key("RTX 3080 for sale")
        examples = [ds.LabeledExample(text=f"RTX 3080 #{i}", label="RTX 3080",
                                      group=group) for i in range(12)]
        examples.append(ds.LabeledExample(text="other", label="x",
                                          group=ds.group_key("other")))
        splits = ds.split_dataset(examples)
        placements = {name for name, items in splits.items()
                      if any(e.group == group for e in items)}
        assert len(placements) == 1

    def test_every_label_is_learnable_from_train(self, tiny_examples):
        """No label may end up with zero training examples.

        Otherwise the test set asks the model to predict a class it never saw,
        which scores a hard zero and swamps macro F1.
        """
        splits = ds.split_dataset(tiny_examples)
        train_labels = {e.label for e in splits["train"]}
        assert {e.label for e in tiny_examples} <= train_labels

    def test_a_single_group_label_is_promoted_into_train(self):
        rare_group = ds.group_key("rare item")
        examples = [ds.LabeledExample(text="rare item", label="Rare",
                                      group=rare_group)]
        examples += [ds.LabeledExample(text=f"common {i}", label="Common",
                                       group=ds.group_key(f"common {i}"))
                     for i in range(30)]

        splits = ds.split_dataset(examples)
        # The single-group label was promoted whole into train...
        assert rare_group in {e.group for e in splits["train"]}
        # ...and appears in train and nowhere else.
        placements = [name for name in ("train", "val", "test")
                      if any(e.group == rare_group for e in splits[name])]
        assert placements == ["train"]


class TestExportAndLoad:
    def test_build_label_export_maps_store_columns(self):
        rows = [{"id": "1", "source": "kijiji", "title": "RTX 3080",
                 "description": "d", "product_key": "RTX 3080",
                 "flag_reason": None}]
        out = ds.build_label_export(rows)
        assert out[0]["baseline_product"] == "RTX 3080"
        assert out[0]["baseline_flag"] == ""     # None becomes ""
        assert out[0]["product_label"] == ""     # blank for the labeler

    def test_export_writes_blanks_that_the_loader_skips(self, tmp_path):
        rows = [{"id": "1", "source": "kijiji", "title": "RTX 3080",
                 "description": "", "product_key": "", "flag_reason": ""}]
        path = tmp_path / "labels.csv"
        assert ds.export_label_csv(rows, path) == 1
        assert ds.load_labeled_csv(path, "product_label") == []

    def test_fixture_loads_every_row(self, labels_csv):
        examples = ds.load_labeled_csv(labels_csv, "product_label")
        assert len(examples) == 27
        assert all(e.group for e in examples)


class TestLabelHelpers:
    def test_rare_label_map_folds_low_support_only(self, tiny_examples):
        mapping = ds.rare_label_map(tiny_examples, min_support=5,
                                    other_label="OTHER")
        assert mapping == {"RTX 3060": "OTHER"}

    def test_apply_label_map_preserves_other_fields(self):
        example = ds.LabeledExample(text="t", label="RTX 3060", group="g",
                                    source="kijiji", listing_id="k27")
        mapped = ds.apply_label_map([example], {"RTX 3060": "OTHER"})
        assert mapped[0].label == "OTHER"
        assert mapped[0].text == "t" and mapped[0].listing_id == "k27"

    def test_label_distribution_counts(self, tiny_examples):
        counts = ds.label_distribution(tiny_examples)
        assert counts["RTX 3080"] == 7
        assert counts["RTX 3060"] == 1


class TestDatasetCli:
    def test_export_from_a_database(self, tmp_path, make_listing):
        db = str(tmp_path / "listings.db")
        store = Store(db=db).connect()
        store.observe_listing(make_listing("k1"), product_key="RTX 3080")
        store.close()

        out = tmp_path / "labels.csv"
        code = ds.main(["--export", str(out), "--db", db])

        assert code == 0
        rows = list(ds.csv.DictReader(out.open(newline="")))
        assert len(rows) == 1
        assert rows[0]["product_label"] == ""
