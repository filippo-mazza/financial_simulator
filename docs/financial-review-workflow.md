# Financial review workflow

The separate `Financial review` workflow invokes the existing OpenAI-hosted Financial Review Agent for one exact pull-request head. It is advisory: it never approves or merges a PR.

## Setup

Create the repository labels `needs-financial-review` and `Financial reviewed`. Configure `OPENAI_FINANCIAL_REVIEW_AGENT_ID` with the existing saved agent ID. The workflow reuses `OPENAI_API_KEY`, `OPENAI_MCP_VAULT_ID`, and `OPENAI_GITHUB_MCP_SERVER_LABEL` from Technical Review; it neither creates an agent nor duplicates GitHub credentials.

Apply `needs-financial-review` to an open, non-draft, same-repository PR targeting `main`. Only an event performed by the repository owner starts an eligible run. For an explicit retry, keep/reapply the label and use `workflow_dispatch` with the PR number. Fork PRs are rejected before their code is checked out or any paid secret is exposed.

The validation job first checks out trusted automation from `main`, resolves and captures the PR's immutable head SHA, and preserves the trusted validation script under `RUNNER_TEMP`. It then checks out exactly that same-repository SHA with persisted checkout credentials disabled. Deterministic validation runs against the PR engine without paid secrets. A single JSON artifact is transferred to a separate, fresh credentialed runner, which checks out only trusted `main` automation. Before any paid call, that runner enforces the report's schema, numeric finiteness, field counts, string lengths, and 64 KiB size limit.

The compact JSON validation covers seeded reproducibility, cash/net-worth reconciliation, loan amortization, represented income/expense growth, income tax, and investment contribution fees. It uses synthetic data, no Streamlit automation, and reports unsupported concepts as limitations. The report, PR number, and head SHA are passed to the saved agent because Actions artifacts are not assumed visible through GitHub MCP.

The agent must post a new PR comment containing the exact head SHA and one of `PASS`, `PASS WITH WARNINGS`, `CHANGES REQUESTED`, or `UNABLE TO REVIEW`. The runner also requires a completed root turn and a successful call to the configured GitHub MCP. It then fetches the PR again and requires the head SHA, target, open/draft state, repository, and request label to remain unchanged. Labels are updated before the completed-SHA record; failures restore the request labels and release the duplicate reservation when possible, making finalization retryable.

## Cost, duplicates, and retries

Financial Review has its own one-running/one-pending concurrency group and its own `financial-review-state` branch. Unrelated label events use unique no-op groups and cannot displace a pending paid review. State is updated with compare-and-swap writes at `.github/financial-review-state.json`.

- No PR/head SHA is reviewed twice after successful completion.
- A maximum of 10 paid attempts may start per UTC day.
- Paid starts are at least five minutes apart, including across midnight.
- Missing, malformed, unreadable, or unwritable state fails closed before session creation.
- Failed agent runs release the duplicate reservation when possible, but their paid attempt remains counted. An orphaned active reservation expires after one hour.
- Quota/cooldown/duplicate skips do not add `Financial reviewed` or remove the request label.
- The 30-minute Actions timeout cannot guarantee cancellation of an already detached hosted session.

All workflow, state, validation, and agent-dispatch tests use fakes and injected clocks. They require no OpenAI key and make zero live paid calls.
