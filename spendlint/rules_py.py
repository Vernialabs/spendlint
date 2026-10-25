"""Python rules (AST based). Each rule yields (rule_id, line, end_line, detail, confidence)."""

import ast
import re

LLM_SUFFIXES = (
    "chat.completions.create",
    "chat.completions.parse",
    "responses.create",
    "messages.create",
    "messages.stream",
    "generate_content",
    "generate_content_async",
    "litellm.completion",
    "litellm.acompletion",
    "llm.ask",
    "llm.chat",
    "llm.invoke",
    "llm.ainvoke",
    "llm.complete",
    "llm.acomplete",
    "llm.generate",
    "model.invoke",
    "model.ainvoke",
    "model.generate_content",
    "client.chat",
    "client.generate",
)
LLM_LAST = {
    "acompletion",
    "call_llm",
    "ask_llm",
    "chat_completion",
    "create_chat_completion",
    "ask_tool",
    "run_llm",
    "llm_call",
    "get_completion",
    "chat_with_llm",
}
DYNAMIC = (
    "datetime.now",
    "datetime.utcnow",
    "datetime.today",
    "date.today",
    "time.time",
    "time.ctime",
    "time.strftime",
    "uuid.uuid4",
    "uuid4",
    "os.getcwd",
    "getcwd",
    "Path.cwd",
    "random.random",
    "random.randint",
    "arrow.utcnow",
    "pendulum.now",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
)
EMBED_SINGLE = {
    "embed_query",
    "get_embedding",
    "get_text_embedding",
    "aget_text_embedding",
    "get_query_embedding",
    "embed_text",
    "aembed_query",
    "generate_embedding",
    "get_embedding_for_text",
}
# single-item embed method name shapes seen in real code, with optional leading underscore / async prefix
EMBED_SINGLE_RX = re.compile(
    r"^_?a?(get_|generate_|create_|compute_|make_)?(general_|single_)?(text_|query_|document_)?embed(ding)?(_text|_query|_one|_single|_string)?$",
    re.I,
)
EMBED_CALLABLE_NAMES = {"embedding_function", "embed_fn", "embedding_fn", "embedder", "embed_func"}
BATCH_FN_RX = re.compile(
    r"(embed_documents|_get_text_embeddings|_aget_text_embeddings|embed_texts|aembed_documents|embed_batch|embed_many)",
    re.I,
)
# per-request volatile identifiers / paths / times interpolated into prompts
VOLATILE_ID_RX = re.compile(
    r"(session|request|run|trace|conversation|thread|invocation|message|turn|job|task)[_-]?(id|uuid|key)$|^(uuid|guid|nonce)$",
    re.I,
)
VOLATILE_PATH_RX = re.compile(
    r"^(cwd|workdir|work_dir|working_dir(ectory)?|directory|tmp_dir|temp_dir|sdk_cwd|project_path|root_dir|sandbox_dir|workspace(_root|_dir|_path)?)$",
    re.I,
)
VOLATILE_TIME_RX = re.compile(
    r"^(now|today|timestamp|current_time|time_str|date_str|current_date|datetime_str|ts)$", re.I
)
HEAD_CHARS = 400  # paths/dates only matter when they sit at the head of the cached prefix
SERIALIZER_RX = re.compile(r"(json|dump|serial|prompt|pretty|render)", re.I)
USAGE_IN_KEY = re.compile(r"^(prompt|input)_?tokens$", re.I)
USAGE_OUT_KEY = re.compile(r"^(completion|output)_?tokens$", re.I)
CACHE_FIELD_RX = re.compile(
    r"cache_read|cache_creation|cached_tokens|cached_input|cache_write|cacheRead|cacheWrite|cachedInput|prompt_cache",
    re.I,
)
CACHE_WRITE_RX = re.compile(r"cache_creation|cache_write|cacheWrite|creation_input|ephemeral_1h|ephemeral_5m", re.I)
CACHE_READ_RX = re.compile(r"cached_tokens|cache_read|cacheRead|cachedInput|cached_input", re.I)
ANTHROPIC_LIKE_RX = re.compile(r"anthropic|claude|litellm|bedrock", re.I)


