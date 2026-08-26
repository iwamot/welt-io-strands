# Example Agent

The example agent for [Welt](https://github.com/iwamot/welt)'s [Quick Start](https://github.com/iwamot/welt#quick-start): the smallest complete agent that exercises the wire in both directions through welt-io-strands.

## Stack

| Package | Role |
|---------|------|
| [Bedrock AgentCore SDK](https://github.com/aws/bedrock-agentcore-sdk-python) | Serves the endpoint |
| [Strands Agents SDK](https://strandsagents.com/) | Runs the model and the tools |
| [Strands Agents Tools](https://github.com/strands-agents/tools) | Provides the `generate_image` tool |
| welt-io-strands | Adapts the wire to Welt |

## Run Locally

The agent runs on your machine as-is — [Welt's Quick Start](https://github.com/iwamot/welt#quick-start) starts here, before anything is deployed: the AgentCore SDK serves the same HTTP surface locally, on port 8080, that AgentCore Runtime serves in the cloud, and Welt's local mode invokes it there.

Fetch the agent and run it with [uv](https://docs.astral.sh/uv/):

```sh
curl -O https://raw.githubusercontent.com/iwamot/welt-io-strands/main/examples/agent/main.py
MODEL_ID=global.anthropic.claude-sonnet-4-6 \
  uv run --with bedrock-agentcore --with strands-agents-tools --with welt-io-strands \
  --with "botocore[crt]" main.py
```

The process needs AWS credentials the standard SDK way — environment variables, `AWS_PROFILE`, an SSO session, `aws login` (which is why `botocore[crt]` is included) — because the model still runs on Amazon Bedrock.

`MODEL_ID` takes any Converse model with access enabled in the Amazon Bedrock console, in the region your credentials point at; unset, the agent uses `global.anthropic.claude-sonnet-4-6`. The model is built in one place near the top of `main.py`: `BedrockModel`, which speaks Converse to bedrock-runtime. Two commented-out lines next to it swap in `OpenAIResponsesModel`, which speaks the Responses API to bedrock-mantle, Bedrock's OpenAI-compatible endpoint, instead.

To try image generation too, also enable access for the Stability AI image models, in us-west-2 — the `generate_image` tool defaults to Stable Image Core but may pick another.

One difference from the cloud: AgentCore Runtime gives every session its own microVM, while the local server is a single process for all sessions — the interrupted runs the agent keeps all share that one process, outlive the session that raised them, and accumulate while unanswered until the process exits.

## Deploy

Deploy with the [AgentCore CLI](https://github.com/aws/agentcore-cli):

```sh
agentcore create --name WeltExample --framework Strands --model-provider Bedrock --memory none
cd WeltExample

curl -o app/WeltExample/main.py https://raw.githubusercontent.com/iwamot/welt-io-strands/main/examples/agent/main.py

# the template's requires-python floor sits below welt-io-strands'
sed -i.bak 's/requires-python = ">=3.10"/requires-python = ">=3.12"/' app/WeltExample/pyproject.toml && rm app/WeltExample/pyproject.toml.bak
uv add --project app/WeltExample welt-io-strands strands-agents-tools

agentcore deploy
```

The agent uses `global.anthropic.claude-sonnet-4-6`, so enable access for it in the Amazon Bedrock console, in the region you deployed to, or point the `MODEL_ID` environment variable at another Converse model. To try image generation too, also enable access for the Stability AI image models, in us-west-2 — the `generate_image` tool defaults to Stable Image Core but may pick another. Note the agent runtime ARN from the deploy output: Welt's `AGENT_ARN` points at it.

## Tools

- `current_time` — the minimal tool: plain text streaming, nothing else. Ask "what time is it?" to see tool use in the thread.
- [`generate_image`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/generate_image.py) — returns the image as a tool-result block, which the model sees and welt-io-strands turns into a file upload. Ask it to draw something.
- `create_sample_file` — writes a small CSV and returns it as a document block, which the model reads and Welt uploads to the thread. Its name carries a random tail (`sample-3f2a1b9c.csv`) because a document's name has to be unique across the run. Ask it for a sample file.
- `sample_dangerous_action` — a pretend dangerous action (no side effects, no extra AWS permissions) that pauses for human approval: Welt renders the pause as **Approve** / **Cancel** buttons plus a free-text field in the Slack thread, and whichever answer comes first — a press, or typed text — resumes the run. Ask "deploy to prod", then press a button or type something like "not now". See [Welt's Interrupts doc](https://github.com/iwamot/welt/blob/main/docs/interrupts.md) for the round trip.
- `sample_draft_report` — drafts a small report, pauses to show it for approval, and on approval returns it as a markdown file (`report-8f3a2c1d.md`, tailed for the same reason). Drafting before the pause is the Strands interrupt pitfall: an interrupted tool re-executes from its start on resume, so the drafting is memoized on the tool use id and the published file stays identical to the approved draft. The draft is timestamped, so a silent redraft would show. Ask "draft a report about apples", then answer the buttons.

The three that produce files are named in the entrypoint's `files_from` — that is what puts their files in the thread, and a tool left out of it would hand its files to the model alone.

## Optional: file input

The agent can also read files uploaded to Slack — disabled by default. To try it, set in Welt's `.env`:

```sh
FILE_INPUT_MODALITIES=image,document
```

These two are what the agent's model, `global.anthropic.claude-sonnet-4-6`, accepts; `video` needs a model that takes Converse video input — see [supported foundation models](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html) and [Welt's Files doc](https://github.com/iwamot/welt/blob/main/docs/files.md).
