from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from typing import Any

from ode.config.timeouts import JIT_TOOL_TIMEOUT
from ode.llm import _ollama_generate

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = JIT_TOOL_TIMEOUT

_ALLOWED_BUILTINS = {
    "None": None,
    "True": True,
    "False": False,
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "range": range,
    "isinstance": isinstance,
    "hasattr": hasattr,
    "dict": dict,
    "list": list,
    "Exception": Exception,
    "print": print,
}

_REQUIRED_KEYS = ("entity", "metric", "value", "evidence_quality", "source_url", "timestamp")


def _extract_code(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    if text.startswith("```") and text.endswith("```"):
        return text.strip("`").strip()
    return text


def _code_is_safe(code: str) -> bool:
    """Reject any code that tries to import modules or call __import__."""
    return not bool(re.search(r"^\s*(?:import|from)\s", code, re.MULTILINE)) and "__import__" not in code


def _validate_signals(result: Any) -> bool:
    """Check that the result is a list of dicts with all required keys."""
    if not isinstance(result, list):
        return False
    return all(
        isinstance(item, dict) and all(k in item for k in _REQUIRED_KEYS)
        for item in result
    )


def _execute_code_with_timeout(code: str, timeout: float = _DEFAULT_TIMEOUT) -> tuple[dict[str, Any] | None, str]:
    """Execute code in a restricted sandbox with a thread-safe timeout."""
    ns: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS, "json": json, "urllib": urllib}

    def _run() -> dict[str, Any] | None:
        exec(code, ns)
        return ns

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run)
        try:
            result_ns = future.result(timeout=timeout)
            return result_ns, ""
        except TimeoutError:
            logger.warning("JIT tool sandbox execution timed out after %.1fs", timeout)
            return None, "execution timed out"
        except Exception as exc:
            return None, str(exc)


def synthesize_and_run_tool(query: str, target_topic: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start = time.time()
    error = ""
    success = False
    signals: list[dict[str, Any]] = []

    # URL-encode the query to prevent control character errors
    encoded_query = urllib.parse.quote(query)

    prompt = (
        f"Write a short Python function `fetch_signals(query, target_topic)` that fetches JSON "
        f"from a public REST API or RSS/Atom feed relevant to target_topic='{target_topic}' "
        f"and query='{encoded_query}'. The function returns a list of dicts. Each dict must have these keys: "
        "entity (string), metric (string 'jit_tool_result'), value (string), "
        "evidence_quality (integer 0-100), source_url (string). "
        "IMPORTANT: When constructing URLs, always use urllib.parse.urlencode() or urllib.parse.quote() "
        "to properly encode query parameters. Do NOT insert query strings directly into URLs. "
        "Do NOT use `import` or `from` statements; the `json` and `urllib` objects "
        "(including `urllib.request.urlopen`) are already in the function's scope. "
        "Return the code inside a ```python ... ``` block."
    )
    response = _ollama_generate(prompt, format=None)
    if not response:
        error = "no generation"
    else:
        code = _extract_code(response)
        if not _code_is_safe(code):
            error = "disallowed import in generated code"
            code = ""
        else:
            ns, exec_error = _execute_code_with_timeout(code, timeout=_DEFAULT_TIMEOUT)
            if exec_error:
                error = exec_error
            else:
                fetch = ns.get("fetch_signals") if ns else None
                if not callable(fetch):
                    error = "no fetch_signals function"
                else:
                    result = fetch(encoded_query, target_topic)
                    if not _validate_signals(result):
                        error = "invalid result"
                    else:
                        # Add timestamps to each signal for consistency with other MCP sources.
                        now = datetime.now(timezone.utc).isoformat()
                        for item in result:
                            if isinstance(item, dict):
                                item["timestamp"] = now
                        signals = result
                        success = True

    mcp_call = {
        "server": "jit_tool",
        "tool": "synthesize_and_run_tool",
        "success": success,
        "duration": time.time() - start,
        "error": error,
    }
    if not success:
        return [], mcp_call
    return signals, mcp_call
