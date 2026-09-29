# Ayla Knowledge Area Taxonomy

## 1. Purpose

The Knowledge Area Taxonomy defines the canonical location and responsibility of project knowledge. It answers a practical question: **where should a new document live?**

It is needed to:

- prevent the same decision or contract from being duplicated across folders;
- make ownership, dependencies, and document status discoverable;
- keep product intent, business behavior, architecture contracts, UX contracts, and implementation knowledge separate;
- preserve a reliable path from a product decision to its operational and technical realization.

The taxonomy is part of project architecture because knowledge has boundaries, owners, dependencies, and contracts just as software components do. A misplaced document creates architectural ambiguity: different teams may treat different copies as authoritative, or implementation details may silently redefine a product or domain decision.

## 2. Knowledge Architecture Principles

### One canonical location

Every document has one primary canonical location. Copies of the same meaning must not be maintained in multiple folders. Links and short references are allowed; competing copies are not.

### Separation of concerns

Knowledge is separated into Foundation, Strategy, Architecture, Product Operations, UX, Engineering, and Operations. A document may reference another area, but its canonical location follows its primary responsibility.

### Document purpose over folder name

Placement is determined by what the document governs, not merely by its title or the folder name that appears convenient.

### Canon before implementation

The contract, decision, or expected behavior is defined first. Implementation documentation describes how an accepted contract is realized and must not silently redefine it.

### No silent migration

Moving an existing document requires an explicit decision. Taxonomy adoption does not authorize renaming, moving, rewriting, deleting, or duplicating existing documents.

## 3. Canonical Knowledge Areas

| Area | Purpose | Contains | Does not contain | Owner |
|---|---|---|---|---|
| `00 Foundation` | Canonical principles, rules, ownership, and governance | Constitution, Glossary, Repository Responsibility Matrix, Domain Capability Registry, Decision Log | Specific business features, UX, implementation details | Governance / Architecture owner |
| `01 Product` | Product Strategy, Vision, and Discovery | Product Vision, Product Principles, Product Map, User Journeys, Conversation Design, Discovery documents, Product strategy | Technical contracts, backend implementation, domain event schemas | Product owner |
| `05 Architecture` | Domain and System Architecture Contracts | Domain models, ownership contracts, event contracts, system boundaries, data contracts; e.g. Repository Responsibility Matrix, MVP Appointment Contract, Domain Event Registry | UI specifications, marketing strategy, implementation tasks | Architecture owner |
| `06 Product` | Product Operations and Operational Contracts | Operational models, business workflows, MVP behavior contracts, capability definitions; e.g. Ayla Salon Operations MVP Contract | Screen designs, low-level API details, code implementation | Product Operations owner |
| `07 UX` | Interaction and Screen Contracts | Screen specifications, user interactions, UX flows, information architecture; e.g. Master Today Screen Contract, Admin Calendar UX Contract, Customer Booking UX Contract | Domain ownership decisions | UX owner |
| `08 Engineering` | Implementation knowledge | Coding standards, technical implementation, API specifications, deployment, infrastructure | Product intent, domain ownership contracts, UX decisions | Engineering owner |
| `09 Operations` | Real-world operational management | Operational procedures, support, business execution | Product strategy, system contracts, code-level implementation | Operations owner |

Area numbering is retained as a repository convention. The number alone does not define responsibility; the document's purpose does.

## 4. Document Classification Rules

| Question answered by the document | Canonical area |
|---|---|
| Why are we building this? | `01 Product` — Product Strategy |
| How should the business function work? | `06 Product` — Product Operations |
| Who owns the data and where are system boundaries? | `05 Architecture` — Architecture |
| What data and interactions does the screen need? | `07 UX` — UX |
| How is it implemented in code or infrastructure? | `08 Engineering` — Engineering |
| How is work executed in the real-world operation? | `09 Operations` — Operations |
| Is this a cross-project rule, glossary item, or governance decision? | `00 Foundation` |

When a document answers multiple questions, split it into contracts where practical. If splitting is not yet practical, place it according to its primary decision and link the dependent areas.

## 5. Current Repository Assessment