def render_template(node):
    """Flatten a prompt expression (constants, f-strings, `+` concatenation, ' '.join([...]) lists) into
    segments: ("text", str) or ("var", dotted_name). Unknown expressions become an 8-char placeholder."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [("text", node.value)]
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(("text", v.value))
            elif isinstance(v, ast.FormattedValue):
                nm = dotted(v.value) if isinstance(v.value, (ast.Name, ast.Attribute)) else ""
                out.append(("var", nm) if nm else ("text", "x" * 8))
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return render_template(node.left) + render_template(node.right)
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mult)
        and isinstance(node.right, ast.Constant)
        and isinstance(node.right.value, int)
    ):
        text = "".join(t for k, t in render_template(node.left) if k == "text")
        return [("text", text * min(node.right.value, 100000 // max(1, len(text))))]
    if isinstance(node, (ast.List, ast.Tuple)):
        out = []
        for e in node.elts:
            out += render_template(e) + [("text", "\n")]
        return out
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join" and node.args:
        return render_template(node.args[0])
    return [("text", "x" * 8)]


def volatile_interpolation(node, head_only=True):
    """(kind, name, offset) for the first interpolated name that looks per-request volatile.
    offset = chars from the start of the rendered prompt; ids count anywhere, paths/times only within HEAD_CHARS."""
    off = 0
    for kind, val in render_template(node):
        if kind == "text":
            off += len(val)
            continue
        last = val.rsplit(".", 1)[-1]
        if VOLATILE_ID_RX.search(last):
            return ("id", val, off)
        if (VOLATILE_PATH_RX.match(last) or VOLATILE_TIME_RX.match(last)) and (not head_only or off < HEAD_CHARS):
            return ("path" if VOLATILE_PATH_RX.match(last) else "time", val, off)
        off += 8
    return None


BOUND_RX = re.compile(
    r"(max|limit|iter|step|turn|round|attempt|retr|budget|remaining|deadline|timeout|count|n_|num_|until|stop)", re.I
)
SYSTEM_NAME_RX = re.compile(r"(system[_ ]?(prompt|message|instruction)s?|^SYSTEM$|_SYSTEM$|^instructions?$)", re.I)
CHEAP_FN_RX = re.compile(r"(title|summar|classif|intent|rout(e|er)|rename|tagging|caption|keywords?)", re.I)
FRONTIER_RX = re.compile(
    r"^(gpt-4o|gpt-4\.1|gpt-4-turbo|gpt-4|gpt-5(\.\d+)?|o1|o3|claude-(3-opus|3-5-sonnet|3-7-sonnet|opus|sonnet)[\w.\-]*|claude-[a-z]+-\d[\w.\-]*(sonnet|opus)[\w.\-]*)$"
)
CHEAP_WORDS = ("mini", "nano", "haiku", "flash", "lite", "small")


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append("?")
    return ".".join(reversed(parts))


LOG_LAST = {
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "exception",
    "critical",
    "log",
    "print",
    "write",
    "echo",
    "fatal",
    "write_text",
    "write_bytes",
    "print_panel",
    "panel",
    "markdown",
    "toast",
    "notify",
    "rprint",
    "pprint",
    "secho",
    "fail",
    "abort",
}
GENERATED_PATH_RX = re.compile(
    r"(_generated|/generated/|/types?\.py$|/schemas?\.py$|/models?\.py$|_types\.py$|_pb2)", re.I
)


def code_only(tree, text):
    """text with docstring lines and comments blanked, for file-level keyword gates"""
    doc = docstring_lines(tree)
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        out.append("" if i in doc else line.split("#")[0])
    return "\n".join(out)


DATE_ONLY_FMT = re.compile(r"%[HMSfXcpI]")


def docstring_lines(tree):
    """line numbers covered by bare string-expression statements (docstrings, comment-strings)"""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            out.update(range(n.lineno, (n.end_lineno or n.lineno) + 1))
    return out


def log_context_ids(tree):
    """ids of every node nested inside a logging/print/file-write call, an assert, or a raise (never a prompt)"""
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and dotted(n.func).rsplit(".", 1)[-1] in LOG_LAST:
            for a in list(n.args) + [k.value for k in n.keywords]:
                ids.update(id(x) for x in ast.walk(a))
        elif isinstance(n, (ast.Assert, ast.Raise)):
            ids.update(id(x) for x in ast.walk(n))
    return ids


def strip_quotes(code):
    return re.sub(r"""(['"`]).*?\1""", "", code)


def is_llm_call(n):
    if not isinstance(n, ast.Call):
        return False
    d = dotted(n.func)
    last = d.rsplit(".", 1)[-1]
    return d.endswith(LLM_SUFFIXES) or last in LLM_LAST


def calls_in(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def has_dynamic(node):
    nodes = list(ast.walk(node))
    day_level = set()
    for x in nodes:
        if isinstance(x, ast.Attribute) and x.attr == "date" and isinstance(x.value, ast.Call):
            day_level.add(id(x.value))  # datetime.now().date()
        if (
            isinstance(x, ast.Call)
            and isinstance(x.func, ast.Attribute)
            and x.func.attr == "strftime"
            and isinstance(x.func.value, ast.Call)
        ):
            fmt = (
                x.args[0].value
                if x.args and isinstance(x.args[0], ast.Constant) and isinstance(x.args[0].value, str)
                else ""
            )
            if fmt and not DATE_ONLY_FMT.search(fmt):
                day_level.add(id(x.func.value))  # .strftime('%Y-%m-%d')
    for c in [n for n in nodes if isinstance(n, ast.Call)]:
        d = dotted(c.func)
        if d.endswith(DYNAMIC) and id(c) not in day_level and not d.endswith("date.today"):
            return d
    return None


def r_loop_001(tree, text, path):
    for n in ast.walk(tree):
        if isinstance(n, ast.While) and isinstance(n.test, ast.Constant) and n.test.value in (True, 1):
            body_calls = [c for s in n.body for c in calls_in(s)]
            if not any(is_llm_call(c) for c in body_calls):
                continue
            # interactive/daemon loops are not runaway spend
            names = {dotted(c.func).rsplit(".", 1)[-1] for c in body_calls}
            if names & {
                "input",
                "receive",
                "recv",
                "readline",
                "sleep",
                "get",
                "accept",
                "wait",
                "prompt",
                "receive_json",
                "receive_text",
            }:
                continue
            idents = set()
            for s in n.body:
                for x in ast.walk(s):
                    if isinstance(x, ast.Name):
                        idents.add(x.id)
                    elif isinstance(x, ast.Attribute):
                        idents.add(x.attr)
                    elif isinstance(x, ast.arg):
                        idents.add(x.arg)
            if any(BOUND_RX.search(i) for i in idents):
                continue
            yield (
                "R-LOOP-001",
                n.lineno,
                n.lineno,
                "while True around an LLM call, no bound identifier in loop body",
                0.55,
            )


def r_loop_002(tree, text, path):
    if not re.search(r"tenacity|backoff", text):
        return
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_has_llm = any(is_llm_call(c) for c in calls_in(n)) or re.search(
            r"(llm|completion|chat|embed|generate)", n.name, re.I
        )
        if not body_has_llm:
            continue
        for d in n.decorator_list:
            name = dotted(d.func if isinstance(d, ast.Call) else d)
            last = name.rsplit(".", 1)[-1]
            if last == "retry":  # tenacity
                kws = {k.arg for k in d.keywords} if isinstance(d, ast.Call) else set()
                if "stop" not in kws:
                    yield ("R-LOOP-002", d.lineno, d.lineno, "tenacity @retry without stop=", 0.7)
                else:
                    stop = [k.value for k in d.keywords if k.arg == "stop"][0]
                    if "stop_never" in ast.dump(stop):
                        yield ("R-LOOP-002", d.lineno, d.lineno, "tenacity stop_never", 0.8)
            elif last == "on_exception" and name.startswith("backoff"):
                kws = {k.arg for k in d.keywords} if isinstance(d, ast.Call) else set()
                if not kws & {"max_tries", "max_time"}:
                    yield ("R-LOOP-002", d.lineno, d.lineno, "backoff.on_exception without max_tries/max_time", 0.7)


def r_cache_003(tree, text, path):
    seen = set()

    def emit(node, why, dyn, conf=0.75):
        key = node.lineno
        if key in seen:
            return None
        seen.add(key)
        return ("R-CACHE-003", node.lineno, getattr(node, "end_lineno", node.lineno), f"{why} contains {dyn}()", conf)

    def check(node, why):
        """dynamic call first (strong), then volatile interpolated name (weaker)"""
        dyn = has_dynamic(node)
        if dyn:
            return emit(node, why, dyn)
        v = volatile_interpolation(node)
        if v:
            kind, nm, off = v
            return emit(
                node,
                why,
                f"per-request {kind} `{nm}`" + (f" at offset {off}" if kind != "id" else ""),
                0.55 if kind == "id" else 0.5,
            )
        return None

    # prompt_cache_key / cache key given a fresh uuid or timestamp: a cache miss by construction
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value in ("prompt_cache_key", "cache_key", "session_id")
                    and has_dynamic(v)
                ):
                    r = emit(v, f"`{k.value}`", has_dynamic(v))
                    if r:
                        yield r
        elif isinstance(n, ast.Call):
            for kw in n.keywords:
                if kw.arg in ("prompt_cache_key", "cache_key") and has_dynamic(kw.value):
                    r = emit(kw.value, f"`{kw.arg}=`", has_dynamic(kw.value))
                    if r:
                        yield r

    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                nm = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else "")
                if nm and SYSTEM_NAME_RX.search(nm) and n.value is not None:
                    r = check(n.value, f"`{nm}`")
                    if r:
                        yield r
        elif isinstance(n, ast.Dict):
            keys = [k.value if isinstance(k, ast.Constant) else None for k in n.keys]
            if "role" in keys and "content" in keys:
                role = n.values[keys.index("role")]
                if isinstance(role, ast.Constant) and role.value in ("system", "developer"):
                    r = check(n.values[keys.index("content")], "system message")
                    if r:
                        yield r
        elif isinstance(n, ast.Call):
            last = dotted(n.func).rsplit(".", 1)[-1]
            if (
                last in ("SystemMessage", "SystemMessagePromptTemplate", "ChatMessage") and n.args
            ) or last == "SystemMessage":
                r = check(n, "SystemMessage")
                if r:
                    yield r
            for k in n.keywords:
                if k.arg in ("system", "system_prompt", "instructions", "system_instruction"):
                    r = check(k.value, f"`{k.arg}=` argument")
                    if r:
                        yield r
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and re.search(
            r"(system_?prompt|build_system|get_system|system_message|_supplement|instructions)", n.name, re.I
        ):
            for s in ast.walk(n):
                if isinstance(s, ast.Return) and s.value is not None:
                    r = check(s.value, f"`{n.name}()` return value")
                    if r:
                        yield r


def _indent_kw(c):
    return any(
        k.arg == "indent" and not (isinstance(k.value, ast.Constant) and k.value.value in (None, 0)) for k in c.keywords
    )


def r_ctx_008(tree, text, path):
    skip = log_context_ids(tree)
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr) and id(n) not in skip:
            for v in n.values:
                if isinstance(v, ast.FormattedValue):
                    for c in calls_in(v.value):
                        d = dotted(c.func)
                        last = d.rsplit(".", 1)[-1]
                        if d.endswith("json.dumps") and _indent_kw(c):
                            yield (
                                "R-CTX-008",
                                c.lineno,
                                c.lineno,
                                "json.dumps(indent=...) interpolated in an f-string",
                                0.5,
                            )
                        elif SERIALIZER_RX.search(last) and _indent_kw(
                            c
                        ):  # helper wrapping json.dumps, e.g. to_prompt_json(x, indent=2)
                            yield (
                                "R-CTX-008",
                                c.lineno,
                                c.lineno,
                                f"{last}(indent=...) interpolated in an f-string",
                                0.45,
                            )
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and re.search(
            r"(to_)?prompt.*json|json.*prompt", n.name, re.I
        ):
            # a prompt-serialization helper whose default indent is > 0
            args = n.args
            defaults = (
                dict(zip([a.arg for a in args.args][-len(args.defaults) :], args.defaults)) if args.defaults else {}
            )
            defaults.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
            dv = defaults.get("indent")
            if isinstance(dv, ast.Constant) and isinstance(dv.value, int) and dv.value > 0:
                yield (
                    "R-CTX-008",
                    n.lineno,
                    n.lineno,
                    f"prompt JSON helper `{n.name}` defaults indent={dv.value}",
                    0.5,
                )
        elif isinstance(n, ast.Call) and dotted(n.func).endswith(".format"):
            # message objects stringified into a prompt: .format(messages=<bare name>) with no serializer applied
            for k in n.keywords:
                # only `messages=`: history/chat_history are almost always pre-rendered strings
                if (
                    k.arg == "messages"
                    and isinstance(k.value, ast.Name)
                    and not re.search(r"(formatted|str|text|rendered|buffer|serialized|joined)", k.value.id, re.I)
                ):
                    yield (
                        "R-CTX-008",
                        n.lineno,
                        n.lineno,
                        f".format({k.arg}={k.value.id}) interpolates message objects via str()",
                        0.35,
                    )


