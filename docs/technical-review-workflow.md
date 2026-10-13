# Technical review workflow

The `Technical review` GitHub Actions workflow creates a fresh OpenAI Agents API session when an issue receives the `needs-tech-review` label. It can also be started manually from the Actions tab.

## Setup

Configure these repository settings before enabling the workflow:

- Actions secret `OPENAI_API_KEY`: an OpenAI application API key with `api.agents.read`, `api.agents.write`, and `api.responses.write` permissions.
- Actions variable `OPENAI_TECH_REVIEW_AGENT_ID`: `agent_4b255b49a6ff49c5a91c80f522b96b0bdba2f52282444d99b4`.
- Actions variable `OPENAI_MCP_VAULT_ID`: the ID of the existing vault that contains the saved agent's GitHub MCP credential.

The workflow uses the official [`POST /v1/agents/sessions`](https://developers.openai.com/api/reference/python/resources/beta/subresources/agents/subresources/sessions/methods/create) endpoint. It references the saved agent with the top-level `agent_id`, omits the optional `agent` override, selects no additional execution environment, attaches the existing MCP credential vault through `vault_ids`, and sends the neutral input `Start.`. The vault attachment is needed because the saved GitHub MCP tool references a `credential_id`; the Agents API does not make that credential available to a new session until its vault is attached. The workflow does not override the saved agent configuration, wait for results, or post a GitHub comment. Prior test sessions are not reused.

On an HTTP error, the workflow prints only the API error's type, code, parameter, and message. Agent IDs, vault IDs, and strings resembling API keys are redacted. Successful response bodies are not printed.

## Find the existing vault ID

The official API exposes vault and credential metadata without returning secret values. Use an application key for the same OpenAI project as the saved agent and grant it `api.vaults.read` for this lookup.

1. Retrieve the saved agent with `GET /v1/agents/{agent_id}` and note the GitHub MCP tool's `credential_id`.
2. List active vaults with `GET /v1/vaults?status=active&limit=100`. When a response has `has_more: true`, request the next page with `after={last_id}` while preserving the other query parameters. Repeat until `has_more` is false.
3. For every vault from every page, call `GET /v1/vaults/{vault_id}/credentials?limit=100`. Paginate each credential list in the same way with its own `last_id` and `after` cursor. Select the vault containing the matching `credential_id` and set that vault's ID as the repository variable `OPENAI_MCP_VAULT_ID`.

Include `Authorization: Bearer $OPENAI_API_KEY` and `OpenAI-Beta: agents=v1` on these read-only requests. The documented APIs return credential metadata, never the secret value. Keep the vault-list cursor separate from each vault's credential-list cursor. OpenAI's current documentation does not identify a dashboard screen that reliably exposes the vault ID. Only after exhausting every page, if the existing UI-created credential does not appear for the agent's project, do not invent an ID or create a replacement credential; confirm the agent, API key, and vault belong to the same OpenAI project, then have the project administrator locate or expose the existing vault.

## Target discovery limitation

No repository or issue context is sent to OpenAI. The saved agent must therefore discover the labeled issue through its existing instructions and GitHub access. A manual run is likewise not associated with a particular issue. If the deployed agent cannot discover the target this way, the workflow cannot reliably select the triggering issue without relaxing that constraint.

Concurrency groups suppress overlapping runs for the same issue, and overlapping manual runs cancel the older run. They cannot prevent a second execution after an earlier run has already completed and the label is applied again.

The API request follows OpenAI's [Agents API session guidance](https://developers.openai.com/api/docs/guides/agents-api/sessions). Do not test it by dispatching the workflow unless a paid agent execution is intended.
