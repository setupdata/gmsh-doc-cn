# Domain Docs

This is a single-context repository.

## Before exploring

Read these files when they exist:

- `CONTEXT.md` at the repository root;
- relevant ADRs under `docs/adr/`.

If they do not exist, proceed silently. Domain-modeling skills create them
when the project has resolved terminology or architectural decisions worth
recording.

## Layout

```
/
├── CONTEXT.md
├── docs/
│   └── adr/
└── ...
```

## Vocabulary

Use domain terms as defined in `CONTEXT.md`. Avoid replacing established
terms with unrecorded synonyms. If a necessary concept is absent, note it
for domain modeling.

## ADR conflicts

If proposed work conflicts with an existing ADR, report the conflict
explicitly instead of silently overriding the decision.
