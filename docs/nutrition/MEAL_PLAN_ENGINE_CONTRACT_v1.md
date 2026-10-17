# Meal Plan Engine Contract v1

Status: DRAFT — implementation requires approved catalogue and professional review

## Result

The engine produces a proposal, not a prescription:

- one or more days;
- meals with portions;
- calculated calories, macros, and supported micronutrients;
- substitutions;
- unresolved limitations;
- applied rule and evidence versions.

The proposal becomes active only after user confirmation.

## Inputs

Required:

- active and consented nutrition profile;
- validated goal strategy;
- confirmed food constraints revision;
- confirmed nutrition targets or user-entered targets;
- approved dietary-pattern catalogue version;
- approved recipe and ingredient catalogue versions.

Optional:

- meal count;
- schedule;
- cooking time;
- budget band;
- region and availability;
- saved meals and explicit favourites.

Diary history may improve convenience, but it may not infer an allergy, medical condition, preference, or target.

## Processing pipeline

1. Run safety and population gates.
2. Resolve the supported base pattern, goal strategy, and preference overlay.
3. Filter ingredients and recipes by hard constraints.
4. Build deterministic candidate combinations.
5. Calculate nutrients from stored ingredient facts.
6. Validate target ranges and plan diversity.
7. Generate substitutions and validate them again.
8. Record all rule, evidence, food-data, and constraint versions.
9. Let the LLM explain the validated proposal in natural language.
10. Ask the user to confirm or edit.

The LLM does not select an unsupported diet, alter nutrient totals, waive a constraint, or mark the proposal safe.

## Validators

A proposal must pass:

- `safety_gate`;
- `allergen_gate`;
- `hard_exclusion_gate`;
- `supported_pattern_gate`;
- `energy_range_gate`;
- `macro_range_gate`;
- `food_data_quality_gate`;
- `diversity_gate`;
- `feasibility_gate`;
- `substitution_revalidation_gate`;
- `provenance_complete_gate`.

A validator returns `PASS | CLARIFY | CAUTION | BLOCKED | HANDOFF` with stable reason codes.

## Target ranges

The engine works with ranges, not false exactness. Range widths and tolerances must come from the evidence registry or an explicit product-policy record. No tolerance is hard-coded in LLM prompts.

## Food-data quality

- Verified local or official data is preferred.
- AI estimates are marked and cannot be the sole basis for high-confidence conclusions.
- Unknown portion or ingredient composition produces clarification or visible uncertainty.
- Final totals are always recalculated by backend code.

## Plan lifecycle

States:

- `draft`;
- `proposed`;
- `active`;
- `review_required`;
- `closed`.

Changes are append-only revisions with:

- actor: `user | engine | professional`;
- reason code;
- source plan revision;
- changed fields;
- evidence and constraint versions;
- effective timestamp.

## Replanning

Replanning may be offered when:

- the user changes the goal, target, or constraints;
- an active plan becomes incompatible with a new hard constraint;
- the user explicitly asks;
- an approved deterministic rule fires on sufficient facts.

A diary deviation alone does not silently change the plan. The system shows facts, asks permission, and preserves history.

## Unsupported requests

Named or therapeutic diets outside the approved catalogue return `BLOCKED` or `HANDOFF`. The LLM may explain the limitation but may not improvise a protocol.

## Acceptance criteria

- identical inputs and versions produce identical calculated totals;
- no allergen or hard exclusion appears in any meal or substitution;
- unsupported diets never reach generation;
- every number has provenance;
- plan history remains reconstructable;
- tests prove fail-closed behaviour for missing and unreadable context.
