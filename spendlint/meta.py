"""Rule metadata. Each rule comes from a study of real cost-defect fixes in open-source LLM agent
projects; `evidence` is how many reviewed real-world fixes back the rule."""

RULES = {
    "R-ACCT-001": dict(
        name="Cost computed from token counts that ignore cached-token fields",
        category="token_accounting_bug",
        severity="warning",
        fp_risk="medium",
        evidence=38,
        action="This code prices `input_tokens`/`prompt_tokens` but never reads the provider's cache fields "
        "(`cache_read_input_tokens`, `cache_creation_input_tokens`, `prompt_tokens_details.cached_tokens`). "
        "Cached reads are billed at a fraction of the input rate and cache writes at a premium, so reported "
        "cost and any budget check built on it will be wrong.",
    ),
    "R-CACHE-001": dict(
        name="Anthropic requests with no prompt caching anywhere in the repo",
        category="missing_cache",
        severity="warning",
        fp_risk="medium",
        evidence=11,
        action="This repo calls the Anthropic Messages API but never sets `cache_control`. Mark the end of the stable "
        "prefix (system prompt, tool definitions, long static context) with "
        '`{"type": "ephemeral"}` so repeat calls read it at ~10% of the input price.',
    ),
    "R-CACHE-003": dict(
        name="Volatile value (timestamp, uuid, cwd) inside the system prompt",
        category="missing_cache",
        severity="warning",
        fp_risk="low",
        evidence=13,
        action="A value that changes on every call (current time, uuid, working directory, random) is interpolated "
        "into the system prompt. Any prefix cache keyed on the prompt will miss every time. Move the volatile "
        "part to a trailing message or the end of the prompt, and round timestamps to the day if you need them.",
    ),
    "R-LOOP-001": dict(
        name="Agent loop around an LLM call with no iteration/step bound",
        category="unbounded_loop",
        severity="warning",
        fp_risk="medium",
        evidence=3,
        action="`while True` loop that calls a model, with no step counter, budget, or deadline in sight. If the model "
        "never emits a stop condition this spends until the process is killed. Add a max-iterations cap and a "
        "token or dollar budget check.",
    ),
    "R-LOOP-002": dict(
        name="Retry on an LLM call with no stop condition",
        category="retry_storm",
        severity="warning",
        fp_risk="low",
        evidence=2,
        action="This retry decorator wraps a model call but has no attempt or time limit. A persistent failure (bad "
        "request, context overflow, quota) will re-send the same prompt forever. Set `stop_after_attempt(...)` "
        "or `max_tries`, and do not retry non-retryable 4xx errors.",
    ),
    "R-CTX-008": dict(
        name="Indented JSON serialized into a prompt",
        category="prompt_bloat",
        severity="info",
        fp_risk="medium",
        evidence=3,
        action="`json.dumps(..., indent=N)` inside a prompt string adds whitespace tokens on every call. Serialize "
        "compactly (`separators=(',', ':')`) unless the model genuinely needs the formatting.",
    ),
    "R-CALL-004": dict(
        name="Embedding requested one item at a time inside a loop",
        category="batching_missed",
        severity="info",
        fp_risk="medium",
        evidence=5,
        action="Embedding call inside a loop over items. Collect the texts and call the provider's batch endpoint "
        "once (`embed_documents` / `embeddings.create(input=[...])`) to cut request count and latency.",
    ),
    "R-MEDIA-001": dict(
        name="Image detail hard-coded to high",
        category="media_oversize",
        severity="info",
        fp_risk="medium",
        evidence=0,
        action="Image input hard-coded to `detail: high`. High detail tiles the image and can cost several times the "
        "tokens of `low`/`auto`. Make it configurable and default to `auto` unless fine text must be read.",
    ),
    "R-OUT-001": dict(
        name="Chat completion with no output token cap",
        category="output_unbounded",
        severity="info",
        fp_risk="high",
        evidence=4,
        action="No `max_tokens`/`max_completion_tokens` on this completion call. Output tokens cost 3-5x input; a "
        "runaway generation or reasoning model can burn a large budget on one request. Set an explicit cap.",
    ),
    "R-MODEL-001": dict(
        name="Frontier model hard-coded for a cheap sub-task",
        category="model_overkill",
        severity="info",
        fp_risk="high",
        evidence=1,
        action="A frontier-tier model id is hard-coded in a helper that looks like title/summary/classification/"
        "routing work. These usually run fine on a small tier model at a fraction of the price. Make the model "
        "a setting and default the helper to the cheap tier.",
    ),
}
