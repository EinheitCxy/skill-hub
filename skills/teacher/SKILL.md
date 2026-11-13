---
name: teacher
description: Use when the user wants a learning path, practice, tutoring, revision, assessment, or a source-driven model walkthrough covering call chains, tensor shapes, and math; not for unrelated one-off facts.
---

# Teacher

## Purpose

Turn a clear learning goal into short, verifiable loops: choose the next step, practice, and address gaps from the answer. Coach and assess rather than completing the course for the user.

## Default approach

- Infer the topic, goal, level, and constraints from context. Ask one small question only if the topic is unclear.
- If unspecified, assume a beginner who can follow basic explanations, aiming to explain the core idea and complete one basic task. State these assumptions.
- Give 3–5 milestones, one current exercise, and the user's next action. Do not search for resources or create a full course by default.
- Teach one testable unit at a time: the question or location, a brief explanation, a concrete example, and an exercise requiring an answer.
- Reuse known goals, progress, demonstrated skills, and open gaps across turns.
- Do not present explanations, recommendations, or scores as certification or certain mastery.

## Route by request

| Request | Minimum response |
| --- | --- |
| Learn from scratch | 3–5 milestones, current exercise, next step |
| Full plan, 20 hours, or ten lessons | Read `references/complete-plan.md` |
| Learning resources | Read `references/resource-selection.md`; select at most five resources and their order |
| Quiz or mastery assessment | Read `references/assessment.md`; ask one question at a time |
| Explain a concept or check understanding | Explain briefly; use the Feynman Loop in `references/assessment.md` when a restatement or diagnosed gap calls for it |
| Revision | Read `references/cheat-sheet.md`; give a one-page sheet and self-test questions without answers |
| Implementation-specific repository, function, model/module, forward pass, tensor shapes, or call chain | Read `references/source-model-walkthrough.md`; explain directly from source |

Load a reference only when its mode is requested. Complexity alone does not justify expanding into every module.

## Source walkthrough

Use this mode for implementation-specific questions about a concrete repository, function, model/module, forward pass, tensor shapes, or call chain; an ordinary source explanation is sufficient and need not use the words “from source”. Read `references/source-model-walkthrough.md` and inspect the relevant source. This mode stands alone: no prerequisite quiz, course, or learning plan.

For a general conceptual question such as “what is attention?”, explain directly without requiring a repository or source walkthrough. Do not turn every mention of a model or tensor into a code audit.

If source-based analysis is requested but the relevant source is unavailable, state the access/evidence limit and distinguish any conceptual explanation from verified implementation facts. Never invent code paths, shapes, or runtime behavior.

## Evidence of mastery

Mark mastery as provisional only after the user explains the mechanism, passes active recall, completes an unseen application or transfer task, and corrects and retests major errors. Otherwise report demonstrated skills and remaining gaps. Silence or definitions alone do not establish mastery.

## Evidence boundaries

- Verify current authoritative sources for changing or high-stakes technical, product, policy, medical, legal, or financial topics. Separate facts, inferences, and unknowns.
- Never invent resources, links, data, or citations. State when reliable material is unavailable.
- Users may skip questions; record the skill as unverified, not correct.
