# Narrowing and Rejection

## Narrowing Order

When ideas sprawl, narrow in this order:

1. one user or scientific decision;
2. one task family or failure regime;
3. one primary mechanism;
4. one measurable contribution;
5. one deployment or evidence setting;
6. one fallback.

Do not begin by stacking more techniques.

## Rejection Tests

Reject or demote a candidate when:

- direct prior work covers its central claim;
- the novelty is only a list of known components;
- the claimed metric does not measure the stated objective;
- the required evidence or implementation is unavailable;
- a cheaper baseline could explain the gain;
- hardware, tuning, or search budget makes the comparison unfair;
- the central premise lacks an early falsification test;
- the mechanism is a platform component rather than the research contribution.

## Required Rejection Record

For every rejected candidate record:

- original claim;
- strongest overlapping evidence;
- reason for rejection;
- remaining role: discard, baseline, ablation, infrastructure, or fallback;
- condition under which it may be reconsidered.

## Primary and Fallback

The primary thesis must be one sentence with:

- intervention;
- target setting;
- decision objective;
- distinguishing constraint.

The fallback must not be a second equal thesis. State the precise failed gate that activates it.

## Stop Rule

Stop narrowing when:

- one thesis survives the checked overlap;
- it has a cheap falsification gate;
- available resources can run that gate;
- the claim remains meaningful after strong baselines;
- adding another mechanism would weaken identification.
