# Provenance header

Every fitness function carries this KDoc (or Javadoc or docstring) block directly above it.
`check_provenance.py` parses it.

```kotlin
/**
 * @fitness
 * Rule: <the constraint, one sentence, domain words>
 * Reason: <why it exists: the threat, incident, regulation or domain principle>
 * Set by: <team or person>, <date>
 * Source: <ADR / PR / commit / incident id / doc:line>
 * Revisit when: <observable evidence that would make us change or drop it>
 * Regeneration test: <yes|no> — <what breaks, for whom, if a regenerated system changed this>
 */
```

## Field rules

| Field | Must | Must not |
|---|---|---|
| Rule | say what is forbidden or required | name the ArchUnit API |
| Reason | cite a cause | be `unknown`, `TODO`, "best practice", "clean architecture" |
| Set by | name an owner who can change it | name the tool that wrote it |
| Source | be checkable: a SHA, PR #, ADR path or doc line | say "team discussion" with no link |
| Revisit when | describe evidence, not a date | say "never" |
| Regeneration test | answer yes or no, plus consequence | be blank |

## Flagged rules

If `Reason` cannot be filled from evidence:

```kotlin
/**
 * @fitness
 * Rule: controllers do not import clients directly.
 * Reason: unknown
 * Set by: unknown
 * Source: observed structure only (no doc found)
 * Revisit when: owner confirms or rejects the rule
 * Regeneration test: unknown — ask the platform team
 */
@Disabled("no provenance: reason unknown; see docs/phoenix/... ")
```

`check_provenance.py --strict` fails if a rule with no reason is enforced. Wire it into CI when
the team is ready. It is opt-in.