def r_call_004(tree, text, path):
    loops = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.For, ast.AsyncFor, ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp))
    ]
    # per-item calls inside an `except NotImplementedError` fallback are a deliberate degradation, not a defect
    fallback = set()
    for h in ast.walk(tree):
        if isinstance(h, ast.ExceptHandler) and h.type is not None and "NotImplemented" in ast.dump(h.type):
            fallback.update(id(x) for x in ast.walk(h))
    seen = set()
    for lp in loops:
        if id(lp) in fallback:
            continue
        if isinstance(lp, (ast.For, ast.AsyncFor)):
            tgts = {x.id for x in ast.walk(lp.target) if isinstance(x, ast.Name)}
            scope = lp.body
            if "batch" in ast.dump(lp.iter).lower() or any(
                re.search(r"(batch|chunk|group|page|shard)", t, re.I) for t in tgts
            ):
                continue
        else:
            tgts = {x.id for g in lp.generators for x in ast.walk(g.target) if isinstance(x, ast.Name)}
            scope = [lp.elt] if hasattr(lp, "elt") else [lp.key, lp.value]
        for s in scope:
            for c in calls_in(s):
                d = dotted(c.func)
                last = d.rsplit(".", 1)[-1]
                hit = False
                uses_tgt = lambda x: (
                    (isinstance(x, ast.Name) and x.id in tgts)
                    or (
                        isinstance(x, (ast.Attribute, ast.Subscript, ast.JoinedStr, ast.Call))
                        and any(isinstance(y, ast.Name) and y.id in tgts for y in ast.walk(x))
                    )
                )
                if last in EMBED_SINGLE or EMBED_SINGLE_RX.match(last) or last in EMBED_CALLABLE_NAMES:
                    args = list(c.args) + [k.value for k in c.keywords]
                    recv = c.func.value if isinstance(c.func, ast.Attribute) else None
                    hit = any(uses_tgt(x) for x in args) or (recv is not None and uses_tgt(recv))
                elif d.endswith("embeddings.create"):
                    for k in c.keywords:
                        if (
                            k.arg == "input"
                            and isinstance(k.value, (ast.Name, ast.Attribute, ast.Subscript, ast.JoinedStr))
                            and any(isinstance(y, ast.Name) and y.id in tgts for y in ast.walk(k.value))
                            and not isinstance(k.value, ast.List)
                        ):
                            hit = True
                if hit and c.lineno not in seen:
                    seen.add(c.lineno)
                    enclosing = next(
                        (
                            f.name
                            for f in ast.walk(tree)
                            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and f.lineno <= c.lineno <= (f.end_lineno or f.lineno)
                            and BATCH_FN_RX.search(f.name)
                        ),
                        None,
                    )
                    if enclosing:
                        yield (
                            "R-CALL-004",
                            c.lineno,
                            c.lineno,
                            f"{last}() per item inside batch method {enclosing}()",
                            0.65,
                        )
                    else:
                        yield ("R-CALL-004", c.lineno, c.lineno, f"{last}() per loop item", 0.5)


