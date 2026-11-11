# Comparison and Statistics

## Before Comparing

Record the unit of analysis: episode, seed, task instance, trajectory, action step, object, or batch. Do not pool a lower-level unit and call it an independent task result, and do not assume records are IID when one task or seed produces multiple observations.

For every arm, check the requested sample set, actual completed trials, exclusions, missing records, duplicates, and failure reason. Preserve raw identifiers so the aggregation can be rerun.

## Pairing

Use paired analysis only when a row links the same experimental unit under both conditions. State the pairing key and count of matched rows. Analyze unmatched rows separately and explain why they are absent.

For binary paired outcomes, a paired disagreement test can be appropriate; for continuous paired outcomes, use a method justified by the paired distribution. Report effect size and uncertainty alongside a p-value. Do not select a test merely because it returns significance.

## Result Table

Keep columns for condition, checkpoint, precision or quantization scope, task ID, seed, evaluator version, outcome, exclusion reason, and raw metric. Add trajectory, contact, or grasp fields only when the logger defines them.

## Missingness and Failures

Distinguish unavailable data, crashed runs, timeout, invalid evaluator output, policy failure, and deliberately excluded trials. Recompute both the stated protocol and a sensitivity view when the exclusion choice can alter the conclusion.
