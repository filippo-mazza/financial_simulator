# Aggregated expense JSON contract, version 1

The [JSON Schema](aggregated-expenses.schema.json) specifies the document's
structure. The [synthetic example](../tests/fixtures/aggregated-expenses-v1.json)
contains no real banking data. This contract describes the output of the
bank-statement prompt and the separate expense-summary import. The scenario
importer accepts simulator scenario files, not expense summaries.

The `format` discriminator is `financial-simulator-aggregated-expenses` and
`schema_version` is the integer `1`. Like scenario exports, the format and
version identify the document before interpreting its contents. This is a
different format from `financial-simulator-export`: future readers must reject
unknown versions with an actionable error unless an explicit migration exists.
No earlier aggregate version or migration is defined.

All properties are required; unknown properties are forbidden at every level.
Only the fields in the schema are permitted: no transaction records, merchant
names, account identifiers, references, descriptions, or free-text notes.
Do not use the JSON as a place to explain ambiguities; review them outside it.
These restrictions minimize data but cannot guarantee privacy.

## Amounts, period and currency

Amounts are JSON numbers in the document currency, expressed as nonnegative
magnitudes except for the signed reconciliation differences. `null` means
unknown, not zero; use zero only when verified. Sum using exact decimal
arithmetic at the precision of the source, without rounding away discrepancies.
Totals involving an unknown value are also unknown (`null`).

`currency` is a single uppercase ISO 4217 alphabetic currency code, or `null`
if unknown. The schema checks the code's shape; a consumer must also verify it
is an actual ISO 4217 code. Never combine or silently convert currencies.
Prepare a separate document for each currency in mixed-currency statements,
including only that currency's debits and credits in its reconciliation. If
the currency of an amount cannot be determined, do not mix it into a known
currency document; resolve it with the user. An unknown currency cannot be
marked reconciled.

`period.start` and `period.end` are inclusive calendar months in `YYYY-MM`
form, with start no later than end. Each monthly entry has a unique `month`
inside that range, in chronological order. A month may be included only if the
source covers it; its totals describe only the source coverage, which may be
partial at the period boundaries. Omitted months are missing, not zero. An
included month with verified no activity has zero totals. A gap in the
inclusive period makes the document unreconciled; no source coverage may be
invented to fill it. A month with partial coverage must not be interpreted as a
full-month spending estimate.

## Categories and review

Each category except `uncategorized` maps by identical name to the
`expense_type` of a simulator `ExpenseConfig`. `miscellaneous` is a known
classification; `uncategorized` is an unresolved expense and has no automatic
simulator mapping. The user must review and explicitly choose a destination or
leave it unapplied. The user may override any category's proposed destination
or amount before applying it, without silently replacing existing expenses.
Overrides are user decisions outside this aggregate JSON, not transaction
details or additional schema fields. Review the preview and explicitly confirm
any changes before applying them.

The simulator's `unexpected` type is stochastic and does **not** spend its
configured monthly amount like a normal recurring expense. Historical
`unexpected` totals therefore must not be blindly applied as a recurring
monthly amount; the user must decide how to model them. Likewise, users should
avoid double counting costs already represented by loans, property-linked
expenses, or other scenario components. Category totals are historical gross
spending, not an automatic prescription for future monthly inputs.

## Exclusions and reconciliation

Classify each source movement exactly once by the statement's verified sign
convention and transaction month. Gross purchase and bill debits, including
uncategorized debits, are expenses. Exclude own-account transfers, card
settlements already represented by card purchases, and other non-expense
debits in `excluded_debits_total`; avoid counting both sides of a transfer
twice if multiple statements overlap. Credits are classified as income,
own-account transfer credits, refunds, or other excluded credits. Unknown
credits go to `other_excluded_credits_total`. Refunds remain separate, even in
a different month from the purchase; they are not subtracted from gross
category totals. The user must review ambiguous classifications.

For each month, `expenses_total` equals the sum of all categories including
`uncategorized`. Each reconciliation total with a monthly counterpart equals
the sum of that field over all months in the inclusive period. If any month
or contributing value is missing, that period total is `null`. The source
debit and credit totals are independently calculated from all source movements
in the period and currency, as nonnegative magnitudes. Define:

```
debits_difference = source_debits_total - expenses_total - excluded_debits_total
credits_difference = source_credits_total - excluded_income_total
    - excluded_transfer_credits_total - refunds_total - other_excluded_credits_total
```

An unknown operand gives a `null` difference. `reconciled` is allowed only if
the currency and every amount are known, all months in the period are present,
all monthly and period sums agree, and both differences are exactly zero.
Otherwise the status is `unreconciled`. An unreconciled document may preserve
known mismatches for review, but must not claim a computed sum or difference
that disagrees with its operands. Structural JSON Schema validation alone
cannot enforce these cross-field rules, date ordering, ISO currency membership,
or missing months; the importer checks them separately and shows errors or
warnings without guessing. It cannot verify the completeness of the original
statement or detect partial coverage within an included month.
