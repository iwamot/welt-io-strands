# Strands Harness Agent

Serve a [Strands Harness](https://github.com/strands-agents/harness-sdk/tree/main/harness-py) agent through Welt. `create_harness()` builds a regular Strands `Agent`, so the same `start_reply` and `renderable_events` wiring applies.

This example combines the Harness system prompt, context management, and task tracking with shell, file, and web-fetch tools. A small `current_time` tool makes the approval flow easy to try. `interventions="ask"` pauses tool calls for approval through Welt's buttons or text field.

## Run Locally

Download the single-file agent and start it with [uv](https://docs.astral.sh/uv/):

```sh
curl -O https://raw.githubusercontent.com/iwamot/welt-io-strands/main/examples/harness/main.py
uv run --with bedrock-agentcore --with strands-harness --with welt-io-strands \
  --with "botocore[crt]" main.py
```

The server listens on port 8080. Connect Welt using its [local Quick Start](https://github.com/iwamot/welt#quick-start). AWS credentials are resolved through the standard SDK chain; `botocore[crt]` supports credentials from `aws login`.

`MODEL_ID` defaults to `global.anthropic.claude-sonnet-4-6`. `BEDROCK_REGION` selects the model's region; unset, it follows `BedrockModel`'s normal region resolution. Enable access to the selected model in Amazon Bedrock.

Ask "What time is it? Use current_time." Approve the pending tool call, then the reply continues with its result. Shell and file tools start in the process's temporary directory. Web fetch can make additional model calls to summarize retrieved content.

## History and Approvals

Keep Welt's `AGENT_MANAGES_HISTORY=false`: each conversation turn creates a fresh Agent and uses the Slack history Welt sends. Harness session persistence and long-term memory are disabled with `session=False` and `memory=False`. Skills and background tasks are also disabled in this example; configure those in `new_agent()` for your application.

The entrypoint retains interrupted Agents until their approval answers arrive. Pending approvals are held in process memory, so they are lost when the process exits or AgentCore Runtime recycles the session. Local mode shares one process across sessions; unanswered approvals remain there until it exits.

## Deploy

Create a Python runtime with [AgentCore CLI](https://github.com/aws/agentcore-cli):

```sh
agentcore create --name WeltHarnessExample
cd WeltHarnessExample
agentcore add runtime --name WeltHarnessExample --language python

curl -o app/WeltHarnessExample/main.py https://raw.githubusercontent.com/iwamot/welt-io-strands/main/examples/harness/main.py
uv init --bare --python 3.12 app/WeltHarnessExample
uv add --project app/WeltHarnessExample bedrock-agentcore aws-opentelemetry-distro strands-harness welt-io-strands

agentcore deploy
agentcore status --json
```

Set Welt's `AGENT_ARN` to the Runtime ARN reported by `agentcore status`. To invoke the runtime before connecting Slack, follow the [direct CLI invocation example](../agent/README.md#verify-the-deployment).

Remove the runtime when finished:

```sh
agentcore remove runtime --name WeltHarnessExample
agentcore deploy --yes
```

## Bring a CLI Prototype

Use the [Strands CLI](https://github.com/strands-agents/harness-sdk/tree/main/strands-cli) to configure and try a Harness, then use `/export` to produce a Python project. Transfer its model, instructions, tools, plugins, and MCP configuration into `new_agent()` here. Keep the fresh-Agent lifecycle and interrupt map so Welt can supply history and resume approvals.

The exported project defines an Agent; this file adds the AgentCore endpoint and Welt's request and response adapters.