This assessment is intentionally non-destructive. No existing files, folders, indexes, or canon files are changed, and no migration is performed.

The requested assessment scope is:

| Document | Current Location | Recommended Area | Reason |
|---|---|---|---|
| Documents currently under `01 Product` | Existing repository location | `01 Product` unless their primary purpose is operational, architectural, UX, or implementation knowledge | The area is reserved for Product Strategy, Vision, and Discovery. Each document should be checked against the classification questions before any future move. |
| Documents currently under `05 Architecture` | Existing repository location | `05 Architecture` when the document defines domain/system boundaries or contracts | Architecture must remain the canonical home for ownership, event, data, and boundary contracts. |
| Documents currently under `06 Product` | Existing repository location | `06 Product` when the document defines business workflows, operational models, capabilities, or MVP behavior | This area separates business behavior from product strategy, UX screens, and implementation details. |

Because the local repository inspection was unavailable in this run due to a sandbox-helper startup failure, individual filenames and duplicate findings are not asserted here. A future repository audit must populate this table with one row per document before any migration proposal is approved.

## 6. Canon Navigation Rules

Every new document must state:

- purpose;
- owner;
- dependencies;
- status;
- related documents.

Required frontmatter:

```yaml
type: ""
status: "Draft"
owner: ""
domain: ""
depends_on: []
```

Recommended additional fields are `related_documents`, `created`, `updated`, and `canonical_area`.

Before creating a document, the author must:

1. identify the primary question the document answers;
2. check for an existing canonical document with the same meaning;
3. choose one canonical area and owner;
4. record dependencies and related documents;
5. link to the canon rather than copying its content.

## 7. Naming Convention

Names should describe the governed artifact and its document type. Avoid vague names such as `Notes`, `Plan`, or `Misc`.

Examples:

| Area | Naming pattern | Example |
|---|---|---|
| Architecture | `<Subject> Domain Contract.md` | `Ayla Domain Contract.md` |
| Product / Operations | `<Subject> Capability MVP Contract.md` | `Ayla Capability MVP Contract.md` |
| UX | `<Subject> Screen Contract.md` | `Ayla Screen Contract.md` |
| Engineering | `<Subject> System Implementation Guide.md` | `Ayla System Implementation Guide.md` |

Use `Contract` for an agreed behavior, boundary, or interface; `Guide` for implementation or operating instructions; `Registry` for a maintained inventory; and `Decision Log` for recorded decisions.

## 8. Document Lifecycle

| Status | Meaning |
|---|---|
| `Draft` | Work in progress; not an authoritative source. |
| `Review` | Submitted for review by the relevant owner(s). |
| `Proposed` | Coherent proposal awaiting formal acceptance. |
| `Accepted` | Approved content that may guide implementation or operations. |
| `Canonical` | The authoritative source for its subject and area. |
| `Deprecated` | No longer authoritative; retained for traceability and linked to its replacement where applicable. |

Status changes should be explicit and attributable. A document must not be treated as canonical merely because it is present in a canon-looking folder.

## 9. Open Questions

| ID | Question | Status |
|---|---|---|
| KAT-001 | What is the final name and scope of `06 Product`? | Owner decision required |
| KAT-002 | Is a separate Strategy area needed, or is `01 Product` sufficient? | Owner decision required |
| KAT-003 | Is a separate Operations area needed, or is `09 Operations` sufficient? | Owner decision required |
| KAT-004 | Is a separate Governance area needed, or is governance sufficiently covered by `00 Foundation`? | Owner decision required |
| KAT-005 | Is a separate AI Knowledge area needed, or should AI knowledge remain classified by purpose and domain? | Owner decision required |

## 10. Validation Checklist

- [x] Folder responsibilities are explicitly described.
- [x] `01 Product` and `05 Architecture` are separated.
- [x] UX is separated from Product and Architecture.
- [x] Implementation knowledge is separated from contracts.
- [x] Future documents have a classification rule and candidate canonical area.
- [x] The taxonomy does not authorize migration or modification of existing content.
- [ ] Individual repository documents have been audited for duplication and placement. Blocked in this run by the local inspection-tool failure.

