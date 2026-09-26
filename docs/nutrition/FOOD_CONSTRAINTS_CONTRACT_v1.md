# Food Constraints Contract v1

Status: DRAFT — required before meal-plan generation

## Purpose

Ayla must know what the person can and will eat before composing a plan. Missing constraints are not permission to assume “no restrictions”.

## Data model

`FoodConstraintsProfile` contains:

- `preference`: `omnivore | vegetarian | vegan | unspecified`;
- `allergens`: structured allergen identifiers confirmed by the user;
- `allergen_free_text`: optional original wording, stored with provenance;
- `intolerances`: structured identifiers plus optional free text;
- `excluded_foods`: ingredients the user refuses or cannot consume;
- `disliked_foods`: preferences, not safety constraints;
- `religious_or_cultural_rules`: explicitly selected rules;
- `eggs_allowed` and `dairy_allowed` where relevant;
- `meals_per_day`: explicit value or `unspecified`;
- `available_cooking_minutes`;
- `cooking_equipment`;
- `budget_level`: optional user-selected band;
- `region` and `availability_notes`;
- `effective_from`, `confirmed_at`, `source`, and `version`.

Every field distinguishes `unknown` from a negative answer.

## Allergy rules

- Allergy constraints are hard exclusions.
- An unclear allergen produces `CLARIFY`, not a generated plan.
- Free text is never the only runtime check: it must resolve to a structured identifier or remain blocking.
- Substitutions must be checked through the same allergen filter.
- Cross-contamination guarantees are outside Ayla's authority and must be stated.
- A severe allergy or uncertain emergency risk routes to professional or emergency guidance under the existing safety policy.

## Preference rules

- Preference, dislike, intolerance, and allergy are different classes and are not merged.
- Vegetarian and vegan status is never inferred from diary history.
- Removing an ingredient for preference must not be presented as medical necessity.
- A preference change creates a new revision and does not rewrite old plans.

## Collection flow

1. Ask preference.
2. Ask allergies and intolerances separately.
3. Confirm resolved structured constraints.
4. Ask exclusions and dislikes.
5. Ask practical constraints only when a meal plan is requested.
6. Show a final summary for confirmation.
7. Store consent, source, version, and timestamp.

## Generation gate

A meal plan is allowed only when:

- preference is explicit;
- allergy status is explicitly `none_confirmed` or contains resolved identifiers;
- every recipe ingredient has structured composition and allergen metadata;
- no hard exclusion matches;
- unresolved free text is absent;
- the profile revision used by the plan is recorded.

## Corrections and deletion

The user can correct or delete constraints. A correction invalidates pending proposals and marks active plans as `review_required`; it never silently edits past plan revisions.
