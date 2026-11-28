# Example Agent

The example agent for [Welt](https://github.com/iwamot/welt)'s [Quick Start](https://github.com/iwamot/welt#quick-start): the smallest complete agent that exercises the wire in both directions through welt-io-strands.

## Stack

| Package | Role |
|---------|------|
| [Bedrock AgentCore SDK](https://github.com/aws/bedrock-agentcore-sdk-python) | Serves the endpoint |
| [Strands Agents SDK](https://strandsagents.com/) | Runs the model and the tools |
| welt-io-strands | Adapts the wire to Welt |

## Run Locally

The agent runs on your machine as-is — [Welt's Quick Start](https://github.com/iwamot/welt#quick-start) starts here, before anything is deployed: the AgentCore SDK serves the same HTTP surface locally, on port 8080, that AgentCore Runtime serves in the cloud, and Welt's local mode invokes it there.

The agent keeps a run that stops for approval in an S3 bucket until its buttons are answered, so that the answer can resume it in whichever process receives it. [Create the session bucket](#create-the-session-bucket) first, then fetch the agent and run it with [uv](https://docs.astral.sh/uv/), naming the bucket in `SESSION_BUCKET`:

```sh
curl -O https://raw.githubusercontent.com/iwamot/welt-io-strands/main/examples/agent/main.py
SESSION_BUCKET='<session-bucket>' uv run --with bedrock-agentcore \
  --with welt-io-strands --with "botocore[crt]" main.py
```

The process needs AWS credentials the standard SDK way — environment variables, `AWS_PROFILE`, an SSO session, `aws login` (which is why `botocore[crt]` is included) — because the model still runs on Amazon Bedrock, and the bucket is in S3.

`MODEL_ID` takes any Converse model with access enabled in the Amazon Bedrock console; unset, the agent uses `global.anthropic.claude-sonnet-4-6`. The model is built in one place near the top of `main.py`: `BedrockModel`, which speaks Converse to bedrock-runtime. Two commented-out lines next to it swap in `OpenAIResponsesModel`, which speaks the Responses API to bedrock-mantle, Bedrock's OpenAI-compatible endpoint, instead.

`BEDROCK_REGION` sends the model calls to a region of their own — useful locally, when the model access you want is not where your credentials point. Unset, `BedrockModel` resolves one the AWS SDK way: `AWS_DEFAULT_REGION`, then the profile's own `region`, then `AWS_REGION`, then `us-west-2` when nothing names one.

To try image generation too, also enable access for Stable Image Core, in us-west-2, which is what the `generate_image` tool calls.

## Create the Session Bucket

Create a bucket for the runs that wait for approval, and let S3 remove the ones nobody answers after a week:

```sh
aws s3 mb 's3://<session-bucket>' --region '<region>'
aws s3api put-bucket-lifecycle-configuration --bucket '<session-bucket>' \
  --lifecycle-configuration '{"Rules":[{"ID":"expire-interrupted","Filter":{"Prefix":"interrupted/"},"Status":"Enabled","Expiration":{"Days":7}}]}'
```

The agent reads and writes objects under `interrupted/` only. It may also look for a run that is no longer there, after a second press or a week's silence, and S3 reports that as missing rather than forbidden only to a role that may list the bucket. Save this policy as `session-policy.json`; the deploy steps below attach it to the agent's role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<session-bucket>/interrupted/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<session-bucket>"
    }
  ]
}
```

## Deploy to AgentCore Runtime

Deploy with the [AgentCore CLI](https://github.com/aws/agentcore-cli):

```sh
agentcore create --name WeltExample
cd WeltExample
agentcore add runtime --name WeltExample --language python \
  --environment-variables '{"SESSION_BUCKET":"<session-bucket>"}'

curl -o app/WeltExample/main.py https://raw.githubusercontent.com/iwamot/welt-io-strands/main/examples/agent/main.py

# welt-io-strands' floor; left out, uv takes the local Python's, which may be newer than AgentCore runs
uv init --bare --python 3.12 app/WeltExample
uv add --project app/WeltExample bedrock-agentcore aws-opentelemetry-distro welt-io-strands

agentcore deploy
```

Then let the runtime's role use the bucket. `agentcore status --json` reports the agent runtime ARN; use the ID after `runtime/` in it as `<runtime-id>`, and its region as `<runtime-region>`:

```sh
role=$(aws bedrock-agentcore-control get-agent-runtime --region '<runtime-region>' \
  --agent-runtime-id '<runtime-id>' --query roleArn --output text)
aws iam put-role-policy --role-name "${role##*/}" \
  --policy-name welt-session-bucket --policy-document file://session-policy.json
```

The agent uses `global.anthropic.claude-sonnet-4-6`, so enable access for it in the Amazon Bedrock console, in the region you deployed to, or point the `MODEL_ID` environment variable at another Converse model — and `BEDROCK_REGION` at another region, to leave the model access where it already is. Both go in the `--environment-variables` JSON object on `agentcore add runtime`, next to `SESSION_BUCKET`. To try image generation too, also enable access for Stable Image Core, in us-west-2, which is what the `generate_image` tool calls. Welt's `AGENT_ARN` points at the agent runtime ARN.

### Verify the Deployment

Display the deployed Runtime ARN:

```sh
agentcore status --json
```

Use the ID after `runtime/` in that ARN as `<runtime-id>`, and its region as `<runtime-region>`. Invoke the agent directly with AgentCore CLI v1:

```sh
agentcore runtime invoke \
  --region '<runtime-region>' \
  --id '<runtime-id>' \
  --session-id manual-check-0000000000000000000000 \
  --content-type application/json \
  --accept text/event-stream \
  --payload '{"messages":[{"role":"user","content":[{"text":"Say hello in one sentence."}]}]}' \
  --json
```

This sends the same `messages` envelope Welt uses, exercising the deployed agent and adapter before connecting Slack. Look for the reply text in the response body's SSE `data:` lines, where each JSON event's `data` field carries a text chunk. The invoke command also works outside the AgentCore project directory.

### Remove the Deployment

Take the bucket policy off the runtime's role, then take the runtime out of the project and deploy the empty project, which deletes the CloudFormation stack, `AgentCore-WeltExample-default`:

```sh
aws iam delete-role-policy --role-name "${role##*/}" --policy-name welt-session-bucket
agentcore remove runtime --name WeltExample
agentcore deploy --yes
```

## Deploy to AWS Lambda Web Functions

The same `main.py` also runs on [Lambda Web Functions](https://docs.aws.amazon.com/lambda/latest/dg/web-functions.html), which load the ASGI app named `app` in `main.py`. Python on Web Functions is `python3.15`, which is in public preview there: not for production workloads. Web Functions run on arm64, and `aws lambda-web deploy` needs AWS CLI 2.42 or later.

Collect the agent and its dependencies, built for that platform, in one directory:

```sh
mkdir build
curl -o build/main.py https://raw.githubusercontent.com/iwamot/welt-io-strands/main/examples/agent/main.py
uv pip install --target build --python-platform aarch64-manylinux_2_34 \
  --python-version 3.15 --only-binary :all: bedrock-agentcore welt-io-strands
```

Create the function's role, allowed to write logs, call the model, and use the session bucket:

```sh
aws iam create-role --role-name welt-example-web --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name welt-example-web \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name welt-example-web --policy-name welt-session-bucket \
  --policy-document file://session-policy.json
aws iam put-role-policy --role-name welt-example-web --policy-name bedrock-invoke \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"}]}'
```

Deploy it behind an IAM-authenticated endpoint:

```sh
aws lambda-web deploy --name welt-example --code build --runtime python \
  --execution-role-arn "arn:aws:iam::<account-id>:role/welt-example-web" \
  --env SESSION_BUCKET='<session-bucket>' --timeout-seconds 900 \
  --auth-type IamAuth --force-include certifi/cacert.pem \
  --force-include botocore/cacert.pem --region '<region>' --create
```

- `--force-include` keeps the CA certificate bundles that `deploy` otherwise leaves out with every `*.pem`; without them the agent cannot reach Amazon Bedrock over TLS.
- `--timeout-seconds 900` lets a reply stream for up to 15 minutes, the most a Web Function request takes; the default is 30 seconds, and a reply that outlasts it is cut off.

The command prints the endpoint URL. Point Welt at it with `AGENT_URL`, and have Welt sign its requests for Lambda:

```sh
AGENT_URL=https://<endpoint-id>.<shard>.lambda-web.<region>.on.aws
AGENT_SIGV4_SERVICE=lambda
AGENT_SIGV4_REGION=<region>
```

Welt's AWS identity needs `lambda:InvokeWebFunctionEndpoint` on `arn:aws:lambda:<region>:<account-id>:web-function/welt-example/endpoint/*`.

### Remove the Web Function

```sh
aws lambda-web delete-web-function --function-name welt-example --region '<region>'
aws iam delete-role-policy --role-name welt-example-web --policy-name welt-session-bucket
aws iam delete-role-policy --role-name welt-example-web --policy-name bedrock-invoke
aws iam detach-role-policy --role-name welt-example-web \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name welt-example-web
```

## Tools

- `current_time` — the minimal tool: plain text streaming, nothing else. Ask "what time is it?" to see tool use in the thread.
- `generate_image` — calls Stable Image Core through bedrock-runtime and returns the image as a tool-result block, which the model sees and welt-io-strands turns into a file upload. Tool-execution middleware asks for approval with **Generate** / **Cancel** buttons and a free-text field before the image tool runs. Ask it to draw something.
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
