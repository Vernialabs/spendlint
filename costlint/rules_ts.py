"""TypeScript/JavaScript rules (regex + brace matching; no parser dependency).
Each rule yields (rule_id, line, end_line, detail, confidence)."""
import re

LLM_CALL_RX = re.compile(r"(chat\.completions\.create|messages\.create|responses\.create|generateText|streamText|generateObject|\.invoke\(|\.ainvoke\(|generateContent)")
BOUND_RX = re.compile(r"(max|limit|iter|step|turn|round|attempt|retr|budget|remaining|deadline|timeout|count|until|stop)", re.I)
DYN_RX = re.compile(r"\$\{[^}]*(new Date|Date\.now|randomUUID|uuid|process\.cwd|toISOString|Math\.random|performance\.now)[^}]*\}")
SYSTEM_RX = re.compile(r"(system(Prompt|Message|Instructions?)?|SYSTEM_PROMPT|instructions)\s*[:=]\s*`")


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def balanced(text, start, open_ch, close_ch, limit=20000):
    depth = 0
    for i in range(start, min(len(text), start + limit)):
        c = text[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return None


def t_loop_001(text, path):
    for m in re.finditer(r"(while\s*\(\s*(true|1)\s*\)|for\s*\(\s*;\s*;\s*\))\s*\{", text):
        end = balanced(text, m.end() - 1, "{", "}")
        if end is None:
            continue
        body = text[m.end(): end]
        if not LLM_CALL_RX.search(body):
            continue
        if re.search(r"(readline|prompt\(|receive|\.on\(|sleep|setTimeout|accept\()", body):
            continue
        if BOUND_RX.search(body):
            continue
        yield ("R-LOOP-001", line_of(text, m.start()), line_of(text, m.start()), "while(true) around an LLM call, no bound identifier", 0.5)


VOL_ID_RX = re.compile(r"\$\{[^}]*\b\w*(session|request|run|trace|conversation|thread|invocation|turn|job)[_-]?(id|Id|ID|uuid|Uuid|key|Key)\b[^}]*\}")
VOL_PATH_RX = re.compile(r"\$\{[^}]*\b(cwd|workdir|workDir|working_?[dD]ir(ectory)?|directory|tmpDir|tempDir|projectPath|rootDir|sandboxDir|workspace(Root|Dir|Path)?)\b[^}]*\}")
HEAD_CHARS = 400
CACHE_KEY_RX = re.compile(r"(prompt_?[cC]ache_?[kK]ey|cacheKey)\s*[:=]\s*[^,;\n]*(randomUUID|uuid|nanoid|Date\.now|Math\.random|crypto\.random)")
TOOL_DESC_PATH_RX = re.compile(r"(description\s*[:=]\s*`[^`]*|\.describe\(\s*`[^`]*|\.replace(All)?\(\s*[\"'][^\"']*(directory|cwd)[^\"']*[\"']\s*,\s*[\w.]*)(\$\{[^}]*(directory|cwd|workdir)[^}]*\}|(Instance|location|process)\.(directory|cwd))")


def t_cache_003(text, path):
    for m in SYSTEM_RX.finditer(text):
        i = m.end() - 1
        j = i + 1
        while j < len(text) and j < i + 20000:
            if text[j] == "`" and text[j - 1] != "\\":
                break
            j += 1
        tmpl = text[i:j]
        d = DYN_RX.search(tmpl)
        if d:
            yield ("R-CACHE-003", line_of(text, m.start()), line_of(text, j), f"template literal for system prompt contains {d.group(0)[:50]}", 0.7)
            continue
        v = VOL_ID_RX.search(tmpl)
        if v:
            yield ("R-CACHE-003", line_of(text, i + v.start()), line_of(text, i + v.start()), f"per-request id {v.group(0)[:50]} interpolated into the system prompt", 0.55)
            continue
        p = VOL_PATH_RX.search(tmpl[:HEAD_CHARS])
        if p:
            yield ("R-CACHE-003", line_of(text, i + p.start()), line_of(text, i + p.start()), f"working-directory value {p.group(0)[:50]} at the head of the system prompt", 0.5)
    for m in CACHE_KEY_RX.finditer(text):
        yield ("R-CACHE-003", line_of(text, m.start()), line_of(text, m.start()), "prompt cache key is a fresh uuid/timestamp per request", 0.75)
    for m in TOOL_DESC_PATH_RX.finditer(text):
        yield ("R-CACHE-003", line_of(text, m.start()), line_of(text, m.start()), "working directory interpolated into a tool description (part of the cached tool schema)", 0.5)
    # environment/session lines placed first in an instructions block (opencode shape): `Current ... session ID: ${sessionID}`
    # only in prompt-building code, never in UI components
    if not path.endswith((".tsx", ".jsx")) and re.search(r"instructions|systemPrompt|system_prompt|SystemPrompt|<env>", text):
        for m in re.finditer(r"`[^`\n]{0,80}(session ID|session id|Session ID)[^`\n]{0,40}\$\{[^}]+\}", text):
            yield ("R-CACHE-003", line_of(text, m.start()), line_of(text, m.start()), "session id rendered into an instructions/env block", 0.5)


def t_media_001(text, path, skip_lines=()):
    lines = text.splitlines()
    for m in re.finditer(r"""\bdetail["']?\s*[:=]\s*["']high["']""", text):
        ln = line_of(text, m.start())
        if ln in skip_lines or lines[ln - 1].lstrip().startswith(("*", "//", "/*", "#")):
            continue
        yield ("R-MEDIA-001", line_of(text, m.start()), line_of(text, m.start()), "detail hard-coded to 'high'", 0.5)


def t_out_001(text, path):
    for m in re.finditer(r"chat\.completions\.create\s*\(", text):
        end = balanced(text, m.end() - 1, "(", ")")
        if end is None:
            continue
        args = text[m.end() - 1: end]
        if "..." in args or re.search(r"max_?(completion_)?[tT]okens|maxOutputTokens|max_output_tokens", args):
            continue
        if "messages" in args:
            yield ("R-OUT-001", line_of(text, m.start()), line_of(text, m.start()), "chat.completions.create without max_tokens", 0.35)


USAGE_NORMALIZER_RX = re.compile(r"\{[^{}]{0,400}\b(prompt_?[tT]okens|input_?[tT]okens)\s*:[^{}]{0,300}\b(completion_?[tT]okens|output_?[tT]okens)\s*:[^{}]{0,300}\}", re.S)


def t_acct_001(text, path):
    has_cache = re.search(r"cache_read|cache_creation|cached_tokens|cachedInput|cacheRead|cacheWrite|cached_input|promptCache|prompt_cache", text, re.I)
    if not has_cache:
        # usage normalizer: object literal mapping both input and output token keys from a usage object, no cache key anywhere
        for m in USAGE_NORMALIZER_RX.finditer(text):
            if re.search(r"usage\??\s*[.\[]", m.group(0)) and not re.search(r"(^|/)(locales?|i18n|types?|_generated|generated)(/|$)", path) and not path.endswith(".d.ts"):
                ln = line_of(text, m.start())
                if not text.splitlines()[ln - 1].lstrip().startswith(("*", "//")):
                    yield ("R-ACCT-001", ln, line_of(text, m.end()), "usage normalizer maps input/output tokens but the file never reads any cache token field", 0.45)
                    return
    if has_cache:
        return
    if not re.search(r"(cost|price|pricing|perMillion|per_million|usd)", text, re.I):
        return
    if re.search(r"(^|/)(locales?|i18n|messages|translations?)(/|$)", path):
        return
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith(("*", "//", "/*")):
            continue
        code = re.sub(r"""(['"`]).*?\1""", "", line.split("//")[0])
        if re.search(r"\b(prompt_tokens|input_tokens|promptTokens|inputTokens)\b", code) and re.search(r"[*/]", code):
            ctx = "\n".join(lines[max(0, i - 4): i + 4])
            if re.search(r"(cost|price|usd|per)", ctx, re.I):
                yield ("R-ACCT-001", i, i, "token count multiplied by a price; file never reads cache token fields", 0.5)
                return


TS_RULES = [t_loop_001, t_cache_003, t_media_001, t_out_001, t_acct_001]
