# Supervised Baseline Filter

## Purpose

The supervised baseline filter is a small interpretable ML challenger.
It is intentionally simpler than the Transformer layer and is meant to answer one question first:

Can a transparent model improve or filter baseline strategy decisions under leakage-safe walk-forward evaluation?

The model implementation lives in `src/scalper_ai/models/baseline_filter.py`.
The walk-forward evaluation lives in `src/scalper_ai/validation/supervised_filter.py`.

## Model Shape

The filter:

- consumes `SupervisedDataset` rows built by the existing leakage-safe dataset builder
- standardizes each feature using only the training fold
- computes positive and negative target centroids
- uses the centroid difference as a transparent linear weight vector
- exposes signed scores, directional predictions, and per-feature weights

No future labels or validation/test rows are used during fitting.

## Walk-Forward Evaluation

Use `run_supervised_filter_walk_forward()` with the existing `WalkForwardConfig`.
Each fold:

- materializes train, validation, and test partitions in timestamp order
- fits only on train
- predicts only on test
- reports directional accuracy, coverage, long ratio, short ratio, and neutral ratio
- aggregates mean feature importance across folds

## Promotion Rule

The supervised filter is only a challenger until it:

- beats the baseline strategy suite under comparable costs
- passes the validation gate
- produces stable feature importance across walk-forward folds
- has shadow decision deltas reviewed against the champion
- does not introduce hidden global state or online/offline feature divergence
