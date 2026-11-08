# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This is a multi-context monorepo. The only context today is `backend/` (the Django app); there is no separate frontend, since pages are server-rendered. Add a context here and in `GLOSSARY-MAP.md` if another one appears.

## Before exploring, read these

- **`GLOSSARY-MAP.md`** at the repo root: it points at one `GLOSSARY.md` per context. Read each one relevant to the topic.
- **`docs/adr/`**: system-wide decisions. Read ADRs that touch the area you're about to work in. Also check `backend/docs/adr/` for context-scoped decisions.
- **`docs/architecture/Decision/pilot-decisions.md`**: the numbered pilot decisions (D-numbers). They outrank skill preferences. `docs/architecture/tenancy.md` describes what is built for tenancy, audit, firms, jobs and numbering.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `domain-modeling` skill (reached from the `grilling` and `codebase-design` skills) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← system-wide decisions
└── backend/
    ├── GLOSSARY.md
    └── docs/adr/                      ← context-specific decisions
```

The pilot spec lives in `docs/product/`.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in the relevant `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for the `domain-modeling` skill).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
