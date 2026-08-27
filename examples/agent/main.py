"""A small AgentCore agent that Welt can drive.

Receives Welt's payload, feeds it to a Strands agent, and streams back the
renderable subset of its `stream_async` events — the AgentCore Runtime SDK
emits each one as SSE, which Welt (https://github.com/iwamot/welt) renders
into Slack. `start_reply` reads which envelope Welt sent (a conversation
turn, or the answers that resume an interrupted run), decodes it, and
streams the Agent this file hands it; `renderable_events` reduces what it
streams.

Which Agent that is, this file decides — including keeping an interrupted
run until its buttons are answered, below.

This example is a standalone deployable; Welt drives it only through the
JSON wire contract, which welt-io-strands adapts in both directions.
"""

import os
import tempfile
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, ToolContext, tool
from strands.models import BedrockModel
from strands.types.tools import ToolUse
from strands.vended_plugins.steering import (
    Interrupt,
    Proceed,
    SteeringHandler,
    ToolSteeringAction,
)
from strands_tools import generate_image

from welt_io_strands import interrupt_reason, renderable_events, start_reply

# generate_image saves each image under ./output as a side effect, and the
# temp dir is the writable path in the AgentCore Runtime container.
os.chdir(tempfile.gettempdir())

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


# Draft bodies by tool use id, dropped as soon as their tool call is answered.
_drafts: dict[str, str] = {}


def _drafted_report(tool_use_id: str, topic: str) -> str:
    """
    Draft the report body once per tool call.

    Memoized rather than plain: Strands re-executes an interrupted tool
    from its start on resume, and drafting is the kind of work that must
    not run twice — a redraft (timestamped here to make that visible)
    would silently publish something other than what the human approved.
    The tool use id is the same on both passes, and the cache outlives
    neither more nor less than the interrupt state it pairs with: both
    live in this process.

    Args:
        tool_use_id (str): The id of the tool call being drafted for.
        topic (str): The report topic.

    Returns:
        str: The draft report body.
    """
    if tool_use_id not in _drafts:
        drafted_at = datetime.now(UTC).isoformat()
        _drafts[tool_use_id] = (
            f"# {topic}\n\nEverything about {topic} is going well.\n\n"
            f"_Drafted at {drafted_at}._\n"
        )
    return _drafts[tool_use_id]


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
    draft = _drafted_report(tool_use_id, topic)
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
    del _drafts[tool_use_id]
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


class ApprovalSteering(SteeringHandler):
    """Ask before the image tool runs, from outside the tool.

    A sample of the other way to raise an interrupt. The tools above call
    `interrupt()` themselves, which a tool nobody here wrote — `generate_image`
    comes from strands-tools — cannot be made to do; a steering handler gates
    it from the outside instead, one place to decide for every tool the agent
    is given.

    The reason is a plain message: a handler cannot declare buttons, so Welt
    answers it with the default buttons, and the plugin reads
    the answer (a boolean) rather than this agent. What the human needs in
    order to decide has to travel in the message, which is why the prompt
    goes into it.
    """

    async def steer_before_tool(
        self, *, agent: Agent, tool_use: ToolUse, **kwargs: object
    ) -> ToolSteeringAction:
        """
        Decide whether one tool call needs a human first.

        Args:
            agent (Agent): The agent about to run the tool.
            tool_use (ToolUse): The tool call, its name and input.
            **kwargs (object): Whatever else the plugin passes.

        Returns:
            ToolSteeringAction: Interrupt for the image tool, Proceed
                otherwise.
        """
        if tool_use["name"] != "generate_image":
            return Proceed(reason="Only image generation is gated here.")
        # The gate is only as good as this method's ability to return: the
        # plugin logs whatever this raises and lets the tool run, so the
        # input is read for what it might be rather than what it should be.
        tool_input = tool_use["input"]
        prompt = tool_input.get("prompt", "") if isinstance(tool_input, dict) else ""
        return Interrupt(reason=f"May I generate this image? — {prompt}")


# The tools whose files belong in the Slack thread. A tool left out keeps
# its files to the model — this agent has none, but an agent that reads
# documents for the model would.
_FILES_FROM = {"generate_image", "create_sample_file", "sample_draft_report"}


# The Agents whose runs stopped for human input, under the ids of the
# interrupts they raised. Welt sends those ids back when the buttons are
# answered, and the run they belong to is the one that can be resumed. An
# entry lives as long as this process: AgentCore Runtime gives each session
# its own microVM, so a resume that arrives after it was recycled finds
# nothing and raises, which Welt renders as its resume-failure notice.
_interrupted: dict[str, Agent] = {}


def _resumed(answers: Mapping[str, object]) -> Agent:
    """
    Take the held run the answered interrupts belong to.

    A stop's questions are answered together, so every id in one payload
    names the same run — and the whole stop leaves the map with that run,
    so every id holding it is dropped here. An answered id no longer held
    means this process lost the run, and there is nothing to resume.

    Args:
        answers (Mapping[str, object]): Welt's `interrupt_responses`,
            keyed by interrupt id.

    Returns:
        Agent: The run the answers name.

    Raises:
        RuntimeError: If no answered id is held.
    """
    held_id = next((i for i in answers if i in _interrupted), None)
    if held_id is None:
        raise RuntimeError("No interrupted agent to resume in this session.")
    agent = _interrupted[held_id]
    for interrupt_id in [i for i, held in _interrupted.items() if held is agent]:
        del _interrupted[interrupt_id]
    return agent


def new_agent() -> Agent:
    """
    Build the Agent of one conversation turn.

    Returns:
        Agent: A fresh Agent — a conversation turn runs on the messages
            Welt sends and nothing else.
    """
    return Agent(
        model=model,
        tools=[
            current_time,
            generate_image,
            create_sample_file,
            sample_dangerous_action,
            sample_draft_report,
        ],
        plugins=[ApprovalSteering()],
        callback_handler=None,
    )


@app.entrypoint
async def invoke(payload: dict) -> AsyncIterator[dict]:
    """
    Stream a reply to the conversation or approval answers Welt sent.

    Args:
        payload (dict): Welt's invocation payload.

    Yields:
        dict: The events Welt renders.

    Raises:
        RuntimeError: If the answers name no interrupted run this process
            still holds.
    """
    if "interrupt_responses" in payload:
        agent = _resumed(payload["interrupt_responses"])
    else:
        agent = new_agent()

    stream = start_reply(agent, payload)
    async for event in renderable_events(stream, agent=agent, files_from=_FILES_FROM):
        interrupt = event.get("interrupt")
        if interrupt is not None:
            _interrupted[interrupt["id"]] = agent
        yield event


if __name__ == "__main__":
    app.run()
