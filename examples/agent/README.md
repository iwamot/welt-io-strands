# Example Agent

The example agent for [Welt](https://github.com/iwamot/welt)'s [Quick Start](https://github.com/iwamot/welt#quick-start): the smallest complete agent that exercises the wire in both directions through welt-io.

## Stack

| Package | Role |
|---------|------|
| [Bedrock AgentCore SDK](https://github.com/aws/bedrock-agentcore-sdk-python) | Serves the endpoint |
| [Strands Agents](https://github.com/strands-agents/sdk-python) | Runs the model and the tools |
| [Strands Agents Tools](https://github.com/strands-agents/tools) | Provides the `current_time` and `generate_image` tools |
| welt-io | Adapts the wire to Welt |

## Deploy

Deploy with the [AgentCore CLI](https://github.com/aws/agentcore-cli):

```sh
agentcore create --name WeltExample --framework Strands --model-provider Bedrock --memory none
cd WeltExample

curl -o app/WeltExample/main.py https://raw.githubusercontent.com/iwamot/welt-io/main/examples/agent/main.py
uv add --project app/WeltExample welt-io strands-agents-tools

agentcore deploy
```

The agent uses the Strands default model — currently an Anthropic Claude model — so enable access for it in the Amazon Bedrock console, in the region you deployed to. To try image generation too, also enable access for the Stability AI image models, in us-west-2 — the `generate_image` tool defaults to Stable Image Core but may pick another. Note the agent runtime ARN from the deploy output: Welt's `AGENT_ARN` points at it.

## Tools

- [`current_time`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/current_time.py) — the minimal tool: plain text streaming, nothing else. Ask "what time is it?" to see tool use in the thread.
- [`generate_image`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/generate_image.py) — returns the image as a tool-result block, which welt-io streams into the thread as a file upload by itself. Ask it to draw something.
- `sample_dangerous_action` — a pretend dangerous action (no side effects, no extra AWS permissions) that pauses for human approval: Welt renders the pause as **Approve** / **Cancel** buttons plus a free-text field in the Slack thread, and whichever answer comes first — a press, or a typed instruction — resumes the run. Ask "deploy to prod", then press a button or type something like "run the tests first". See [Welt's Interrupts doc](https://github.com/iwamot/welt/blob/main/docs/interrupts.md) for the round trip.

## Optional: file input

The agent can also read files uploaded to Slack — disabled by default. To try it, set in Welt's `.env`:

```sh
FILE_INPUT_MODALITIES=image,document
```

These two are what the default model (currently Anthropic Claude) accepts; `video` needs a model that takes Converse video input — see [supported foundation models](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html) and [Welt's Files doc](https://github.com/iwamot/welt/blob/main/docs/files.md).
