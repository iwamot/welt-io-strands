"""A small AgentCore agent that Welt can drive.

Receives Welt's payload, feeds it to a Strands agent, and streams back the
renderable subset of its `stream_async` events — the AgentCore Runtime SDK
emits each one as SSE, which Welt (https://github.com/iwamot/welt) renders
into Slack. `start_reply` reads which envelope Welt sent (a conversation
turn, or the answers that resume an interrupted run), decodes it, and
streams the Agent this file hands it; `renderable_events` reduces what it
streams.

Which Agent that is, this file decides — including keeping an interrupted
run in S3 until its buttons are answered, below, so that the answers can
resume it wherever they arrive.

This example is a standalone deployable; Welt drives it only through the
JSON wire contract, which welt-io-strands adapts in both directions.
"""

import asyncio
import json
import os
from base64 import b64decode
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import uuid4

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, ToolContext, tool
from strands.middleware import (
    ExecuteToolContext,
    ExecuteToolStage,
    MiddlewareNext,
    ToolResultEvent,
    TypedEvent,
)
from strands.models import BedrockModel
from strands.types import Snapshot

from welt_io_strands import interrupt_reason, renderable_events, start_reply

app = BedrockAgentCoreApp()

# The model is the one place that decides which Bedrock endpoint, API, and
# region the agent talks to; nothing else in this file depends on that choice.
# BedrockModel speaks Converse to bedrock-runtime, so MODEL_ID takes any
# Converse model there. BEDROCK_REGION sends the model calls to a region of
# their own; unset, they go where the AWS SDK resolves one. An empty value
# means unset, like Welt's own variables.
_model_id = os.environ.get("MODEL_ID") or "global.anthropic.claude-sonnet-4-6"
_region = os.environ.get("BEDROCK_REGION") or None
model = BedrockModel(model_id=_model_id, region_name=_region)
# For bedrock-mantle, Bedrock's OpenAI-compatible endpoint, swap in the
# Responses API provider from `strands-agents[openai]` instead:
# from strands.models import OpenAIResponsesModel
# model = OpenAIResponsesModel(
#     model_id=_model_id,
#     bedrock_mantle_config={"region": _region} if _region else {},
# )

# The image model has a region of its own, whichever one the agent's model
# calls go to: access to it is enabled there.
_IMAGE_MODEL_ID = "stability.stable-image-core-v1:1"
_IMAGE_REGION = "us-west-2"


@tool
def current_time() -> str:
    """
    Get the current date and time.

    Returns:
        str: The current UTC time in ISO 8601 format.
    """
    return datetime.now(UTC).isoformat()


def _document_name(stem: str) -> str:
    """
    Name a document apart from every other document of the run.

    Converse rejects a request whose messages carry two documents under one
    name, and the tool that returns a document is the only one placed to
    keep it apart — it cannot know what the rest of the run named theirs,
    so it pays the going price of a random tail (strands-tools' file_read
    pays it too). The name is the model's handle on the document, and the
    filename Welt uploads it under.

    Args:
        stem (str): The readable part of the name.

    Returns:
        str: The stem, tailed apart from the run's other documents.
    """
    return f"{stem}-{uuid4().hex[:8]}"


@tool
def generate_image(prompt: str) -> dict:
    """
    Generate an image from a text prompt.

    Args:
        prompt (str): What the image should show.

    Returns:
        dict: The outcome, the image carried as an image block — which
            reaches the model, and the Slack thread because the entrypoint
            takes files from this tool.
    """
    client = boto3.client("bedrock-runtime", region_name=_IMAGE_REGION)
    response = client.invoke_model(
        modelId=_IMAGE_MODEL_ID,
        body=json.dumps({"prompt": prompt, "output_format": "jpeg"}),
    )
    image = json.loads(response["body"].read())["images"][0]
    return {
        "status": "success",
        "content": [
            {"text": "Generated the image."},
            {"image": {"format": "jpeg", "source": {"bytes": b64decode(image)}}},
        ],
    }


@tool
def create_sample_file() -> dict:
    """
    Create a small sample CSV file.

    Returns:
        dict: The outcome, the file carried as a document block — which
            reaches the model, and the Slack thread because the entrypoint
            takes files from this tool.
    """
    name = _document_name("sample")
    return {
        "status": "success",
        "content": [
            {"text": f"Created {name}.csv."},
            {
                "document": {
                    "format": "csv",
                    "name": name,
                    "source": {"bytes": b"fruit,count\napple,3\nbanana,5\n"},
                }
            },
        ],
    }