def r_out_001(tree, text, path):
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            d = dotted(n.func)
            last = d.rsplit(".", 1)[-1]
            if (
                d.endswith("chat.completions.create")
                or d.endswith("litellm.completion")
                or d.endswith("litellm.acompletion")
                or last == "acompletion"
            ):
                kws = {k.arg for k in n.keywords}
                if None in kws:  # **kwargs may carry the cap
                    continue
                if not kws & {"max_tokens", "max_completion_tokens", "max_output_tokens", "max_new_tokens"} and kws & {
                    "messages",
                    "model",
                }:
                    yield ("R-OUT-001", n.lineno, n.lineno, f"{d}() without max_tokens", 0.35)
            # cap explicitly disabled: f(..., max_tokens=None)
            for k in n.keywords:
                if (
                    k.arg in ("max_tokens", "max_completion_tokens", "max_output_tokens")
                    and isinstance(k.value, ast.Constant)
                    and k.value.value is None
                ):
                    yield ("R-OUT-001", k.value.lineno, k.value.lineno, f"{k.arg}=None disables the output cap", 0.45)
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is None:
            for t in n.targets:
                nm = (
                    t.id
                    if isinstance(t, ast.Name)
                    else (t.slice.value if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) else None)
                )
                if nm in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
                    yield ("R-OUT-001", n.lineno, n.lineno, f"{nm} set to None disables the output cap", 0.45)


