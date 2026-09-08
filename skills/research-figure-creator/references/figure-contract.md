# Figure Contract

## Required Record

| Field | Record |
| --- | --- |
| Question | The scientific or engineering question the figure addresses |
| Panel claim | The limited conclusion for each panel |
| Source | Real-data file/query/log identifier, or explicit synthetic generator |
| Origin and mode | Measured versus illustrative; offline versus online protocol; simulated versus physical environment when relevant |
| Unit | Independent observation used for aggregation and uncertainty |
| Conditions | Treatment, control, matched settings, exclusions, and sample count |
| Transform | Filters, joins, normalization, aggregation, and uncertainty method |
| Encoding | Axis quantity/unit, scales, color/shape meaning, and legend |
| Regeneration | Script/notebook/query path, parameters, environment, and output path |

## Comparison Rules

Use a common scale only when values share a quantity and unit. State when a higher or lower value is favorable. Keep raw observations available when aggregation can conceal heterogeneity, failures, or paired disagreement.

If samples are paired, preserve the pairing in the visual when it helps interpretation and match the reported statistical analysis. Do not use an error bar without defining whether it is a confidence interval, standard error, standard deviation, or quantile range.

## Caption Pattern

Name the comparison, unit, conditions, aggregation, uncertainty, and narrow conclusion. A caption should not introduce a mechanism claim that is absent from the data or body text.