@tool(context=True)
def sample_dangerous_action(tool_context: ToolContext, action: str) -> str:
    """
    Pretend to run a dangerous or irreversible action the user asked for.

    A sample of the approval round trip: the interrupt below pauses the
    run until someone answers in the Slack thread — with the buttons, or
    by typing an answer into the text field. Nothing is actually
    executed.

    Args:
        tool_context (ToolContext): The Strands tool context.
        action (str): The action to pretend to run.

    Returns:
        str: The outcome of the action.
    """
    answer = tool_context.interrupt(
        "example-dangerous-action-approval",
        reason=interrupt_reason(
            f"May I run this dangerous action? — {action}",
            approve={},
            reject={"label": "Cancel"},
            input={"label": "Or type your answer"},
        ),
    )
    if answer is True:
        return f"Ran: {action}. Completed successfully (simulated by this demo tool)."
    if answer is False:
        return "The action was cancelled by the user."
    return f"The action was not run. The user answered: {answer}"


def _drafted_report(agent: Agent, tool_use_id: str, topic: str) -> str:
    """
    Draft the report body once per tool call.

    Memoized rather than plain: Strands re-executes an interrupted tool
    from its start on resume, and drafting is the kind of work that must
    not run twice — a redraft (timestamped here to make that visible)
    would silently publish something other than what the human approved.
    The tool use id is the same on both passes, and the drafts live in the
    agent's state, which is held and restored with the interrupt state it
    pairs with.

    Args:
        agent (Agent): The agent running the tool.
        tool_use_id (str): The id of the tool call being drafted for.
        topic (str): The report topic.

    Returns:
        str: The draft report body.
    """
    drafts = agent.state.get("drafts") or {}
    if tool_use_id not in drafts:
        drafted_at = datetime.now(UTC).isoformat()
        drafts[tool_use_id] = (
            f"# {topic}\n\nEverything about {topic} is going well.\n\n"
            f"_Drafted at {drafted_at}._\n"
        )
        agent.state.set("drafts", drafts)
    return drafts[tool_use_id]


@tool(context=True)
def sample_draft_report(tool_context: ToolContext, topic: str) -> str | dict:
    """
    Draft a small report on a topic and ask whether to publish it.

    A sample of work before an interrupt: the draft is written first,
    then the run pauses to show it for the publish decision. Approval
    returns the approved draft as a markdown file.

    Args:
        tool_context (ToolContext): The Strands tool context.
        topic (str): The report topic.

    Returns:
        str | dict: The outcome, the approved draft carried as a document
            block.
    """
    tool_use_id = tool_context.tool_use["toolUseId"]
    draft = _drafted_report(tool_context.agent, tool_use_id, topic)
    answer = tool_context.interrupt(
        "example-draft-report-approval",
        reason=interrupt_reason(
            f"May I publish this draft?\n\n```\n{draft}```",
            [
                {"value": "Publish", "style": "primary"},
                {"value": "Discard"},
            ],
            input={"label": "Or type your answer"},
        ),
    )
    drafts = tool_context.agent.state.get("drafts") or {}
    del drafts[tool_use_id]
    tool_context.agent.state.set("drafts", drafts)
    if answer == "Publish":
        name = _document_name("report")
        return {
            "status": "success",
            "content": [
                {
                    "text": "The user answered the publish question in the"
                    " thread by pressing Publish, so this draft is already"
                    f" published there as {name}.md. The publish flow is"
                    " complete; nothing is left to approve."
                },
                {
                    "document": {
                        "format": "md",
                        "name": name,
                        "source": {"bytes": draft.encode()},
                    }
                },
            ],
        }
    if answer == "Discard":
        return "The user discarded the draft; nothing was published."
    return f"The draft was not published. The user answered: {answer}"


async def approve_image_generation(
    context: ExecuteToolContext,
    next_fn: MiddlewareNext[ExecuteToolContext, TypedEvent],
) -> AsyncGenerator[TypedEvent]:
    """Ask before the image tool runs, from outside the tool.

    The tool itself knows nothing of approval; middleware gives it the same
    structured interrupt reason as the tools above without changing it.
    Only the approve button authorizes generation. Other answers return
    to the model as a tool error, with a typed answer preserved verbatim.

    Args:
        context (ExecuteToolContext): The tool call being considered.
        next_fn (MiddlewareNext): The next handler, which executes the tool.

    Yields:
        TypedEvent: The tool's events, or the result declining execution.
    """
    if context.tool_use["name"] == "generate_image":
        prompt = context.tool_use["input"].get("prompt", "")
        answer = context.interrupt(
            "example-image-generation-approval",
            reason=interrupt_reason(
                f"May I generate this image? — {prompt}",
                approve={"label": "Generate"},
                reject={"label": "Cancel"},
                input={"label": "Or type your answer"},
            ),
        ).response
        if answer is not True:
            text = (
                "Image generation was cancelled by the user."
                if answer is False
                else f"The image was not generated. The user answered: {answer}"
            )
            yield ToolResultEvent(
                {
                    "toolUseId": context.tool_use["toolUseId"],
                    "status": "error",
                    "content": [{"text": text}],
                }
            )
            return
    async for event in next_fn(context):
        yield event


