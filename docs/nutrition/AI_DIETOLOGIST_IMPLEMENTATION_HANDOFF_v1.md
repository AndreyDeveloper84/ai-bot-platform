# AI Dietologist Implementation Handoff v1

Status: READY FOR OWNER REVIEW  
Type: cross-repository implementation handoff

## Objective

Upgrade Ayla from diary analysis and reference targets to a safe non-medical meal-plan proposal for healthy adults.

V1 supports:

- balanced plans;
- `lose | maintain | gain`;
- `omnivore | vegetarian | vegan`;
- explicit constraints and substitutions;
- backend-calculated totals and provenance.

V1 does not support therapeutic or restrictive named diets.

## Current-state gaps confirmed in code

1. `diet_preference` is a free string, is not collected by the anketa, and does not drive planning.
2. `vegan` and `vegetarian` branches exist in `compute_rda`, but the current validated health-flag API does not admit those keys.
3. Pregnancy and breastfeeding stop automatic target calculation before pregnancy/feeding RDA branches can participate in the normal calculation path.
4. Allergy collection is deferred; current flags cannot safely filter ingredients.
5. There is no approved dietary-pattern catalogue, recipe catalogue, meal-plan generator, or substitution validator.
6. Current AI output is a short diary comment, not a meal plan.
7. Several numeric rules exist in code without a row-level evidence record approved by a nutrition professional.

These are implementation gaps, not permission to relax safety gates.

## Workstream A — professional review

Owner: product + registered nutrition professional

- approve the product scope;
- review every `REVIEW_REQUIRED` evidence entry;
- approve target ranges and tolerances;
- approve the initial ingredient/recipe corpus;
- approve golden personas and expected outcomes;
- decide the public product name while clinical capability remains excluded.

No meal-plan feature flag may open before this workstream signs off.

## Workstream B — beautygo_backend

### Domain and migrations

Add:

- `FoodConstraintsProfile` with revision history;
- structured allergens, intolerances, exclusions, dislikes, and preference overlay;
- versioned `DietaryPattern`, `Ingredient`, `Recipe`, and recipe-ingredient data;
- `MealPlan`, `MealPlanRevision`, `MealPlanDay`, and `MealPlanItem`;
- evidence and calculation provenance snapshots.

Do not store safety-critical constraints only as unvalidated free text.

### Services

Add:

- constraints upsert and confirmation;
- supported-pattern resolver;
- deterministic ingredient/recipe filter;
- meal-plan candidate builder;
- nutrient calculator;
- validation pipeline;
- substitution generator plus revalidation;
- proposal confirmation and append-only revision service.

The LLM is not a dependency of validation.

### API

Internal endpoints:

- GET/PATCH/confirm food constraints;
- list supported patterns and preference overlays;
- create/validate meal-plan proposal;
- confirm/edit/close plan;
- retrieve active plan and revisions.

Every response includes stable decision and reason codes.

### Repair current inconsistencies

- replace the free-form `diet_preference` path with a versioned enum migration;
- route vegetarian/vegan data through the constraints domain, not medical health flags;
- remove or isolate unreachable RDA branches after professional review;
- keep pregnancy, breastfeeding, minors, eating-disorder, and medical conditions fail-closed.

## Workstream C — ai-bot-platform

### Conversation

Add a goal-to-plan flow:

1. identify explicit request;
2. obtain consent;
3. collect or read confirmed profile;
4. collect food constraints;
5. request backend validation;
6. show proposal and uncertainty;
7. confirm or edit;
8. expose active plan and revision history.

Extraction returns `null` for unspoken fields.

### Integration

Extend the nutrition client with typed methods for constraints and meal plans. Backend decision codes are authoritative.

### Mini App

Add:

- constraints review screen;
- plan proposal screen;
- meal details and substitutions;
- edit/confirm/close actions;
- source and uncertainty disclosure;
- clear separation of target, proposal, fact, and achieved result.

### Wording

Continue the existing no-diagnosis, no-pressure, autonomy, and anti-nag policies. Do not call a diary estimate a deficiency or a proposed menu a prescription.

## Test pack

Backend unit and contract tests:

- all three goal strategies;
- omnivore, vegetarian, and vegan overlays;
- explicit no-allergy and resolved allergy;
- ambiguous allergen fail-closed;
- allergen-safe substitutions;
- unsupported named diets;
- missing target and missing constraints;
- minors, pregnancy, breastfeeding, eating-disorder, and health flags;
- AI-estimated ingredient quality limits;
- deterministic recalculation;
- revision history.

Cross-repository replay tests:

- “Составь меню для похудения”;
- “Хочу набрать вес, мясо не ем, яйца и молочное можно”;
- “Я веган, аллергия на арахис”;
- “Составь кето”;
- “У меня диабет, распиши питание”;
- correction of an allergy after proposal;
- change of goal after plan activation.

Professional golden cases must include expected allowed foods, forbidden foods, ranges, decision code, and acceptable explanation boundaries.

## Delivery order

1. Approve docs and evidence decisions.
2. Implement constraints domain and tests.
3. Implement recipe/ingredient catalogue with provenance.
4. Implement deterministic plan proposal and validators.
5. Add internal API.
6. Add bot integration and replay tests.
7. Add Mini App surfaces.
8. Professional review of golden cases.
9. Internal feature flag.
10. Limited pilot with audit logging.

## Definition of done

Ayla can create a useful balanced proposal for a healthy adult without inventing facts, violating constraints, or implying medical authority; every applied number and food choice is reconstructable from stored inputs, rules, and versions.
