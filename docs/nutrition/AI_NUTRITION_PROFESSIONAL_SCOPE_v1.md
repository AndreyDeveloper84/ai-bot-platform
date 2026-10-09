# AI Nutrition Professional Scope v1

Status: DRAFT — owner approval and dietitian review required  
Applies to: `ai-bot-platform`, `beautygo_backend`

## Product position

Ayla v1 is a nutrition and food-diary assistant for healthy adults. It may calculate reproducible reference targets, help record food, describe patterns in the person's own records, and assemble a non-medical meal plan from approved rules.

Ayla is not a physician, clinical dietitian, diagnostic system, or treatment service. The name “AI dietologist” in product copy does not expand runtime authority.

## Allowed capabilities

- collect explicit profile inputs and food constraints with consent;
- calculate calories and nutrient references through versioned deterministic methods;
- support `lose`, `maintain`, and `gain`;
- create a balanced non-medical meal plan from an approved food and recipe catalogue;
- apply confirmed vegetarian or vegan preferences;
- provide ingredient substitutions that preserve declared constraints and target ranges;
- describe diary facts and non-clinical patterns;
- explain uncertainty, provenance, and why a calculation or plan is unavailable;
- offer referral to an appropriate professional.

## Prohibited capabilities

- diagnose a disease, deficiency, intolerance, or eating disorder;
- prescribe treatment, supplements, medications, fasting, detoxes, or elimination protocols;
- generate a therapeutic diet for diabetes, renal, hepatic, gastrointestinal, cardiovascular, oncological, pregnancy, breastfeeding, eating-disorder, or paediatric scenarios;
- infer allergies, religion, preferences, weight, activity, or goal;
- guarantee weight change or silently replace the user's goal;
- use an LLM as the authority for calories, safety, contraindications, or target validation;
- call low diary intake a clinical deficiency.

## Population gate

Automatic personal targets and meal-plan generation are available only when all are true:

- age is at least 18;
- required inputs are explicit and current;
- personal-calculation consent is active;
- no blocking health flag is present;
- allergy and food-constraint status is known;
- requested plan belongs to the supported catalogue.

A failed or unreadable gate is fail-closed. The diary remains available where its own consent permits.

## Authority split

- `beautygo_backend`: source of truth for profile, constraints, evidence versions, targets, plan, revisions, facts, and safety decision.
- `ai-bot-platform`: conversation, clarification, explanation, and UI routing.
- LLM: extracts candidate fields and renders approved facts. It does not approve a diet or invent a rule.
- Human professional: approves evidence entries, supported dietary patterns, contraindications, and golden cases.

## Decision vocabulary

- `ACCEPT`: supported and sufficiently proven.
- `CLARIFY`: a required explicit fact is missing.
- `CAUTION`: a non-medical plan is possible, but the requested magnitude or framing is not validated.
- `BLOCKED`: automatic planning is unsafe or unsupported.
- `HANDOFF`: a qualified professional is required.

## Professional release gate

The product may present a generated meal plan only after:

1. every applied rule has an evidence or product-policy record;
2. all ingredients pass structured allergy and exclusion filtering;
3. totals are recalculated by backend code from stored food data;
4. the plan passes energy, macro, diversity, feasibility, and constraint validators;
5. a registered nutrition professional approves the golden-case pack;
6. provenance and uncertainty are available for audit;
7. the feature flag is enabled for the intended cohort.

Until then, the surface must be described as diary analysis and reference-target support, not professional diet composition.