# The tools whose files belong in the Slack thread. A tool left out keeps
# its files to the model — this agent has none, but an agent that reads
# documents for the model would.
_FILES_FROM = {"generate_image", "create_sample_file", "sample_draft_report"}


# The runs that stopped for human input are kept in this bucket, one object
# per interrupt they raised, under `interrupted/<interrupt id>.json`. Welt
# sends those ids back when the buttons are answered, and the answers may
# arrive at another process than the one that stopped: AgentCore Runtime
# recycles a session's microVM, and Lambda keeps no request on the
# environment of the one before it. A lifecycle rule on the prefix clears
# the runs nobody answered. The calls are boto3's, which block, so they run
# on a thread: one process serves several requests on its event loop.
_bucket = os.environ["SESSION_BUCKET"]
_s3 = boto3.client("s3")


def _held_key(interrupt_id: str) -> str:
    return f"interrupted/{interrupt_id}.json"


def _hold(agent: Agent, interrupt_ids: list[str]) -> None:
    """
    Keep a stopped run until its interrupts are answered.

    The snapshot carries the run's messages, its state and where it
    stopped; the tools and the model are this file's, and are built again
    on resume. Every id of the stop gets the same snapshot, since the
    answers name any of them.

    Args:
        agent (Agent): The run that stopped.
        interrupt_ids (list[str]): The ids of the interrupts it raised.
    """
    snapshot = agent.take_snapshot(
        preset="session", app_data={"interrupt_ids": interrupt_ids}
    )
    body = json.dumps(snapshot.to_dict())
    for interrupt_id in interrupt_ids:
        _s3.put_object(Bucket=_bucket, Key=_held_key(interrupt_id), Body=body)


def _resumed(answers: Mapping[str, object]) -> Agent:
    """
    Take the held run the answered interrupts belong to.

    A stop's questions are answered together, so every id in one payload
    names the same run, and the whole stop is dropped here. An answered id
    no longer held means the run was answered already or cleared, and
    there is nothing to resume.

    Args:
        answers (Mapping[str, object]): Welt's `interrupt_responses`,
            keyed by interrupt id.

    Returns:
        Agent: The run the answers name.

    Raises:
        RuntimeError: If no answered id is held.
    """
    for interrupt_id in answers:
        try:
            held = _s3.get_object(Bucket=_bucket, Key=_held_key(interrupt_id))
        except _s3.exceptions.NoSuchKey:
            continue
        snapshot = Snapshot.from_dict(json.loads(held["Body"].read()))
        agent = new_agent()
        agent.load_snapshot(snapshot)
        for stopped_id in snapshot.app_data["interrupt_ids"]:
            _s3.delete_object(Bucket=_bucket, Key=_held_key(stopped_id))
        return agent
    raise RuntimeError("No interrupted agent to resume in this session.")


def new_agent() -> Agent:
    """
    Build the Agent of one conversation turn.

    Returns:
        Agent: A fresh Agent — a conversation turn runs on the messages
            Welt sends and nothing else.
    """
    agent = Agent(
        model=model,
        tools=[
            current_time,
            generate_image,
            create_sample_file,
            sample_dangerous_action,
            sample_draft_report,
        ],
        callback_handler=None,
    )
    agent.add_middleware(ExecuteToolStage, approve_image_generation)
    return agent


@app.entrypoint
async def invoke(payload: dict) -> AsyncIterator[dict]:
    """
    Stream a reply to the conversation or approval answers Welt sent.

    Args:
        payload (dict): Welt's invocation payload.

    Yields:
        dict: The events Welt renders.

    Raises:
        RuntimeError: If the answers name no interrupted run still held.
    """
    if "interrupt_responses" in payload:
        agent = await asyncio.to_thread(_resumed, payload["interrupt_responses"])
    else:
        agent = new_agent()

    stream = start_reply(agent, payload)
    interrupt_ids: list[str] = []
    async for event in renderable_events(stream, agent=agent, files_from=_FILES_FROM):
        interrupt = event.get("interrupt")
        if interrupt is not None:
            interrupt_ids.append(interrupt["id"])
        yield event
    if interrupt_ids:
        await asyncio.to_thread(_hold, agent, interrupt_ids)


if __name__ == "__main__":
    app.run()
