"""The studio's MCP endpoint, as seen by the Ask Claude run.

Claude Code connects to the studio's /mcp over HTTP (one bearer token per run) and speaks
MCP there. The studio checks each message, lets a policy look at (and pin, or refuse) each
tool call, forwards it to the running subject's REA engine and hands the reply back, so
Claude is really talking to REA — the same engine and the same opened target the Tools page
uses. Every tool call is recorded on the way through, so the panels can draw the latest
evidence without Claude having to repeat it.

What is forwarded: requests, and Claude's notifications/cancelled (its id translated to the
studio's, see app/rea.py). Other notifications are swallowed, and a JSON-RPC response from
Claude (an id with a result or error but no method) is ignored: the studio never asks it
anything. Malformed messages and batches get a -32600 error.
"""
import json

from .rea import classify, rpc_error

# JSON-RPC error returned when the run has no engine (the subject was closed mid-run).
NO_ENGINE = {"code": -32000, "message": "The analysis engine for this subject is not running."}


class Refused(Exception):
    """Raised by a policy: the studio will not run this tool call. The text is shown to Claude."""


def refused_result(message, code="refused_by_studio"):
    """A tools/call result in REA's error contract for a call the studio would not run."""
    error = {"code": code, "category": "permission", "message": message, "retryable": False}
    return {"content": [{"type": "text", "text": json.dumps({"error": error})}], "isError": True}


def _error(message_id, error):
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def handle(message, engine, record=None, timeout=None, policy=None, on_call=None):
    """One JSON-RPC message from Claude. Returns the reply dict, or None when there is nothing
    to answer (a notification, or a client's response: the caller answers 202).

    `policy(name, arguments)` sees each tool call first: it returns the arguments to send
    (None keeps them), or raises Refused with a plain reason, which Claude gets back as an
    error result. `record(name, arguments, result)` is called for each tool call REA answered,
    with the arguments actually sent. `on_call(name, arguments)` runs when a forwarded tool
    call starts (after any queue for the engine)."""
    kind, error = classify(message)
    if kind == "invalid":
        return error
    if kind == "response":
        return None
    message_id = message.get("id")
    if engine is None or not engine.alive():
        return None if kind == "notification" else _error(message_id, NO_ENGINE)
    if kind == "notification":
        try:
            engine.forward(message)
        except Exception:
            pass   # a cancel for a call that already ended, or an engine that just went away
        return None

    is_tool_call = message["method"] == "tools/call"
    name, arguments = "", {}
    if is_tool_call:
        params = message.get("params") or {}
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return rpc_error(message_id, -32602, "A tool call needs a tool name.")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return rpc_error(message_id, -32602, "Tool arguments must be an object.")
        if policy is not None:
            try:
                pinned = policy(name, dict(arguments))
            except Refused as exc:
                return {"jsonrpc": "2.0", "id": message_id, "result": refused_result(str(exc))}
            except Exception as exc:
                return {"jsonrpc": "2.0", "id": message_id,
                        "result": refused_result(f"The studio could not check this call: {exc}")}
            if pinned is not None:
                arguments = pinned
        message = dict(message, params=dict(params, arguments=arguments))

    try:
        reply = engine.forward(message, timeout=timeout, on_call=on_call)
    except Exception as exc:  # the engine died or timed out mid-call
        return _error(message_id, {"code": -32000, "message": str(exc)})

    if is_tool_call and isinstance(reply, dict) and "result" in reply and record is not None:
        try:
            record(name, arguments, reply.get("result"))
        except Exception:
            pass  # recording is for the panels only; never fail a tool call over it
    return reply
