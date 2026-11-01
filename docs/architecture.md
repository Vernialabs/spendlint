# spendlint architecture (RFC, engine v2)

Status: **accepted contract for the 0.2.0 to 0.6.0 releases** (week of 2026-10-26). Rule, engine and SDK
work implements against this document. A change to anything marked *contract* needs a PR that edits this
file first.

spendlint is a linter for LLM API best practices. Each rule enforces **one documented practice** ("set an
output cap", "put `cache_control` at the end of the stable prefix"). A finding means the code breaks that
practice. A strong rule never flags code that already follows it.

This version replaces the stdlib-`ast` Python rules and the regex TypeScript rules with one tree-sitter
engine. Every language is extracted into the same model, values are resolved, and rules are visitors in the
style of ESLint and Ruff.

Contents

1. [Dependencies (verified 2026-10-25)](#1-dependencies-verified-2026-10-25)
2. [Module layout](#2-module-layout)
3. [Shared model](#3-shared-model)
4. [Value resolution](#4-value-resolution)
5. [SDK catalog and models.toml](#5-sdk-catalog-and-modelstoml)
6. [Rule API](#6-rule-api)
7. [RuleTester](#7-ruletester)
8. [Pipeline](#8-pipeline)
9. [Error reporting](#9-error-reporting)
10. [Compatibility contract](#10-compatibility-contract)
11. [Packaging](#11-packaging)
12. [Performance budget](#12-performance-budget)
13. [Adding a language](#13-adding-a-language)
14. [Adding an SDK](#14-adding-an-sdk)
15. [Worked example: R-OUT-001](#15-worked-example-r-out-001)
16. [Open decisions and known conflicts](#16-open-decisions-and-known-conflicts)

---

## 1. Dependencies (verified 2026-10-25)

Checked on PyPI on 2026-10-25. Each package was installed into a clean CPython 3.11.5 venv on win_amd64
and run through `scripts/dev/probe_grammars.py`, which loads each grammar with `Language(...)`, checks its
ABI, parses a sample with no ERROR or MISSING node, and runs one `Query` with a `#eq?` predicate through
`QueryCursor(query).captures(root)`. All 8 grammars passed.

| Distribution | Pin (*contract*) | Released | Requires-Python | Licence | Entry point(s) | ABI | Wheel tags |
|---|---|---|---|---|---|---|---|
| `tree-sitter` | `>=0.26,<0.27` (0.26.0) | 2026-06-30 | >=3.10 | MIT (classifier; the `license` field is empty) | core | supports 13..15 | cp310 to cp314: manylinux2014 x86_64 and aarch64, musllinux x86_64, manylinux_2_31 riscv64, macOS x86_64 and arm64, win_amd64, win_arm64 |
| `tree-sitter-python` | `==0.25.0` | 2025-09-11 | >=3.10 | MIT | `language()` | 15 | cp310-abi3: manylinux x86_64 and aarch64, musllinux x86_64 and aarch64, macOS x86_64 and arm64, win_amd64, win_arm64 |
| `tree-sitter-javascript` | `==0.25.0` | 2025-09-01 | >=3.10 | MIT | `language()` | 15 | same as python |
| `tree-sitter-typescript` | `==0.23.2` | 2024-11-11 | >=3.9 | MIT | `language_typescript()`, `language_tsx()` (both confirmed) | 14 | cp39-abi3: manylinux x86_64 and aarch64, musllinux **x86_64 only**, macOS, win_amd64, win_arm64 |
| `tree-sitter-go` | `==0.25.0` | 2025-08-29 | >=3.10 | MIT | `language()` | 15 | same as python |
| `tree-sitter-java` | `==0.23.5` | 2024-12-21 | >=3.9 | MIT | `language()` | 14 | same as typescript (no musllinux aarch64) |
| `tree-sitter-rust` | `==0.24.2` | 2026-03-27 | >=3.9 | MIT | `language()` | 15 | cp39-abi3: same set as python |
| `tree-sitter-c-sharp` | `==0.23.5` | 2026-04-14 | >=3.9 | MIT | `language()` | 15 | cp310-abi3: same set as python |

Every grammar's ABI (14 or 15) is within the core's supported range (`MIN_COMPATIBLE_LANGUAGE_VERSION` 13
to `LANGUAGE_VERSION` 15). Wheels on manylinux x86_64 total about 1.85 MB (core 649 KB, typescript
337 KB, c-sharp 350 KB, rust 161 KB, python 106 KB, javascript 97 KB, java 82 KB, go 65 KB). The grammar
wheels are `abi3`, so the same wheel serves 3.11 to 3.14.

**Gaps and hazards** (each has an owner in the tracker):

- **`progress_callback` crashes the interpreter.** With tree-sitter 0.26.0 on CPython 3.11 / win_amd64,
  passing any `progress_callback` segfaulted (exit 0xC0000005), whether to `Parser.parse(read_callback, ...)`
  or to `QueryCursor.captures(...)`. It crashed even when the callback always returned False. With a
  `bytes` source the callback is ignored (with a `UserWarning`). **The engine never passes
  `progress_callback`.** Work is bounded by `MAX_BYTES` (400 KB, unchanged) and by `QueryCursor(query,
  match_limit=...)`, which works. The probe re-checks this hazard in a subprocess on every bump.
- **ABI 14 grammars carry no metadata.** For typescript, tsx and java, `Language.name` and
  `Language.semantic_version` are `None`. Selfcheck prints the distribution version from
  `importlib.metadata`, never `semantic_version`. (tree-sitter-rust 0.24.2 also reports
  `semantic_version` 0.24.1, so that field can't be trusted.)
- **Platforms without wheels** fall back to the sdist, which needs a C compiler:
  - Alpine/musl on aarch64: no core wheel, and no typescript or java wheel;
  - linux riscv64: there is a core wheel but no grammar wheels;
  - 32-bit Windows: nothing;
  - free-threaded CPython (`cp314t`): no core wheel, and the grammars are abi3, which a free-threaded build
    can't load.

  This is documented as unsupported in USAGE.md. The Action runs on ubuntu-latest x86_64 and isn't affected.
- **Only 3.11 / win_amd64 was probed locally.** The CI matrix (ubuntu, macos, windows × 3.11, 3.14) runs
  `scripts/dev/probe_grammars.py` and `spendlint --selfcheck` on every cell. A failing cell blocks the release.
- **typescript 0.23.2 and java 0.23.5 date from 2024** but are the newest releases. Dependabot watches them,
  and a bump needs a green probe run.
- **The config-file "languages" in `practices.json`** (`properties/yaml`, `cron/yaml`, for example Spring AI
  `spring.ai.anthropic.chat.max-tokens`) are not tree-sitter grammars. They are out of scope for the engine
  this week (see §16).

Python floor: **`requires-python = ">=3.11"`** (*contract*). Code must run on 3.11, so these are not
allowed: PEP 695 `type` statements and `def f[T]()` generics, PEP 701 nested same-quote f-strings, and
`typing.override`. Allowed and encouraged: `tomllib`, `dataclass(slots=True, frozen=True)`, `match`,
`typing.Self`, `ExceptionGroup`. PyYAML stays an optional extra.

---

## 2. Module layout

```
spendlint/
  __init__.py            __version__
  __main__.py            entry point (unchanged)
  api.py                 scan(paths, rules=, config=, diff=) -> ScanResult      (the private harness uses this)
  cli.py  action.py  report.py  config.py  telemetry.py      (kept; they call api/engine)
  meta.py                SHIM: RULES = registry.legacy_table()  (dict id -> name/category/severity/fp_risk/evidence/action)
  scan.py                SHIM: scan_repo(), parse_diff(), suppressed_rules(), to_sarif() delegate to engine
  gitdiff.py             SHIM: re-exports engine.diff (added_lines, GitError); test_cli imports it
  py.typed
  engine/
    discover.py          walk + SKIP_DIR/SKIP_FILE + ignore_paths + MAX_BYTES; file-as-root; correct relpaths
    parse.py             grammar registry, lazy Language load, ABI check, Parser/Query caches, parse errors
    model.py             frozen dataclasses of §3 (the only types rules see)
    expr.py              the engine-internal expression IR extractors emit (opaque to rules)
    extract.py           shared extractor helpers: spans, IR builders, query runner, comment table
    symbols.py           scopes, bindings, imports, the repo module index, self.x / this.x members
    values.py            resolve() / candidates() / param() of §4
    visitor.py           FileRule, RepoRule, hook dispatch in source order
    registry.py          @rule, RuleMeta, Source, discovery of spendlint.rules.*, legacy_table()
    context.py           FileContext, RepoContext, CaptureContext (report, resolve, options, catalogs)
    options.py           Opt schema + validation for `rules:` options
    suppress.py          comment-node-aware `spendlint: ignore` handling
    diff.py              (was gitdiff.py) added_lines(), parse_diff()
    errors.py            ScanStats counters, RuleError isolation, SPENDLINT_DEBUG
    run.py               the pipeline of §8
  lang/
    python/              extractor.py  queries/*.scm
    ecmascript/          extractor.py (shared)  typescript/ tsx/ javascript/ (queries/*.scm per dialect)
    go/  java/  rust/  csharp/                   (Thursday; same layout)
  sdk/
    catalog.py           CallShape / ClientShape / match()
    anthropic.py openai.py google_genai.py bedrock.py litellm.py langchain.py langgraph.py llamaindex.py
    vercel_ai.py openrouter.py claude_agent_sdk.py openai_agents.py instructor.py  (+ go/java/rust/csharp shapes)
    models.py            ModelCatalog over data/models.toml
    data/models.toml
  rules/
    __init__.py          empty (discovery walks the package; nothing is imported eagerly)
    <cat>/__init__.py
    <cat>/r_<cat>_<nnn>.py      one rule per module; cat = lower-case middle segment of the id
  testing/
    rule_tester.py       RuleTester, py/ts/tsx/js/go/java/rust/cs case helpers, err()
  tools/
    gendocs.py           docs/rules/R-*.md from @rule metadata + example cases; --check for CI
tests/                   outside the package (pytest); tests/rules/<cat>/test_r_<cat>_<nnn>.py
docs/                    architecture.md, writing-rules.md, rules/R-*.md (generated)
scripts/dev/probe_grammars.py
```

`<cat>` covers every category in `practices.json`: `acct api batch cache call cfg ctx emb loop media
model out retry think tier tool`. (The plan's first draft listed 9 of them.)

**Import rules** (*contract*; checked by a test that walks the import graph):

- `engine/*` never imports `rules/*`, `sdk/*` rule modules, `cli`, `report` or `meta`. The only exception is
  `registry.py`, which imports rule modules dynamically when it discovers them.
- `lang/*` imports `engine/{model,expr,extract,parse}` only. Extractors know syntax, never SDKs or rules.
- `sdk/*` imports `engine/model` only. It is data plus small match functions.
- `rules/*` imports only `engine.{model,registry,visitor,context,options}` and `sdk.models`. It never
  imports `tree_sitter` (except `visit_capture` rules, which use `CaptureNode`), and never imports
  another rule.
- `meta.py`, `scan.py`, `gitdiff.py` are compatibility shims that hold no logic. `cli`, `action`,
  `report`, `telemetry` keep their public functions.

---

## 3. Shared model

All model types are `@dataclass(frozen=True, slots=True)` in `engine/model.py`. They are picklable, so a
later `--jobs` can ship them between processes, and they hold no tree-sitter objects. Rules see only these
types. Field lists are the *contract*: adding a field is fine, but renaming or removing one needs an RFC edit.

Expression-valued fields have type `Expr`. That is the engine-internal IR from `engine/expr.py`, and it is
opaque to rules: a rule turns an `Expr` (or a `Param`) into a `Value` with `ctx.resolve()` or
`ctx.candidates()` (§4). This is how the tree can be dropped after pass 1 while resolution still runs in
pass 2.

### 3.1 Positions and languages

```python
Lang = Literal["python", "javascript", "typescript", "tsx", "go", "java", "rust", "csharp"]   # grammar id
Family = Literal["python", "javascript", "typescript", "go", "java", "rust", "csharp"]        # what rules declare

class Span:
    start_byte: int; end_byte: int          # offsets into the file's bytes
    line: int; col: int                     # 1-based; col counts Unicode code points
    end_line: int; end_col: int             # end_col is exclusive
```

| Extension | Lang | Family |
|---|---|---|
| `.py` | python | python |
| `.js .jsx .mjs .cjs` | javascript | javascript |
| `.ts .mts .cts` | typescript | typescript |
| `.tsx` | tsx | typescript |
| `.go` / `.java` / `.rs` / `.cs` | go / java / rust / csharp | same |

`.pyi`, `.d.ts`, `.min.js` are never scanned.

### 3.2 Values

`Value = Const | Template | DictVal | ListVal | EnvVar | ParamRef | CallResult | MemberRef | Unknown`

| Type | Fields | Meaning |
|---|---|---|
| `Const` | `value: str \| int \| float \| bool \| None`, `raw: str`, `span` | A literal or a folded constant. Python `None`, JS `null`/`undefined`, Go/Java/C# `nil`/`null` and Rust `None` all give `value=None`; `raw` keeps the spelling. |
| `Template` | `template: StringTemplate`, `span` | A string built with interpolation (§3.4). |
| `DictVal` | `entries: tuple[tuple[str, Expr], ...]`, `closed: bool`, `span` | A dict, object literal or struct literal after merges and spreads. `closed` is true only when every key that can be present is known. |
| `ListVal` | `items: tuple[Expr, ...]`, `closed: bool`, `span` | A list or array literal. |
| `EnvVar` | `name: str`, `default: Value \| None`, `span` | `os.getenv("X", d)`, `os.environ.get`, `os.environ["X"]`, `process.env.X ?? d`, `os.Getenv`, `System.getenv`, `Environment.GetEnvironmentVariable`. Type conversions (`int(...)`, `Number(...)`) apply to `default`. |
| `ParamRef` | `function: str` (qualname), `name: str`, `index: int`, `default: Expr \| None`, `span` | A function parameter. `resolve()` returns it as-is; `candidates()` binds it through the call sites, one hop (§4.3). |
| `CallResult` | `callee: Value`, `qualname: str \| None`, `args: tuple[Expr, ...]`, `kwargs: tuple[tuple[str, Expr], ...]`, `span` | The symbolic result of a call that wasn't folded, e.g. `OpenAI()` or `openai("gpt-5.5")`. |
| `MemberRef` | `base: Value \| None`, `attr: str`, `qualname: str \| None`, `span` | An attribute or imported symbol carried symbolically. For `from openai import OpenAI`, `OpenAI` is `MemberRef(None, "OpenAI", "openai.OpenAI")`, and `client.chat.completions.create` has qualname `openai.OpenAI().chat.completions.create`. |
| `Unknown` | `reason: str` | Can't be resolved. Reasons: `depth`, `cycle`, `branches`, `external`, `dynamic`, `mutated`, `open`, `unsupported`, `parse_error`. **`Unknown` never reports** (§4.5). |

Lookup results that aren't values:

```python
class Absent:      span: Span | None        # provably not sent / not present (closed container, no key)
class Param:       name: str                # normalized name, e.g. "max_output"
                   raw: str                 # name as written: "max_completion_tokens", "maxOutputTokens", "MaxTokens"
                   expr: Expr               # resolve with ctx.resolve(param)
                   span: Span               # the key/keyword/field, through the value
class Candidate:   value: Value | Absent    # one possible value on one path
                   span: Span               # where that value enters (the literal, the assignment, the default)
                   via: Span | None         # the call site that supplied it, for ParamRef bindings
```

### 3.3 Bindings, functions, calls, imports

```python
class Binding:
    name: str; scope: str                    # scope qualname, "" = module
    kind: Literal["assign", "augassign", "param", "import", "for", "with", "global", "class_attr", "member"]
    expr: Expr | None                        # RHS (None for params/imports; see Import)
    span: Span
    conditional: bool                        # assigned inside if/else/try/match/ternary arms
    in_loop: bool

class Import:
    module: str                              # "openai", "@ai-sdk/openai", "./models", "github.com/anthropics/anthropic-sdk-go"
    name: str | None                         # imported member, None for `import x` / namespace import
    alias: str; span: Span
    kind: Literal["import", "from", "require", "dynamic", "using", "use"]
    resolved_path: str | None                # repo-relative file when the module index finds it

class ParamSpec:
    name: str; index: int
    kind: Literal["positional", "keyword", "varargs", "varkw"]
    default: Expr | None

class Function:
    qualname: str; name: str; span: Span
    params: tuple[ParamSpec, ...]
    decorators: tuple[Expr, ...]
    class_name: str | None; is_async: bool; exported: bool
    returns: tuple[Expr, ...]                # every `return` expression, source order

class Call:                                  # generic calls, only those matching some rule's `calls=` patterns
    span: Span; anchor: Span                 # anchor = callee through "("
    callee: str                              # source text, e.g. "json.dumps"
    qualname: str | None                     # resolved, e.g. "json.dumps"
    args: tuple[Expr, ...]; kwargs: tuple[tuple[str, Expr], ...]
    has_spread: bool; function: str | None   # enclosing function qualname
```

### 3.4 Domain objects

```python
class StringTemplate:
    id: int; span: Span
    kind: Literal["fstring", "template_literal", "format", "percent", "concat", "join", "sprintf", "interpolated"]
    parts: tuple["TemplatePart", ...]        # TemplatePart(text: str | None, expr: Expr | None, span: Span)
    head_len: int                            # constant characters before the first interpolation
    function: str | None

class LLMCall:
    id: int; span: Span
    anchor: Span                             # callee through "(": the default report location (one line)
    sdk: str                                 # catalog id: "openai", "anthropic", "langchain_openai", "vercel_ai", ...
    api: str                                 # "chat.completions", "responses", "messages", "messages.batch",
                                             # "generate_text", "chat_model", "embeddings", "agent.run", ...
    kind: Literal["request", "client_config", "agent_run", "embedding"]
    callee: str                              # display text: "client.chat.completions.create"
    shape: "CallShape"                       # the catalog entry that matched (§5)
    match: Literal["resolved", "import_tail"]
    params: tuple[Param, ...]                # params found syntactically; use ctx.param() for the tri-state view
    spreads: tuple[Expr, ...]                # **kw / ...spread / request objects still to resolve
    config: int | None                       # id of the client_config LLMCall this request inherits from
    cache_markers: tuple[int, ...]           # CacheMarker ids inside its arguments
    loops: tuple[int, ...]                   # enclosing Loop ids, innermost first
    function: str | None
    result: Binding | None                   # where the response is stored
    result_reads: tuple[str, ...]            # attribute paths read from the result: "choices[].message", "usage.input_tokens"
```

The normalized parameter names (*contract*; the catalog maps raw names to these) are `model`, `max_output`,
`messages`, `system`, `input`, `tools`, `tool_choice`, `thinking`, `effort`, `n`, `stream`,
`stream_usage`, `retries`, `timeout`, `previous_response_id`, `prompt_cache_key`,
`prompt_cache_retention`, `response_format`, `temperature`, `service_tier`, `step_limit`, `store`. A new
name is added in the catalog together with a line here.

```python
class CacheMarker:
    id: int; span: Span
    kind: Literal["anthropic_cache_control", "bedrock_cache_point", "openai_prompt_cache_key",
                  "openai_prompt_cache_retention", "gemini_cached_content", "litellm_cache_control",
                  "langchain_cache", "vercel_provider_options"]
    ttl: Expr | None; key: Expr | None
    location: Literal["system", "tools", "messages", "content_block", "request", "unknown"]
    index_in_list: int | None; list_len: int | None     # position of the marked block in its list, if literal
    call: int | None                                    # LLMCall id it belongs to (lexically or by flow)

class Loop:
    id: int; span: Span; header: Span
    kind: Literal["while", "for", "for_in", "do_while", "loop", "goroutine_for"]
    infinite: bool                          # condition is a truthy Const or missing (while True, for(;;), loop {})
    iter_expr: Expr | None
    llm_calls: tuple[int, ...]              # LLMCall ids lexically inside (not inside nested functions)
    parent: int | None
    exits: tuple["LoopExit", ...]           # LoopExit(kind: break|return|raise|throw|goto, span, guard: Expr | None)
    counters: tuple[str, ...]               # names incremented in the body AND compared in a guard or the condition
    awaits_input: bool                      # input()/readline/recv/accept/queue.get/websocket receive in the body
    agent: bool                             # an LLM result flows into a MessageOp on a history that feeds the next call

class RetryPolicy:
    id: int; span: Span
    source: Literal["decorator", "client_option", "call_option", "loop", "framework", "sdk_default"]
    library: str                            # "tenacity", "backoff", "p-retry", "openai", "anthropic", "langchain", "instructor", ...
    max_attempts: Expr | None               # None = no stop condition found
    max_delay: Expr | None
    backoff: Literal["none", "fixed", "exponential", "unknown"]
    retry_on: tuple[str, ...]               # exception/status names; ("*",) = everything
    wraps: tuple[int, ...]                  # LLMCall ids retried
    sdk_default_attempts: int | None        # from the catalog when source == "sdk_default"
    checks_cancellation: bool | None

class UsageRead:
    id: int; span: Span
    raw_path: tuple[str, ...]               # ("usage", "prompt_tokens_details", "cached_tokens")
    field: Literal["input", "output", "cache_read", "cache_write", "reasoning", "total", "other"]
    provider_shape: Literal["openai_chat", "openai_responses", "anthropic", "gemini", "bedrock_converse",
                            "litellm", "vercel", "langchain", "unknown"]
    response: int | None                    # LLMCall id the base object flows from (by resolution), else None
    op: Literal["read", "assign", "accumulate", "sum", "price_multiply"]
    target: str | None                      # binding or member written
    in_stream_loop: bool
    function: str | None

class ToolDispatch:
    id: int; span: Span
    mechanism: Literal["dict_lookup", "if_chain", "match", "getattr", "framework"]
    name_expr: Expr
    handlers: tuple[str, ...]
    loop: int | None; call: int | None      # the loop it runs in, the LLMCall whose response it dispatches
    appends_result_to: str | None           # history binding the tool output is appended to
    output_capped: bool | None              # slice/truncate applied before the append; None = can't tell
    terminal_tools: tuple[str, ...]         # handlers followed by return/break

class MessageOp:
    id: int; span: Span
    target: str; target_expr: Expr
    op: Literal["append", "extend", "insert", "assign", "slice_assign", "clear", "pop", "truncate_call"]
    value: Expr | None
    loop: int | None; function: str | None
```

### 3.5 File and repo

```python
class Comment:   span: Span; text: str

class FileModel:
    path: str                               # repo-relative, "/" separators
    lang: Lang; family: Family
    source: bytes                           # kept for snippets and suppression; the tree is not kept
    imports: tuple[Import, ...]; exports: Mapping[str, Binding]
    functions: tuple[Function, ...]; bindings: tuple[Binding, ...]; calls: tuple[Call, ...]
    llm_calls: tuple[LLMCall, ...]; loops: tuple[Loop, ...]; templates: tuple[StringTemplate, ...]
    retry_policies: tuple[RetryPolicy, ...]; usage_reads: tuple[UsageRead, ...]
    cache_markers: tuple[CacheMarker, ...]; tool_dispatches: tuple[ToolDispatch, ...]
    message_ops: tuple[MessageOp, ...]
    comments: tuple[Comment, ...]
    error_spans: tuple[Span, ...]           # ERROR/MISSING regions; nothing is extracted inside them
    sdks_imported: frozenset[str]

class RepoModel:
    root: str
    files: Mapping[str, FileModel]          # every file parsed in this scan (candidates + lazily indexed)
    modules: "ModuleIndex"                  # module name -> path, per language
    sdks_seen: frozenset[str]
    vocab_hits: Mapping[str, frozenset[str]]   # vocabulary term -> paths, over ALL discovered files (bytes scan)
```

All ids are unique within a file and follow source order. Every tuple is sorted by `span.start_byte`.

---

## 4. Value resolution

`engine/values.py` resolves the IR lazily in pass 2, after the module index exists. It has three entry
points, which rules reach through `ctx`:

| Call | Semantics | Use |
|---|---|---|
| `ctx.resolve(x) -> Value` | **must**: the value on every path, else `Unknown` | "the cap is 100000" |
| `ctx.candidates(x) -> tuple[Candidate, ...]` | **may**: every value on some path, each with its span; an `Unknown` candidate means some path is unknown | "on this branch the cap is None" |
| `ctx.param(call, name) -> Param \| Absent \| Unknown` | tri-state presence of a normalized param across the call's own params, spreads and inherited `config` | "is a cap sent at all?" |

Here `x` is an `Expr`, a `Param` or a `ParamRef`.

### 4.1 What resolves

The resolver handles each expression form as follows:

- **Literals.** Strings, numbers, booleans and null-likes become `Const`. Concatenation of constants, `+ -
  * // / %` on numbers, string `*` int, and `??` / `or` / `||` with a null-like or falsy constant on the left
  all fold.
- **Names.** A name is found by lexical scope: block (JS `let`/`const`), function, enclosing functions,
  class (Python class-body rules), module, then imports.
  - One reaching definition: resolve its RHS.
  - Several: `resolve` gives `Unknown("branches")` unless they all resolve equal; `candidates` returns the
    union.
  - Reaching definitions are found by source order inside the scope. The last unconditional assignment
    before the use wins. Conditional assignments after it join as candidates.
  - An `if`/`elif`/`else`, `match` or `try`/`except` whose arms **all** assign the name replaces the
    earlier value. When some arm doesn't assign (including a missing `else`), the earlier value stays as a
    candidate.
  - Assignments inside a loop body join as candidates, with no fixed-point iteration.
- **Imports.** An import goes through the module index to the exported binding (one hop). A module that
  isn't in the repo gives `MemberRef(qualname="pkg.name")`, which is how SDK symbols are recognized.
  Relative imports, `__init__.py` re-exports, `index.ts` barrels, `export { x } from`, CommonJS
  `module.exports` / `exports.x` and `require()` are followed. Dynamic `import()`, `importlib`, and
  `__getattr__` modules give `Unknown("dynamic")`.
- **Attributes.** `a.b` resolves `a`.
  - If `a` is a `DictVal`, the entry `b` is used (JS objects; Python only for subscripts).
  - In a method, `self.x` / `this.x` resolves through the class member index (assignments to `self.x` in any
    method of the class, with the same branch rules as names).
  - Otherwise the result is `MemberRef(base, attr)`.
- **Subscripts.** `d["k"]` with a constant key on a `DictVal` gives the entry. `l[i]` with a constant int on
  a `ListVal` gives the item. Anything else gives `Unknown("dynamic")`.
- **Calls.**
  - `dict(...)`, `{**a, **b}`, `{...a, k: v}`, `Object.assign({}, a, b)` and `a | b` (dicts) merge into a
    `DictVal`.
  - The env readers listed under `EnvVar` produce an `EnvVar`.
  - `int()`, `float()`, `str()`, `Number()` and `String()` of a constant fold.
  - A call to an in-repo function whose `returns` all resolve equal gives that value, with the parameters
    bound to this call's arguments (one hop).
  - Anything else gives a symbolic `CallResult`.
- **Templates.** f-strings, template literals, `.format`, `%`, `+` with non-constant parts, `fmt.Sprintf`,
  `String.format` and C# `$"..."` become `Template(StringTemplate)`.
- **Ternaries.** `a if c else b` and `c ? a : b`: `resolve` gives `Unknown("branches")` unless both sides
  are equal; `candidates` returns both.
- **Containers.** Dict and object literals become `DictVal`, list and array literals `ListVal`, and Go/Rust/C#
  struct literals and object initializers `DictVal` keyed by field name.

### 4.2 Container mutation (closed or open)

A `DictVal` keeps the key changes made to its binding in the same scope, after creation and before the use,
in source order:

- `d["k"] = v` with a constant key, and `d.k = v` in JS: the entry is set. Branches follow the same arm
  rule as names (§4.1): if every arm stores the key, the previous value (or the previous `Absent`) is
  replaced; otherwise the previous value stays as a candidate next to the new one.
- `d.update({...})`, `Object.assign(d, {...})` and `d |= {...}` with a resolvable mapping: merge.
- `del d["k"]`, `d.pop("k")` and `delete d.k`: the entry is removed. Inside a branch, `Absent` becomes a
  candidate.
- Passing `d` as an argument to any call except a known non-mutating one makes it `closed=False`
  (`Unknown("mutated")`). The non-mutating calls are `print`, `logging.*`, `console.*`, `len`, `str`,
  `repr`, `json.dumps`, `JSON.stringify`, `copy`, `dict`, and `deepcopy`.
- A `**kw` or `...spread` whose source resolves to a closed `DictVal` keeps the merged dict closed. An
  unresolved spread makes it open.

`ctx.param(call, name)` returns:

1. `Param` if any of the call's own params, any spread that resolves, or the inherited `config` call has it;
2. `Absent` if none has it **and** every spread resolved to a closed `DictVal` **and** the config chain is
   closed;
3. `Unknown("open")` otherwise.

### 4.3 Parameters (one hop)

`candidates(ParamRef)` uses the module index to find the in-repo call sites of the function: direct calls,
calls through an imported name, and method calls on a receiver that resolves to the class.

- For each call site, the candidate is the matching argument (positional or keyword), resolved one more
  level, or the `default` when the site omits it. `Candidate.via` is the call site's span.
- If there are no in-repo call sites, or the function also escapes (it is passed as a value, registered as a
  callback or route handler, or decorated with a framework decorator), the candidates include
  `Unknown("external")`.
- `*args` and `**kwargs` parameters always give `Unknown("external")`.

### 4.4 Limits

- **Depth ≤ 4 hops** (*contract*). One hop is a name to its binding, an import to its module, a call to its
  return, a parameter to its call site, or `self.x` to its member. At depth 5 the result is
  `Unknown("depth")`.
- **Memoization.** Results are memoized per `(expr id, binding environment)` for the whole scan.
- **Cycle guard.** An in-progress set gives `Unknown("cycle")`.
- **No interprocedural mutation.** No heap model and no type inference beyond the constructors in the
  module index (`client = OpenAI()`), plus TS annotations that name an SDK type (`client: OpenAI`).
- **Lazy indexing.** The module index parses a non-candidate file lazily, the first time resolution crosses
  into it. At most `MAX_LAZY_FILES = 2000` files are parsed per scan; after that, `Unknown("depth")`.
- **Parse errors.** Expressions that overlap an `error_spans` region give `Unknown("parse_error")`.

### 4.5 `Unknown` never reports (*contract*)

A rule reports only on resolved facts:

- A `Const` that breaks the practice.
- An `Absent` from a **closed** container, for rules about something missing.
- A `Candidate` whose value is a concrete violation. Other candidates of the same expression may be
  `Unknown`, because the reported path is still definite.

A rule never reports because something is `Unknown`, `MemberRef`, `CallResult`, `EnvVar` or `ParamRef`.
Environment variables count as configured. A rule that thinks it needs to report on `Unknown` is wrong, or
it belongs in Vernia Reason.

---

## 5. SDK catalog and models.toml

### 5.1 Call shapes

`sdk/catalog.py` (*contract* for field names):

```python
class CallShape:
    sdk: str; api: str
    kind: Literal["request", "client_config", "agent_run", "embedding"]
    families: tuple[Family, ...]
    imports: tuple[str, ...]                 # module roots that make the shape eligible ("openai", "@ai-sdk/*", "ai")
    callee: tuple[str, ...]                  # resolved qualname patterns, "()" = instance:
                                             # "openai.OpenAI().chat.completions.create", "openai.AsyncOpenAI().chat.completions.create"
    tail: tuple[str, ...] = ()               # attribute tails accepted when the receiver is Unknown AND the
                                             # file imports one of `imports` ("chat.completions.create"); () = resolved only
    args: Literal["kwargs", "object0", "object1", "builder", "struct", "positional"]
    params: Mapping[str, tuple[str, ...]]    # normalized -> raw names, preferred wire name first:
                                             # {"max_output": ("max_completion_tokens", "max_tokens"), "model": ("model",)}
    nested: Mapping[str, tuple[str, ...]] = {}   # dotted paths that also carry a param:
                                             # {"max_output": ("extra_body.max_tokens", "model_kwargs.max_tokens")}
    generates: bool = True                   # False for embeddings, moderation, count_tokens
    cap_required: bool = False               # the API rejects a request without a cap (Anthropic Messages)
    default_retries: int | None = None       # SDK default, used for RetryPolicy(source="sdk_default")
    inherits: Literal["receiver", "model_arg", None] = None   # where `config` comes from (LangChain .invoke ->
                                             # its constructor; Vercel generateText -> the `model` arg's middleware)
    sources: tuple[Source, ...] = ()         # doc URL + verified date for every non-obvious field

class ClientShape:                           # constructors whose options configure later calls
    sdk: str; callee: tuple[str, ...]; params: Mapping[str, tuple[str, ...]]
```

**Matching** (*contract*; this rule is what keeps name-based false positives out):

1. If the callee's resolved qualname matches a `callee` pattern, the call is an `LLMCall` with
   `match="resolved"`.
2. Otherwise, if the shape has a `tail`, the callee text ends with it, the receiver resolves to `Unknown` or
   `MemberRef` (not to some other known value), **and** the file imports a module in `imports`, the call is
   an `LLMCall` with `match="import_tail"`.
3. Otherwise the call is not an LLM call. Nothing is ever matched by name alone. `.invoke(`, `.create(`
   and `.generate(` have no tail: they match only when resolved.

Default imports and CommonJS `require()` bind the module's default export, with qualname
`<module>.default`. A shape lists both `openai.OpenAI()` and `openai.default()` when the package's default
export is the client class (as it is for `openai` and `@anthropic-ai/sdk` in JS).

### 5.2 models.toml

`sdk/data/models.toml`, read with `tomllib` and validated at load:

```toml
[[model]]
id = "claude-haiku-4-5-20251001"
aliases = ["claude-haiku-4-5"]
provider = "anthropic"
tier = "small"                        # small | mid | frontier
max_output_tokens = 64000
status = "active"                     # active | deprecated | retired
source = "https://platform.claude.com/docs/en/build-with-claude/thinking#output-limits"
verified = 2026-10-25
# also: input_usd_per_mtok, output_usd_per_mtok, cache_read_usd_per_mtok, cache_write_usd_per_mtok,
#       context_window, cache_min_tokens, thinking_modes, effort_levels, budget_range, reasoning,
#       sampling_params_allowed, long_context_threshold, batch_extended_output, deprecated_on, retired_on,
#       successor, api_output_cap_param
```

The example shows the shape only. Every number is copied from its `source` on the `verified` date; never
write one from memory. `ModelCatalog.get(model_id, provider=None) -> ModelInfo | None` normalizes provider
prefixes (`anthropic/`, `openai/`, Bedrock `anthropic.` / `us.anthropic.` and a `-v1:0` suffix, Vertex
`@date`) and aliases. An unknown id returns `None`, and rules stay silent. CI warns when an entry's
`verified` date is more than 60 days old.

---

## 6. Rule API

### 6.1 Rule classes and hooks

```python
from spendlint.engine.registry import rule, Source
from spendlint.engine.visitor import FileRule, RepoRule
from spendlint.engine.options import Opt
from spendlint.engine.context import FileContext, RepoContext
from spendlint.engine.model import LLMCall, Const, Absent, Unknown, Param   # ...
```

| Hook | Called with | Pass |
|---|---|---|
| `visit_file(file: FileModel, ctx)` / `leave_file(file, ctx)` | each candidate file | 2 |
| `visit_llm_call(call: LLMCall, ctx)` | every LLMCall | 2 |
| `visit_loop(loop: Loop, ctx)` | every Loop | 2 |
| `visit_template(t: StringTemplate, ctx)` | every StringTemplate | 2 |
| `visit_retry_policy(p: RetryPolicy, ctx)` | every RetryPolicy | 2 |
| `visit_usage_read(u: UsageRead, ctx)` | every UsageRead | 2 |
| `visit_cache_marker(m: CacheMarker, ctx)` | every CacheMarker | 2 |
| `visit_tool_dispatch(d: ToolDispatch, ctx)` | every ToolDispatch | 2 |
| `visit_message_op(op: MessageOp, ctx)` | every MessageOp | 2 |
| `visit_function(f: Function, ctx)` | every Function | 2 |
| `visit_call(c: Call, ctx)` | generic calls matching the rule's `calls=` patterns | 2 |
| `visit_capture(name: str, node: CaptureNode, ctx: CaptureContext)` | escape hatch: captures of the rule's `queries=` | 1 |
| `RepoRule.finish(ctx: RepoContext)` | once, after every file | finish |

- **Dispatch.** The registry records which hooks each rule overrides and dispatches only those. Within a
  file, `visit_file` comes first, then all model objects of every kind merged in source order
  (`span.start_byte`, ties by kind in the table order), then `leave_file`. Files are visited in sorted path
  order. The order is deterministic (*contract*).
- **Rule state.** Each rule is instantiated once per scan with its validated options. A `FileRule` must not
  keep state across files; reset it in `visit_file` if needed. A `RepoRule` may accumulate state, and it
  reports from `finish` (R-CACHE-001 "Anthropic calls but no cache marker anywhere" moves here).
- **The escape hatch.** A rule declares `queries={"python": "queries/r_ctx_008.scm"}` (paths relative to the
  rule module). The engine compiles each query once per process and runs it in pass 1 while the tree is
  alive. `CaptureNode` exposes `type`, `text`, `span` and `child_by_field(name)`, and it is valid only
  during the call; never store it. `CaptureContext.resolve` resolves file-locally, so cross-module names are
  `Unknown`. Use the hatch only when no model object covers the syntax, and open a ticket to model it.

### 6.2 Context

`FileContext` (pass 2):

- `ctx.file`: the `FileModel`.
- `ctx.repo`: the `RepoModel` (read-only).
- `ctx.options`: options with defaults merged.
- `ctx.catalog` and `ctx.models`: the SDK catalog and `ModelCatalog`.
- `ctx.resolve(x)`, `ctx.candidates(x)`, `ctx.param(call, name)`: §4.
- `ctx.flows_to(template) -> frozenset[str]`: the normalized params a `StringTemplate` reaches, for example
  `{"system"}` or `{"messages"}`. It is precomputed per file by resolving every LLMCall param once.
- `ctx.llm_call(id)`, `ctx.loop(id)`: id lookups.
- `ctx.text(span) -> str`.
- `ctx.report(at, detail, *, end=None, confidence=1.0)`:
  - `at` is a `Span` or any model object (its `anchor`, or else its `span`, is used).
  - `detail` is one line of plain text that names the concrete fact ("`max_tokens` is None").
  - Reports are de-duplicated per `(rule, span)`.

`RepoContext` (`finish`) has the same methods, plus `report(at, detail, *, path)`.

### 6.3 `@rule` metadata (*contract*)

```python
@rule(
    id="R-OUT-001",                     # ^R-[A-Z]{2,8}-\d{3}$, unique; cat dir = middle segment lower-cased
    title="Generation call without an output-token cap",   # <= 80 chars, no newline; = finding rule_name
    practice="Every generation call sets an explicit output-token cap; ...",   # ONE practice, one sentence
    why="Output tokens are the most expensive tokens; ...",  # 1-2 sentences on the cost impact (docs page)
    fix="Pass an explicit cap: max_completion_tokens= ...",  # the recommended action (annotation, SARIF help)
    category="out",                     # one of the 16 categories in section 2
    severity="info",                    # default level: "warning" (definite waste / API error) | "info" (advisory)
    status="stable",                    # "stable" | "experimental" (off by default) | "deprecated"
    languages=("python", "typescript", "javascript"),      # Family values; RuleTester needs >=1 case each
    sdks=("openai", "anthropic", "langchain_openai", "vercel_ai"),   # catalog ids the rule understands
    sources=(Source(url="https://...", verified="2026-10-25", rows=("ANTH-OUT-01",)),),
    golden=("acg-assafelovic-gpt-researcher-5c1ce388",),   # golden datapoint ids; sources or golden required
    options={"max": Opt(int, default=None, minimum=1, help="...")},
    calls=(),                           # qualname/tail patterns that feed visit_call (prefilter for Call extraction)
    queries={},                         # {Family: "queries/x.scm"} for visit_capture
    vocabulary=(),                      # extra prefilter terms (bytes); SDK vocabulary comes from `sdks`
    introduced="0.1.0",
    replaced_by=None,                   # deprecated rules: the id that supersedes them
    legacy_category=None,               # the 10 rules shipped in 0.1.x keep their old category string
    tier="P0",                          # P0 | P1 | P2, from practices.json
)
class OutputCapMissing(FileRule): ...
```

- **Severity reflects impact, not certainty.** Stable rules report `confidence=1.0` (§16.2 covers the
  Monday parity exception).
- **Experimental rules** are off unless the `rules:` config enables them, or `--rules` / the Action's
  `rules` input names them. They may report `confidence < 1.0`.
- **`meta.RULES` is derived** from the registry, so `cli`, `report` and `telemetry` keep working unchanged.
  The legacy fields map as follows: `name` = `title`, `category` = `legacy_category or category`,
  `severity`, `fp_risk` = `"low"` for stable and `"medium"` for experimental, `evidence` = `len(golden)`,
  `action` = `fix`.
- **Discovery.** `registry.load()` walks `spendlint.rules` with `pkgutil.walk_packages`, imports every
  `r_*.py`, and fails at import on a duplicate id, an id that doesn't match its module path, or invalid
  metadata.

### 6.4 Options (`engine/options.py`)

`Opt(type, default, help, minimum=None, maximum=None, choices=None, items=None)`:

- `type` is one of `int`, `float`, `str`, `bool` or `list`.
- `choices` makes an enum.
- `items` gives the element type of a list.

Unknown option keys, wrong types and out-of-range values are `ConfigError`s that name the rule and the
option. Options are documented on the generated rule page.

### 6.5 Findings

The legacy finding dict keeps every existing key (`rule_id path line end_line detail confidence snippet
rule_name severity category fp_risk dataset_evidence recommended_action`) and gains `col`, `end_col`,
`help_uri` and `fingerprint` (additive, *contract*). `severity` is the effective level after the `rules:`
config.

---

## 7. RuleTester

`spendlint/testing/rule_tester.py` (*contract*):

```python
from spendlint.testing.rule_tester import RuleTester, py, ts, tsx, js, go, java, rust, cs, err

RuleTester(OutputCapMissing).run(
    valid=[py("..."), ts("...")],
    invalid=[py("...", errors=[err(line=3, detail_contains="no output cap")])],
)
```

**Case helpers.** `py(src, *, errors=(), options=None, filename=None, files=None, name=None,
example=False)`, and the same for every language.

- `src` is dedented (`textwrap.dedent`) and one leading newline is stripped, so line 1 is the first line
  after the opening `"""`.
- `filename` defaults to `case.<ext>`, placed at `app/` inside a temporary root. Discovery skips test
  directories, so it is never under `tests/`.
- `files={"app/llm.py": "...", ...}` adds more files, for cross-module resolution and `RepoRule`s. `src`
  is then the file under test, named by `filename`.
- `options` is validated exactly as the `rules:` config validates it.
- `example=True` marks the case for the generated docs page (at most one valid and one invalid per
  language).

**`err(line, *, col=None, end_line=None, detail_contains=None)`.** Each expected finding gives an exact
`line`, plus optional `col`, `end_line` and a substring of `detail`.

**Semantics.**

- Each case runs through the real pipeline: discover, parse, extract, the module index, the rule, and then
  suppression. Config defaults and the diff filter are skipped. Only the rule under test runs, even when it
  is experimental.
- A valid case must produce zero findings from the rule.
- An invalid case must produce exactly the listed findings, compared by line (and by `col`/`end_line` when
  given) as a sorted multiset. Every finding must also match `detail_contains`.
- A case whose source has a parse error fails, unless it sets `allow_parse_error=True`.
- A failure raises `AssertionError` with the case name, the source with line numbers, and the expected and
  actual findings.

**Layout and contract test.**

- Tests live in `tests/rules/<cat>/test_r_<cat>_<nnn>.py`, with module-level `VALID` and `INVALID` lists
  and a `test_rule()` that calls `RuleTester(Rule).run(valid=VALID, invalid=INVALID)`.
- `tests/test_rule_contract.py` imports every rule and its test module, and checks:
  - a unique id that matches the module path;
  - a non-empty `practice`, `why` and `fix`;
  - `sources` (each with a URL and a verified ISO date) or `golden` ids;
  - an options schema (`{}` counts);
  - ≥3 `VALID` and ≥3 `INVALID` cases, with ≥1 valid and ≥1 invalid per declared language;
  - every golden id exists in the private dataset export (`tests/data/golden_ids.txt`);
  - `tools/gendocs.py --check` is clean, so `docs/rules/<id>.md` is up to date.

---

## 8. Pipeline

`engine/run.py`. `api.scan()` and the `scan.scan_repo()` shim both end up here.

1. **Discover** (`discover.py`). Walk each root with the existing `SKIP_DIR` / `SKIP_FILE` regexes, which
   are unchanged for py/ts/js. New languages add only their own test-file patterns: `_test.go`,
   `*Test.java`, `*Tests.java`, `*Tests.cs`, and Rust `#[cfg(test)]` modules, which are skipped at
   extraction.
   - Two fixes: relpaths are computed with `relative_to` (today's `lstrip("./")` also strips a leading dot
     from `.github`-style directories), and a **file given as the root** is scanned with its file name as
     the relpath.
   - Files over `MAX_BYTES` count in `skipped_large`.
   - In diff mode, a file outside the diff is skipped before reading, as today. Matches for
     `ignore_paths` count in `ignored_files`, as today.
2. **Read and prefilter.** Read the bytes. Invalid UTF-8 increments `decode_errors`, and the file is still
   parsed (tree-sitter takes bytes; snippets decode with `errors="replace"`). One compiled bytes regex
   holds the union of the SDK vocabulary (catalog `imports` and `tail` terms) and every enabled rule's
   `vocabulary`. A file that hits it is a **candidate**. Every file read still counts in `files`, `py`,
   `ts`, etc. All discovered files also feed `RepoModel.vocab_hits` (a bytes scan with no parse, so
   repo-wide facts stay correct in diff mode).
3. **Pass 1: parse and extract** (candidates only).
   - `parse.py` loads the grammar lazily (ABI checked) and reuses one `Parser` per language.
   - `tree.root_node.has_error` increments `parse_error_files`. ERROR and MISSING regions go into
     `error_spans`, and extractors skip them.
   - The language extractor builds the `FileModel` and the IR, and records exports.
   - `visit_capture` rules run.
   - The comment table is built from comment nodes.
   - **The tree is dropped.**
4. **Module index** (`symbols.py`). Map module names to paths for every candidate, and parse other files
   lazily on first use (§4.4). In diff mode, and for `ignore_paths`, files outside the reported set can
   still be indexed for resolution. Nothing is reported in them.
5. **Pass 2: rules.** For each candidate file in sorted path order, dispatch the `FileRule` and `RepoRule`
   hooks (§6.1). An exception in a rule for a file is caught, increments `rule_errors`, and drops that
   rule's findings for that file.
6. **Finish.** Call `RepoRule.finish()` once.
7. **Post-process, in this order** (it is today's order, so the `suppressed` count is unchanged;
   *contract*):
   1. rule selection and levels (`--rules`, `disable`, `rules:` off; level overrides set `severity`);
   2. the diff filter: keep a finding if any line in `[line, end_line]` was added;
   3. inline suppression (comment-node aware, §10.4), counted in `suppressed`;
   4. legacy enrichment (rule_name, category, ...);
   5. the sort by `(-confidence, rule_id, path, line, col)`.

   `--min-confidence` is applied afterwards in `cli.run_scan`, as today.

---

## 9. Error reporting

- **Stats.** `stats` keeps `files py ts py_parse_errors suppressed ignored_files`. `py_parse_errors` is
  now the number of Python files with ERROR nodes. It also gains:
  - `parse_error_files`, `skipped_large`, `decode_errors`, `rule_errors`;
  - `go`, `java`, `rust`, `csharp`;
  - `grammar_errors` (files whose grammar failed to load or failed the ABI check).

  `ts` stays the TypeScript plus JavaScript count.
- **Shown only when nonzero**, so every asserted string is unchanged on clean input (*contract*):
  - Text summary line, appended after the existing `; N suppressed inline`: `; N go`, `; N java`,
    `; N rust`, `; N csharp`, `; N file(s) with parse errors`, `; N skipped over 400 KB`,
    `; N file(s) not UTF-8`, `; N rule error(s)`.
  - Markdown summary: the other languages go inside the existing parentheses (`(1 Python, 2
    TypeScript/JavaScript, 3 Go)`), and one more line is added: `N file(s) had parse errors; findings
    there cover the parsed parts only.`
  - stderr, once per run: `spendlint: warning: rule R-X-NNN failed on N file(s) (first: <path>:
    <ExceptionType>)` and `spendlint: warning: grammar <lang> unavailable (<reason>); N file(s) not
    scanned`.
- **Exit codes don't change.** Parse, decode, rule and grammar errors never change the exit code.
  `SPENDLINT_DEBUG=1` re-raises the first rule or extractor exception, which `cli.main` turns into
  `spendlint: internal error: ...` and exit 2.
- **`spendlint --selfcheck`** is intercepted in `cli.main` before argparse, like `telemetry` and `action`.
  It prints the following and exits 0 when everything loads, 2 otherwise:
  ```
  spendlint 0.2.0 selfcheck
  python 3.11.5 win32/AMD64
  tree-sitter 0.26.0 (ABI 13..15)
    ok  python      tree-sitter-python 0.25.0      ABI 15
    ok  typescript  tree-sitter-typescript 0.23.2  ABI 14
    ...
  rules: 10 registered (10 stable, 0 experimental, 0 deprecated)
  models.toml: 42 models, oldest verified 2026-10-25
  ```

---

## 10. Compatibility contract

Checked in CI on every release. A change here is a breaking change.

### 10.1 CLI and exit codes

- **Unchanged.** `spendlint [scan] PATH [--rules] [--min-confidence] [--config | --no-config] [--diff |
  --diff-base] [--format text|jsonl|sarif|github] [--out] [--sarif] [--summary-md] [--fail-on
  none|info|warning]`, `spendlint action`, `spendlint telemetry status|show|on|off`, `spendlint
  --version`.
- **New.** `spendlint --selfcheck`.
- **Exit codes.** 0 clean, 1 findings at or above `--fail-on`, 2 usage/config/git/internal error, 130
  interrupted. `spendlint` with no arguments still exits 2.

### 10.2 Exact strings asserted in `spendlint/test_cli.py` (must stay byte-identical)

| Where | String |
|---|---|
| `--version` | `spendlint {__version__}` |
| bad config stderr | contains `unknown rule id` |
| text summary | `-- {n} finding(s); scanned {files} files ({py} py, {ts} ts)` + optional `; {n} suppressed inline` (the test checks `2 finding(s)`) |
| github format | `::warning ` / `::notice ` prefix; properties exactly `file={path},line={line},endLine={end},title={rule_id} {rule_name}` (escaped with `%3A`/`%2C`, no raw `:` or `,` after the level); message `{detail} (confidence {c}). {recommended_action}` |
| step summary | starts with `## spendlint` (appended after prior content); `**{n} finding(s)**`; per-rule row `\| {rule_id} \|`; finding row ``\| {severity} \| {rule_id} \| `{path}:{line}` \|``; clean run `No cost findings.` |
| SARIF | `runs[0].tool.driver.version == __version__`; one result per finding; driver name `spendlint` |
| diff notes | `lines added since the merge base with {base}`, `lines added by the pull request merge commit` |
| action | `spendlint: {n} finding(s) in {files} file(s); {note}` [`; config {name}`]; `::warning title=spendlint::Could not compute the pull request diff ({e}); scanning all files instead.`; note `full scan (pull request diff unavailable)`; `::error title=spendlint::{msg}`; outputs `findings-count`, `sarif-path` |
| jsonl | one JSON object per line with at least `rule_id`, `path`, `line` |
| importable | `spendlint.scan.{scan_repo, suppressed_rules, parse_diff, to_sarif}`, `spendlint.gitdiff.added_lines`, `spendlint.config.{ConfigError, load_config, parse_simple_yaml}`, `spendlint.cli.main`, `spendlint.action.main`, `spendlint.meta.RULES` |

The test fixtures (`SRC` and the per-test sources) are not part of the contract; the strings above are.
See §16.3.

### 10.3 Config

- **Unchanged keys, still strictly validated:** `disable` (list or comma string of known ids),
  `ignore_paths` (globs; a trailing `/` means a directory), `min_confidence` (0..1). An unknown top-level
  key is a `ConfigError`.
- **New key `rules:`.** A mapping of rule id to either a level (`off | info | warning`) or `{level,
  options}`.

  ```yaml
  rules:
    R-OUT-001: warning
    R-LOOP-001: {level: warning, options: {max_iterations: 50}}
    R-CACHE-016: info          # turns on an experimental rule
  ```

  - An unknown id, an unknown level, or an unknown or invalid option is a `ConfigError` (exit 2).
  - `disable: [X]` is the same as `rules: {X: off}`. Naming an id in `disable` and also setting it to a
    non-off level is a `ConfigError`.
  - Precedence: `--rules` / the `rules` input selects the set to run (experimental rules included when
    named). Then `disable` and `off` remove rules. Then levels set `severity`, which drives `--fail-on`,
    annotations and SARIF.
  - The built-in YAML reader (no PyYAML) learns exactly one nested form: `rules:` followed by indented
    `R-ID: level` lines. The `{level, options}` form needs PyYAML or `.spendlint.json`; otherwise the
    existing `line N: unsupported YAML (install PyYAML or use .spendlint.json)` error is raised.
    `nested:\n  key: value` under any other key still raises `ConfigError`, as `test_simple_yaml_reader`
    asserts.

### 10.4 Suppression (syntax unchanged)

- **Syntax.** `# spendlint: ignore R-ID[, R-ID ...] reason` (or `//`, or `/* */`) on the finding's first
  line, or on a comment-only line directly above it. A bare `spendlint: ignore` suppresses nothing. Ids are
  parsed until the first token that is not an id, and the rest is the reason.
- **What changes.** The comment must be a real comment node (`comment`, `line_comment`, `block_comment`),
  so `"# spendlint: ignore R-X"` inside a string literal no longer suppresses. That case can't occur in
  any shipped test.
- **Kept.** `suppressed_rules(line_text)` stays importable from `spendlint.scan` with its current behavior.

### 10.5 SARIF 2.1.0 additions (additive only)

- **Kept as is.** `$schema`, `version`, driver `name="spendlint"` and `version`, the per-rule `id`,
  `name`, `shortDescription`, `help.text` and `properties.{category, falsePositiveRisk, datasetEvidence}`,
  the result `ruleId`, the `level` map (`warning`→`warning`, `info`→`note`), `message.text = detail + " - "
  + recommended_action`, and `region.startLine` / `endLine`.
- **Added.**
  - `run.columnKind = "unicodeCodePoints"`.
  - Per rule: `fullDescription.text` = the practice, `helpUri`, `defaultConfiguration.level`,
    `properties.{practice, status, tier}`.
  - Per result: `region.startColumn` and `endColumn`, `ruleIndex`, `properties.confidence`, and
    `partialFingerprints["spendlint/v1"]`.
- **`helpUri`** is `https://github.com/vernialabs/spendlint/blob/v{version}/docs/rules/{id}.md`.
- **The fingerprint** is the first 32 hex characters of `sha256(rule_id \0 path \0
  whitespace-normalized text of the span's first line \0 occurrence index of that triple in the file)`.
  It stays stable when lines shift.

### 10.6 Action

- Inputs (`path rules min-confidence fail-on diff-mode diff-base config sarif-output upload-sarif
  telemetry`), outputs (`findings-count sarif-path exit-code`) and diff-mode behavior are unchanged.
- `action/install.sh` checks Python ≥3.11, installs the hashed `action/requirements.txt` with
  `--require-hashes`, then installs spendlint with `--no-deps`.
- The `uses: ./` CI job must pass.

### 10.7 Telemetry

- **Counts only.** `findings_by_rule` keys must be registered ids, and every registered id must match
  `^R-[A-Z]{2,8}-\d{3}$` (the registry enforces this).
- **v1 is unchanged until the collector is deployed** (Thursday): `files_scanned` is exactly `{python,
  typescript_javascript}` from `stats.py` / `stats.ts`, and other languages are not reported.
- **v2** adds buckets `{python, typescript, javascript, go, java, rust, csharp}` and `parse_error_files`.
- **No new field reaches an event without a schema version bump** and a `docs/telemetry.md` edit. Stats
  such as `rule_errors` stay local.

---

## 11. Packaging

```toml
[project]
requires-python = ">=3.11"
dependencies = [
  "tree-sitter>=0.26,<0.27",
  "tree-sitter-python==0.25.0",
  "tree-sitter-javascript==0.25.0",
  "tree-sitter-typescript==0.23.2",
  "tree-sitter-go==0.25.0",
  "tree-sitter-java==0.23.5",
  "tree-sitter-rust==0.24.2",
  "tree-sitter-c-sharp==0.23.5",
]
[project.optional-dependencies]
yaml = ["PyYAML>=5.1"]

[tool.setuptools.packages.find]
include = ["spendlint*"]

[tool.setuptools.package-data]
"*" = ["py.typed", "queries/*.scm", "data/*.toml"]
```

- The spendlint wheel stays pure Python (`py3-none-any`). The native code comes from the grammar wheels.
  No `[core]` extras and no language pack.
- **The CI wheel check** builds the wheel and asserts:
  - it contains `spendlint/py.typed`, `spendlint/sdk/data/models.toml`, and at least one
    `spendlint/lang/*/queries/*.scm` per shipped language;
  - it contains no `tests/` and no `scripts/`;
  - `pip install dist/*.whl` into a clean venv followed by `spendlint --selfcheck` exits 0.
- `tests/` lives outside the package, and `SKIP_DIR` already skips it during scans.
- Rule modules, queries and `models.toml` are found with `importlib.resources`, never `__file__`
  arithmetic. That keeps zipapps working.

---

## 12. Performance budget

- **Budget** (*contract*, measured on ubuntu-latest):
  - a 5k-file mid-size corpus in ≤15 s;
  - 30k files in ≤60 s (measured Saturday);
  - ≤20% regression per release, tracked in the metrics row.
- **Day-0 measurement** (CPython 3.11, win_amd64, a 25.6 KB Python file):

  | Operation | Time |
  |---|---|
  | tree-sitter parse | 5.9 ms |
  | stdlib `ast.parse` | 6.6 ms |
  | one query with captures | 2.0 ms |
  | full `TreeCursor` walk (8k nodes) | 1.3 ms |

  Parsing costs about the same as today. The budget is met by not parsing what can't matter.
- **How it is met:**
  - **Prefilter** (§8 step 2): most files in a repo never mention an LLM SDK, and they cost a read and a
    regex.
  - **Lazy index**: non-candidates are parsed only when resolution crosses into them.
  - **Caches**: one `Parser` per language per process, and each `Query` compiled once per process.
  - **Queries over walks**: extraction prefers a few combined queries per language to Python-level walks,
    because creating Node objects is the main cost.
  - **Lazy resolution** with a memo for the whole scan.
  - **Trees dropped** after pass 1.
  - `QueryCursor(match_limit=...)` on every cursor.
- **Allowances per stage** (5k corpus): discovery and read ≤2 s, pass 1 ≤8 s, index and pass 2 ≤4 s, post
  ≤1 s.
- **Perf smoke check.** `scripts/perf_smoke.py` scans a pinned corpus in CI and fails above 1.2× the
  recorded baseline.

---

## 13. Adding a language

1. **Grammar.** Add the pin to `pyproject.toml`, `action/requirements.txt` (hashed) and the probe's
   `GRAMMARS`. Run `python scripts/dev/probe_grammars.py` in a clean venv and record the version, ABI and
   wheel tags in §1.
2. **Registry.** In `engine/parse.py`, add `GrammarSpec(lang, family, distribution, module, entry)`, the
   extensions, and the comment node types. In `discover.py`, add its test-file patterns.
3. **Extractor.** Add `lang/<x>/extractor.py`, a class implementing
   `Extractor.extract(tree, source: bytes, path: str) -> tuple[FileModel, Exports]` with the helpers in
   `engine/extract.py`. It must:
   - map scopes and bindings, with the language's block-scoping rules;
   - map imports to module names, plus the module-name rule for the index (Go import path, Java package +
     class, Rust `mod`/`use`, C# namespace + `using`);
   - emit the IR for literals, names, attributes, calls, containers, templates and env reads;
   - emit every domain object of §3.4 the language can express, and skip `error_spans`.

   Queries go in `lang/<x>/queries/*.scm`, with a header comment listing the capture names.
4. **Tests.** Add the RuleTester helper (`go()` and so on), an extractor golden test
   (`tests/lang/<x>/test_extract.py`: source → expected model objects), and selfcheck output.
5. **Docs and telemetry.** Update the stats key and the text/Markdown summary tail (§9), the telemetry v2
   bucket, the USAGE.md language table, and the wheel matrix.
6. **Rules.** Rules opt in by adding the family to `languages` and adding cases. A rule never runs on a
   language it does not declare.

---

## 14. Adding an SDK

1. Read the SDK's docs and source for the call surface. Record the URL and the verified date in
   `Source(...)` on the shape.
2. Add `sdk/<name>.py` with its `CallShape`s and `ClientShape`s:
   - `imports`, resolved `callee` patterns, and a `tail` only if the tail is unambiguous for that import;
   - the `params` map from raw names to normalized names, plus `nested` paths;
   - `cap_required`, `default_retries` and `inherits`.

   Register it in `sdk/catalog.py`.
3. Add the SDK vocabulary for the prefilter (import roots and tails), which is derived automatically from
   the shapes.
4. Add catalog tests: one source per shape asserting the `LLMCall` fields (`tests/sdk/test_<name>.py`).
   Add a negative test proving a same-named method without the import is not an `LLMCall`.
5. Add the SDK id to the `sdks=` of the rules that should understand it, with RuleTester cases for it.
6. Add `models.toml` entries for new model ids it introduces, each with a source and a verified date.

---

## 15. Worked example: R-OUT-001

`practices.json` (the source of truth) defines R-OUT-001 as "Every generation call sets an explicit
output-token cap; it is not omitted, set to None, or dropped on a model-family branch." The "within the
model maximum" half of the Tuesday item (and of row ANTH-OUT-01) is its own practice, **R-OUT-005**,
because one rule enforces one practice. The companion rule after this one shows that half and how
`models.toml` is used.

`spendlint/rules/out/r_out_001.py`:

```python
"""R-OUT-001: every generation call sets an explicit output-token cap."""
from spendlint.engine.context import FileContext
from spendlint.engine.model import Absent, Const, LLMCall, Param, Unknown
from spendlint.engine.options import Opt
from spendlint.engine.registry import Source, rule
from spendlint.engine.visitor import FileRule


@rule(
    id="R-OUT-001",
    title="Generation call without an output-token cap",
    practice="Every generation call sets an explicit output-token cap; it is not omitted, set to None, "
             "or dropped on a model-family branch.",
    why="Without a cap the provider default applies (often the model maximum), so one runaway or "
        "looping generation, including reasoning tokens, is billed in full.",
    fix="Pass an explicit cap on the call or on the client/model object it uses: max_completion_tokens "
        "(OpenAI chat), max_output_tokens (Responses), max_tokens (Anthropic, LangChain), maxOutputTokens "
        "(Vercel AI SDK). Size it to the expected answer plus reasoning headroom.",
    category="out",
    severity="info",
    status="stable",
    languages=("python", "typescript", "javascript"),       # go/java/rust/csharp variants land Thursday
    sdks=("openai", "anthropic", "langchain_openai", "langchain_anthropic", "vercel_ai"),
    sources=(
        Source(url="https://platform.claude.com/docs/en/build-with-claude/thinking#output-limits",
               verified="2026-10-25", rows=("ANTH-OUT-01",)),
        Source(url="https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text",
               verified="2026-10-25", rows=("VAI-OUT-01",)),
        Source(url="https://github.com/langchain-ai/langchain/blob/master/libs/partners/openai/langchain_openai/chat_models/base.py",
               verified="2026-10-25", rows=("LC-MAXTOK-01",)),
    ),
    golden=("acg-assafelovic-gpt-researcher-5c1ce388", "acg-beenuar-aisoc-e3c7fd30"),
    options={"max": Opt(int, default=None, minimum=1,
                        help="Also report caps whose resolved value is above this team ceiling. Off by default.")},
    introduced="0.1.0",
    legacy_category="output_unbounded",
    tier="P0",
)
class OutputCapMissing(FileRule):
    def visit_llm_call(self, call: LLMCall, ctx: FileContext) -> None:
        if not call.shape.generates:
            return                                  # embeddings, moderation, count_tokens: no output
        if call.config is not None:
            return                                  # inherits from a client/model object; that site reports
        cap = ctx.param(call, "max_output")
        wire = call.shape.params["max_output"][0]
        if isinstance(cap, Unknown):
            return                                  # **kwargs / spread we cannot see: never report
        if isinstance(cap, Absent):
            ctx.report(call, f"{call.callee}() sends no output cap; set `{wire}`")
            return
        assert isinstance(cap, Param)
        for cand in ctx.candidates(cap):
            value = cand.value
            if isinstance(value, Absent):
                ctx.report(cand.span, f"`{cap.raw}` is dropped on this path, so the call sends no output cap")
            elif isinstance(value, Const) and value.value is None:
                via = f" on the call from line {cand.via.line}" if cand.via else ""
                ctx.report(cap.span if cand.via else cand.span,
                           f"`{cap.raw}` is {value.raw}{via}, which disables the output cap")
        ceiling = ctx.options["max"]
        if ceiling is not None:
            value = ctx.resolve(cap)
            if isinstance(value, Const) and type(value.value) is int and value.value > ceiling:
                ctx.report(cap.span, f"`{cap.raw}` is {value.value}, above the configured ceiling {ceiling}")
```

The rule reports only on proven facts:

- **`Absent`** comes from a closed argument set: no `**kw` or spread left unresolved, and no inherited
  config.
- **A `None`/`null` constant** comes on a path that definitely reaches the call.
- **A ceiling breach** needs a resolved integer, and only applies when a team configured a ceiling.

Everything else is `Unknown` and stays silent.

`tests/rules/out/test_r_out_001.py`:

```python
from spendlint.rules.out.r_out_001 import OutputCapMissing
from spendlint.testing.rule_tester import RuleTester, err, js, py, ts

VALID = [
    py("""
        from openai import OpenAI
        client = OpenAI()
        r = client.chat.completions.create(model="gpt-5.5", messages=m, max_completion_tokens=800)
        """, example=True),
    # golden fix (gpt-researcher): the cap is passed on every branch; `max_tokens` is a parameter with
    # no in-repo caller, so it is Unknown, not None
    py("""
        from langchain_openai import ChatOpenAI

        def make_llm(model, temperature, max_tokens):
            kw = {"model": model}
            if model not in NO_TEMPERATURE_MODELS:
                kw["temperature"] = temperature
            else:
                kw["temperature"] = None
            kw["max_tokens"] = max_tokens
            return ChatOpenAI(**kw)
        """),
    py("""
        from openai import OpenAI
        client = OpenAI()

        def ask(request):
            return client.chat.completions.create(**request)
        """, name="unresolvable **kwargs is Unknown"),
    py("""
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-5.5", max_tokens=512)
        answer = llm.invoke(prompt)
        """, name="invoke inherits the constructor's cap"),
    py("""
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(model="claude-haiku-5-5", max_tokens=1024, messages=m)
        """),
    py("""
        from .db import db
        db.messages.create(body=b)
        """, name="same tail without an SDK import is not an LLM call"),
    py("""
        import os
        from openai import OpenAI
        client = OpenAI()

        def ask(q, cap=None):
            return client.chat.completions.create(model="gpt-5.5", messages=q, max_completion_tokens=cap)

        ask(m, cap=int(os.getenv("CAP", "800")))
        """, name="every in-repo call site passes a cap (env value counts as configured)"),
    py("""
        from openai import OpenAI
        from app.limits import CAP
        client = OpenAI()
        r = client.chat.completions.create(model="gpt-5.5", messages=m, max_completion_tokens=CAP)
        """, files={"app/limits.py": "CAP = 800\n"}, name="cross-module constant"),
    ts("""
        import OpenAI from "openai";
        const client = new OpenAI();
        const r = await client.chat.completions.create({ model: "gpt-5.5", messages, max_completion_tokens: 800 });
        """, example=True),
    ts("""
        import OpenAI from "openai";
        const client = new OpenAI();
        const opts = { model: "gpt-5.5", messages, max_tokens: 500 };
        const r = await client.chat.completions.create(opts);
        """),
    ts("""
        import OpenAI from "openai";
        const client = new OpenAI();
        export async function ask(params: ChatCompletionCreateParams) {
          return client.chat.completions.create({ ...params, messages });
        }
        """, name="spread of a parameter is Unknown"),
    ts("""
        import { generateText } from "ai";
        import { model } from "./models";
        const { text } = await generateText({ model, prompt });
        """, name="model from an unresolved module may carry defaultSettingsMiddleware"),
    ts("""
        import { generateText, wrapLanguageModel, defaultSettingsMiddleware } from "ai";
        import { openai } from "@ai-sdk/openai";
        const model = wrapLanguageModel({
          model: openai("gpt-5.5"),
          middleware: defaultSettingsMiddleware({ settings: { maxOutputTokens: 512 } }),
        });
        const { text } = await generateText({ model, prompt });
        """),
    js("""
        const OpenAI = require("openai");
        const client = new OpenAI();
        const r = await client.chat.completions.create({ model: "gpt-5.5", messages, max_tokens: 256 });
        """),
]

INVALID = [
    py("""
        from openai import OpenAI
        client = OpenAI()
        r = client.chat.completions.create(model="gpt-5.5", messages=m)
        """, errors=[err(line=3, detail_contains="sends no output cap")], example=True),
    # golden before (gpt-researcher): the cap is nulled on the reasoning-model branch
    py("""
        from langchain_openai import ChatOpenAI

        def make_llm(model, temperature, max_tokens):
            kw = {"model": model}
            if model not in NO_TEMPERATURE_MODELS:
                kw["temperature"] = temperature
                kw["max_tokens"] = max_tokens
            else:
                kw["temperature"] = None
                kw["max_tokens"] = None
            return ChatOpenAI(**kw)
        """, errors=[err(line=10, detail_contains="`max_tokens` is None")]),
    # golden before (aisoc): chat model built without a cap; the invoke is not reported again
    py("""
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-5.5", temperature=0)
        answer = llm.invoke(prompt)
        """, errors=[err(line=2, detail_contains="sends no output cap")]),
    py("""
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-5-5",
            messages=[{"role": "user", "content": q}],
        )
        """, errors=[err(line=3, detail_contains="`max_tokens`")]),
    py("""
        from openai import OpenAI
        client = OpenAI()

        def ask(q, cap=None):
            return client.chat.completions.create(model="gpt-5.5", messages=q, max_completion_tokens=cap)

        ask([{"role": "user", "content": "hi"}])
        """, errors=[err(line=5, detail_contains="on the call from line 7")]),
    py("""
        from openai import OpenAI
        client = OpenAI()
        r = client.chat.completions.create(model="gpt-5.5", messages=m, max_completion_tokens=100000)
        """, options={"max": 8000}, errors=[err(line=3, detail_contains="above the configured ceiling 8000")]),
    ts("""
        import OpenAI from "openai";
        const client = new OpenAI();
        const r = await client.chat.completions.create({ model: "gpt-5.5", messages });
        """, errors=[err(line=3, detail_contains="sends no output cap")], example=True),
    ts("""
        import OpenAI from "openai";
        const client = new OpenAI();
        const opts = { model: "gpt-5.5", messages };
        const r = await client.chat.completions.create(opts);
        """, errors=[err(line=4)]),
    ts("""
        import { generateText } from "ai";
        import { openai } from "@ai-sdk/openai";
        const model = openai("gpt-5.5");
        const { text } = await generateText({ model, prompt });
        """, errors=[err(line=4, detail_contains="maxOutputTokens")]),
    js("""
        const OpenAI = require("openai");
        const client = new OpenAI();
        const r = await client.chat.completions.create({
          model: "gpt-5.5",
          messages,
          max_tokens: null,
        });
        """, errors=[err(line=6, detail_contains="disables the output cap")]),
]


def test_rule():
    RuleTester(OutputCapMissing).run(valid=VALID, invalid=INVALID)
```

These cases depend on catalog entries that MON-5 and TUE must provide:

- **OpenAI.** `chat.completions` maps `max_output` to `("max_completion_tokens", "max_tokens")`, with
  `nested` `extra_body.max_tokens`.
- **Anthropic.** `messages` maps `max_output` to `("max_tokens",)` with `cap_required=True`.
- **LangChain.** `ChatOpenAI` / `ChatAnthropic` are `client_config` shapes with `max_output` =
  `("max_tokens", "max_completion_tokens")` and `nested` `model_kwargs.max_tokens`. `.invoke`,
  `.ainvoke`, `.stream` and `.batch` have `inherits="receiver"`.
- **Vercel.** `generateText`, `streamText`, `generateObject` and `streamObject` map `max_output` to
  `("maxOutputTokens",)`, with `inherits="model_arg"`. That resolves the `model` argument: an
  unresolved model is `Unknown`, a provider factory call (`openai("...")`) is closed with no cap, and
  `wrapLanguageModel` with `defaultSettingsMiddleware({settings: {maxOutputTokens}})` supplies the cap.

### Companion: R-OUT-005 (cap within the model maximum)

```python
@rule(
    id="R-OUT-005",
    title="Output cap above the model's maximum output tokens",
    practice="The output cap does not exceed the model's documented maximum output tokens (request rejected).",
    why="The API rejects the request, and retry wrappers often re-send it, so the input is billed again.",
    fix="Lower the cap to the model's documented maximum, or use the batch extended-output beta where supported.",
    category="out", severity="warning", status="stable",
    languages=("python", "typescript"), sdks=("anthropic",),
    sources=(Source(url="https://platform.claude.com/docs/en/build-with-claude/thinking#output-limits",
                    verified="2026-10-25", rows=("ANTH-OUT-01",)),
             Source(url="https://platform.claude.com/docs/en/build-with-claude/batch-processing#extended-output-beta",
                    verified="2026-10-25", rows=("ANTH-OUT-01",))),
    options={}, introduced="0.3.0", tier="P1",
)
class OutputCapAboveModelMax(FileRule):
    def visit_llm_call(self, call: LLMCall, ctx: FileContext) -> None:
        cap, model = ctx.param(call, "max_output"), ctx.param(call, "model")
        if not (isinstance(cap, Param) and isinstance(model, Param)):
            return
        c, m = ctx.resolve(cap), ctx.resolve(model)
        if not (isinstance(c, Const) and type(c.value) is int and isinstance(m, Const) and isinstance(m.value, str)):
            return                                   # model or cap not a literal: Unknown, silent
        info = ctx.models.get(m.value, provider="anthropic")
        if info is None or info.max_output_tokens is None:
            return                                   # model not in models.toml: silent
        if call.api == "messages.batch":
            return                                   # the extended-output beta raises the ceiling; not modelled yet
        if c.value > info.max_output_tokens:
            ctx.report(cap.span, f"`{cap.raw}={c.value}` exceeds {m.value}'s maximum of {info.max_output_tokens} output tokens")
```

```python
VALID = [
    py("""
        import anthropic
        client = anthropic.Anthropic()
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=64000, messages=m)
        """),
    py("""
        import anthropic
        client = anthropic.Anthropic()
        r = client.messages.create(model=settings.model, max_tokens=100000, messages=m)
        """, name="model not resolvable"),
    py("""
        import anthropic
        client = anthropic.Anthropic()
        r = client.messages.create(model="claude-unknown-9", max_tokens=100000, messages=m)
        """, name="model not in models.toml"),
    ts("""
        import Anthropic from "@anthropic-ai/sdk";
        const client = new Anthropic();
        const r = await client.messages.create({ model: "claude-haiku-4-5-20251001", max_tokens: 4096, messages });
        """),
]
INVALID = [
    py("""
        import anthropic
        client = anthropic.Anthropic()
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=100000, messages=m)
        """, errors=[err(line=3, detail_contains="maximum of 64000")]),
    py("""
        import anthropic
        MODEL = "claude-haiku-4-5"
        client = anthropic.Anthropic()
        r = client.messages.create(model=MODEL, max_tokens=70000, messages=m)
        """, errors=[err(line=4)], name="alias via a constant"),
    py("""
        import anthropic
        client = anthropic.Anthropic()
        kw = {"model": "claude-haiku-4-5-20251001", "max_tokens": 128000}
        r = client.messages.create(messages=m, **kw)
        """, errors=[err(line=3)]),
    ts("""
        import Anthropic from "@anthropic-ai/sdk";
        const client = new Anthropic();
        const r = await client.messages.create({ model: "claude-haiku-4-5-20251001", max_tokens: 100000, messages });
        """, errors=[err(line=3, detail_contains="maximum of 64000")]),
]
```

(The 64000 ceiling for `claude-haiku-4-5-20251001` is from ANTH-OUT-01, verified 2026-10-25.)

---

## 16. Open decisions and known conflicts

1. **The scope of R-OUT-001.** WEEK1_PLAN §6 says "OUT-001 output cap set and within model max", but
   `practices.json` splits ANTH-OUT-01 into R-OUT-001 (cap present) and R-OUT-005 (cap ≤ the model
   maximum, P1). This RFC follows `practices.json`, which is authoritative, and the one-practice rule.
   R-OUT-005 can ship on Tuesday alongside it.
2. **Confidence 1.0 against `test_min_confidence_from_config_and_flag_override`.** That test sets
   `min_confidence: 0.99` and expects zero findings from `SRC`, which only works while the rules report
   confidence below 0.99. Decision: Monday's 0.2.0 parity port **keeps each ported rule's legacy
   confidence**, so the test is unchanged. When a rule is rewritten as a crisp check (Tuesday onward) it
   reports 1.0, and the test moves to an experimental fixture rule registered only in tests, with
   `confidence=0.5`. That PR must keep the asserted behavior (a config floor drops findings below it, and
   `--min-confidence 0` restores them).
3. **The `test_cli.SRC` fixture relies on heuristic shapes.** It expects R-CTX-008 to fire on `p =
   f'Data: {json.dumps(d, indent=2)}'` and R-CACHE-003 to fire on `SYSTEM_PROMPT = f'... {datetime.now()}'`.
   Neither is provably a prompt. When those rules become crisp, the fixture may change so that the same
   `(rule_id, line, severity)` tuples still come out: lines 2 and 4, info and warning. One way is to pass
   the strings into `messages=` / `system=`. Every asserted string in §10.2 stays byte-identical. The same
   applies to `test_rules.py` cases, which become RuleTester cases.
4. **Post-processing order.** The plan lists "suppress, rule config, diff filter". This RFC keeps today's
   order (selection, then diff, then suppression), because `stats["suppressed"]` counts only findings that
   survive the diff filter, and the Action summary prints that count.
5. **`gitdiff.py` becomes `engine/diff.py`.** `test_cli` imports `spendlint.gitdiff.added_lines`, so
   `gitdiff.py` stays as a re-export shim.
6. **`progress_callback` crashes** (§1). Parse timeouts are not available in 0.26.0. If a pathological file
   shows up, the fallback is a lower `MAX_BYTES` for that extension, not a callback. Re-check on each
   tree-sitter bump; the probe does it automatically.
7. **Config-file languages** (`properties/yaml`, `cron/yaml` in `practices.json`, for Spring AI
   `spring.ai.*.max-tokens`) have no grammar here. Proposal for week 2: a tiny `lang/config/` reader that
   emits `client_config` LLMCalls from `application.properties` / `application.yml`. Until then, those SDK
   variants count as Java-only.
8. **Tests move to `tests/`** (pytest). `spendlint/test_*.py` move with absolute imports. Their assertions
   are the contract in §10.2.
