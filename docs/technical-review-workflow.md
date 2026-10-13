# Technical review workflow

The `Technical review` GitHub Actions workflow creates a fresh OpenAI Agents API session when an issue receives the `needs-tech-review` label. It can also be started manually from the Actions tab.

## Setup

Configure these repository settings before enabling the workflow:

- Actions secret `OPENAI_API_KEY`: an OpenAI application API key with `api.agents.read`, `api.agents.write`, and `api.responses.write` permissions.
- Actions variable `OPENAI_TECH_REVIEW_AGENT_ID`: `agent_4b255b49a6ff49c5a91c80f522b96b0bdba2f52282444d99b4`.

The workflow uses the official [`POST /v1/agents/sessions`](https://developers.openai.com/api/reference/python/resources/beta/subresources/agents/subresources/sessions/methods/create) endpoint. It references the saved agent with the top-level `agent_id`, omits the optional `agent` override, selects no additional execution environment, and sends the neutral input `Start.`. It does not override the saved agent configuration, wait for results, or post a GitHub comment. The prior test session is not reused.

On an HTTP error, the workflow prints only the API error's type, code, parameter, and message. Agent IDs and strings resembling API keys are redacted. Successful response bodies are not printed.

## Target discovery limitation

No repository or issue context is sent to OpenAI. The saved agent must therefore discover the labeled issue through its existing instructions and GitHub access. A manual run is likewise not associated with a particular issue. If the deployed agent cannot discover the target this way, the workflow cannot reliably select the triggering issue without relaxing that constraint.

Concurrency groups suppress overlapping runs for the same issue, and overlapping manual runs cancel the older run. They cannot prevent a second execution after an earlier run has already completed and the label is applied again.

The API request follows OpenAI's [Agents API session guidance](https://developers.openai.com/api/docs/guides/agents-api/sessions). Do not test it by dispatching the workflow unless a paid agent execution is intended.
