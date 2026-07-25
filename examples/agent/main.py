"""A small AgentCore agent that Welt can drive.

Receives Welt's payload, feeds it to a Strands agent, and yields the
renderable subset of its `stream_async` events — the AgentCore Runtime SDK
emits each one as SSE, which Welt (https://github.com/iwamot/welt) renders
into Slack. The payload carries one of two envelopes: Converse-shaped
`messages` for a conversation turn, or `interrupt_responses` when a human
answered the approval buttons of an interrupted run.

This example is a standalone deployable; Welt drives it only through the
JSON wire contract, which welt-io-strands adapts in both directions.
"""

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, ToolContext, tool
from strands_tools import current_time, generate_image

from welt_io_strands import (
    decode_interrupt_responses,
    decode_messages,
    interrupt_reason,
    renderable_events,
)

# generate_image saves each image under ./output as a side effect, and the
# temp dir is the writable path in the AgentCore Runtime container.
os.chdir(tempfile.gettempdir())

app = BedrockAgentCoreApp()

# Where an interrupted Agent waits for its answers. One slot is enough:
# AgentCore Runtime runs each session in its own microVM, so this process
# never serves two sessions. Resume only: a normal turn always builds a
# fresh Agent from the messages Welt sends (the Slack thread is the source
# of truth for conversation history, so the slot must not stand in for
# it). No persistence either — the slot lives and dies with the session's
# microVM (recycled on idle timeout, 8 hours at most).
_interrupted_agent: Agent | None = None


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
    by typing an instruction into the text field. Nothing is actually
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
            [
                {"value": "y", "label": "Approve", "style": "primary"},
                {"value": "n", "label": "Cancel"},
            ],
            input={"label": "Or tell me what to do instead"},
        ),
    )
    if answer == "y":
        return f"Ran: {action}. (This example doesn't actually run anything.)"
    if answer == "n":
        return "The action was cancelled by the user."
    return f"The action was not run. The user said instead: {answer}"


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
        drafted_at = datetime.now(timezone.utc).isoformat()
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
                {"value": "y", "label": "Publish", "style": "primary"},
                {"value": "n", "label": "Discard"},
            ],
            input={"label": "Or tell me what to fix"},
        ),
    )
    del _drafts[tool_use_id]
    if answer == "y":
        name = _document_name("report")
        return {
            "status": "success",
            "content": [
                {
                    "text": "The draft was approved and is already published"
                    f" to the thread as {name}.md. The publish flow is"
                    " complete; no further review or approval is needed."
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
    if answer == "n":
        return "The user discarded the draft; nothing was published."
    return f"The draft was not published. The user said instead: {answer}"


# The tools whose files belong in the Slack thread. A tool left out keeps
# its files to the model — this agent has none, but an agent that reads
# documents for the model would.
_FILES_FROM = {"generate_image", "create_sample_file", "sample_draft_report"}


@app.entrypoint
async def invoke(payload: dict) -> AsyncIterator[dict]:
    """
    Stream a reply to the conversation or approval answers Welt sent.

    Args:
        payload (dict): The invocation payload: Converse-shaped `messages`
            built by Welt from the Slack thread (file blocks
            base64-encoded), or `interrupt_responses` carrying the button
            answers that resume an interrupted run.

    Yields:
        dict: The renderable subset of Strands `stream_async` events.
    """
    global _interrupted_agent

    if "interrupt_responses" in payload:
        agent = _interrupted_agent
        _interrupted_agent = None
        if agent is None:  # The microVM was recycled while the buttons waited.
            # The SDK reports the raise as an `error` event, and Welt renders
            # its resume-failure notice.
            raise RuntimeError("No interrupted agent to resume in this session.")
        stream = agent.stream_async(
            decode_interrupt_responses(payload["interrupt_responses"])
        )
    else:
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            yield {
                "data": "I received an empty conversation, "
                "so there is nothing to reply to."
            }
            return
        messages = decode_messages(messages)  # base64 file bytes -> raw bytes
        agent = Agent(
            # Any Converse model; unset falls back to the Strands default.
            model=os.environ.get("MODEL_ID"),
            tools=[
                current_time,
                generate_image,
                create_sample_file,
                sample_dangerous_action,
                sample_draft_report,
            ],
            callback_handler=None,
        )
        stream = agent.stream_async(messages)

    interrupted = False
    # Reduce the stream to the JSON-serializable events Welt renders
    async for event in renderable_events(stream, agent=agent, files_from=_FILES_FROM):
        if "interrupt" in event:
            interrupted = True
        yield event

    if interrupted:
        # Re-stashed on every interrupted stop, so a resume that interrupts
        # again keeps working.
        _interrupted_agent = agent


if __name__ == "__main__":
    app.run()