def r_model_001(tree, text, path):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and CHEAP_FN_RX.search(n.name):
            for c in ast.walk(n):
                if (
                    isinstance(c, ast.Constant)
                    and isinstance(c.value, str)
                    and FRONTIER_RX.match(c.value)
                    and not any(w in c.value for w in CHEAP_WORDS)
                ):
                    if any(is_llm_call(x) for x in calls_in(n)):
                        yield ("R-MODEL-001", c.lineno, c.lineno, f"`{c.value}` hard-coded in {n.name}()", 0.4)
                        break


CACHE_READ_ACCESS_RX = re.compile(
    r"(\.|\[[\"']|get\([\"'])(cached_tokens|cache_read_input_tokens|cache_read_tokens|cached_input_tokens|cacheRead\w*|cache_read)\b"
)


def r_acct_001(tree, text, path):
    if GENERATED_PATH_RX.search("/" + path):
        return  # type/schema/generated modules declare usage fields; they do not account for them
    code = code_only(tree, text)
    has_cache = bool(CACHE_FIELD_RX.search(code))
    # (b) half-coverage: *reads* cache-read tokens but never any cache-write field, in code that knows Anthropic/LiteLLM/Bedrock
    if (
        has_cache
        and CACHE_READ_ACCESS_RX.search(code)
        and not CACHE_WRITE_RX.search(code)
        and ANTHROPIC_LIKE_RX.search(code)
        and re.search(r"(usage|metrics)", code, re.I)
    ):
        for i, line in enumerate(code.splitlines(), 1):
            if CACHE_READ_ACCESS_RX.search(strip_quotes(line)):
                yield (
                    "R-ACCT-001",
                    i,
                    i,
                    "reads cache-read tokens but no cache-write/creation field anywhere in the file",
                    0.4,
                )
                break
    # (a) usage normalizer: dict literal mapping input+output token keys read off a usage object, file has no cache field at all
    if not has_cache:
        for n in ast.walk(tree):
            if isinstance(n, ast.Dict):
                keys = [k.value for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if any(USAGE_IN_KEY.match(k) for k in keys) and any(USAGE_OUT_KEY.match(k) for k in keys):
                    src = ast.get_source_segment(text, n) or ""
                    if re.search(r"usage\s*[.\[]|getattr\(\s*[\w.]*usage", src):
                        yield (
                            "R-ACCT-001",
                            n.lineno,
                            n.end_lineno or n.lineno,
                            "usage normalizer maps input/output tokens from `usage` but the file never reads any cache token field",
                            0.45,
                        )
                        return
    # (c) file-level: prices token counts but never touches cache fields
    if has_cache:
        return
    if not re.search(r"(cost|price|pricing|per_million|per_1k|per_token|usd)", text, re.I):
        return
    doc = docstring_lines(tree)
    for i, line in enumerate(text.splitlines(), 1):
        code = strip_quotes(line.split("#")[0])
        if i in doc:
            continue
        if re.search(r"\b(prompt_tokens|input_tokens)\b", code) and re.search(r"[*/]", code):
            ctx = "\n".join(text.splitlines()[max(0, i - 4) : i + 4])
            if re.search(r"(cost|price|usd|per_)", ctx, re.I):
                yield (
                    "R-ACCT-001",
                    i,
                    i,
                    "token count multiplied by a price; file never reads cache token fields",
                    0.5,
                )
                return


PY_RULES = [r_loop_001, r_loop_002, r_cache_003, r_ctx_008, r_call_004, r_out_001, r_model_001, r_acct_001]
