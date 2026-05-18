# Factor Factory Public Case Studies

This file keeps reusable operating lessons from live Factor Factory work while avoiding bundled account/session state.

## Publishing Policy

- Keep reusable lessons, decision rules, failure modes, and review language.
- Keep template-level expressions when they help users understand the system.
- Do not bundle cookies, tokens, browser profiles, historical ledgers, raw audit logs, or account/session state.
- Prefer `case_id` over account-specific Alpha IDs in public teaching material.

## Case 1: GOOD Gate, Weak Test Stability

`case_id`: `public-case-calm-market-gate-good-unstable`

Pattern:

```text
trade_when(<calm-market condition>, <fundamental ratio signal>, <risk exit condition>)
```

Observed lesson:

- A market-state gate can lift headline quality metrics.
- A high in-sample grade is not enough if test-period Sharpe is weak.
- The structure is useful, but it should become a controlled contrast branch rather than the next large-scale search direction.

Reusable rule:

- Preserve the question: "When should this factor be active?"
- Do not over-expand a gate family just because one case received a good grade.
- Use later official checks and test-period behavior to decide whether the gate deserves more volume.

## Case 2: High Correlation Is A Stop Signal, Not Trash

Pattern:

```text
same seed family + small parameter/window changes
```

Observed lesson:

- High self-correlation usually means the local family has already been occupied by a similar submitted or stronger Alpha.
- The right response is not to delete the failed item.
- Archive it as evidence, then move to a different field family, data source, neutralization, or expression structure.

Reusable rule:

- A high-correlation archive should block same-family expansion.
- The archive should still feed retrospectives and generator policy.
- The next batch should change the information source or structure, not merely tweak windows.

## Case 3: Returns Alone Is Not A Ranking Rule

Pattern:

```text
high returns branch + Sharpe/Fitness/Turnover/Drawdown/Self-correlation gates
```

Observed lesson:

- Third-party screenshots often highlight high Returns, but high Returns can come with instability, turnover problems, or correlation failure.
- Returns should be a branch objective, not the only ranking rule.

Reusable rule:

- Let high-Returns candidates enter a dedicated exploration lane.
- Still rank final candidates with Sharpe, Fitness, Turnover, Drawdown, Test Sharpe, and platform checks.
- Review after 20 to 50 official results before increasing volume.

## Case 4: Datafield Profile Before Alpha Generation

Pattern:

```text
field profile gate -> candidate generation -> official simulation -> ledger -> retro
```

Observed lesson:

- Generating expressions before understanding field coverage and update behavior wastes official simulation slots.
- A field should earn its place through coverage, non-zero coverage, update rhythm, extreme-value behavior, long-window median behavior, and distribution scale.

Reusable rule:

- Profile fields first.
- Use profiled fields to generate task pools.
- Treat profile probes as diagnostics, not submit candidates.
