"""Probe the tree-sitter core and every grammar package spendlint depends on.

    python scripts/dev/probe_grammars.py          # human-readable table, exit 1 on any failure
    python scripts/dev/probe_grammars.py --json   # one JSON object per grammar

For each grammar it loads the language with `Language(...)`, checks its ABI against the core's
supported range, parses a small sample (which must have no ERROR/MISSING nodes) and runs one
`Query` + `QueryCursor(query).captures(root)` with a `#eq?` predicate, so a pass proves the
tree-sitter 0.26 API end to end. Run it in a clean venv after a dependency bump. It is the same
check `spendlint --selfcheck` performs in the shipped engine (docs/architecture.md, "Errors").
"""
import argparse
import importlib
import json
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

import tree_sitter as ts

# (grammar id, distribution, module, entry function, sample source, query, capture name, expected capture texts)
GRAMMARS = [
    ("python", "tree-sitter-python", "tree_sitter_python", "language",
     b"r = client.messages.create(model='claude-haiku-5-5', max_tokens=512, messages=m)\n",
     '((keyword_argument name: (identifier) @kw) (#eq? @kw "max_tokens"))', "kw", ["max_tokens"]),
    ("javascript", "tree-sitter-javascript", "tree_sitter_javascript", "language",
     b"const r = await client.messages.create({ model: 'm', max_tokens: 512, messages });\n",
     '((pair key: (property_identifier) @key) (#eq? @key "max_tokens"))', "key", ["max_tokens"]),
    ("typescript", "tree-sitter-typescript", "tree_sitter_typescript", "language_typescript",
     b"const r: Message = await client.messages.create({ model: 'm', max_tokens: 512, messages } as Params);\n",
     '((pair key: (property_identifier) @key) (#eq? @key "max_tokens"))', "key", ["max_tokens"]),
    ("tsx", "tree-sitter-typescript", "tree_sitter_typescript", "language_tsx",
     b"const r = await generateText({ model, maxOutputTokens: 256, prompt });\nexport const C = () => <div>{r.text}</div>;\n",
     '((pair key: (property_identifier) @key) (#eq? @key "maxOutputTokens"))', "key", ["maxOutputTokens"]),
    ("go", "tree-sitter-go", "tree_sitter_go", "language",
     b"package main\n\nfunc f() {\n\tmsg, err := client.Messages.New(ctx, anthropic.MessageNewParams{MaxTokens: 1024})\n\t_, _ = msg, err\n}\n",
     '((call_expression function: (selector_expression field: (field_identifier) @method)) (#eq? @method "New"))',
     "method", ["New"]),
    ("java", "tree-sitter-java", "tree_sitter_java", "language",
     b"class A { void f() { var o = OpenAiChatOptions.builder().maxTokens(512).build(); } }\n",
     '((method_invocation name: (identifier) @method) (#eq? @method "maxTokens"))', "method", ["maxTokens"]),
    ("rust", "tree-sitter-rust", "tree_sitter_rust", "language",
     b"fn f() { let req = CreateChatCompletionRequestArgs::default().max_completion_tokens(512u32).build(); }\n",
     '((call_expression function: (field_expression field: (field_identifier) @method)) (#eq? @method "max_completion_tokens"))',
     "method", ["max_completion_tokens"]),
    ("csharp", "tree-sitter-c-sharp", "tree_sitter_c_sharp", "language",
     b"class A { void F() { var r = client.CompleteChat(messages, new ChatCompletionOptions { MaxOutputTokenCount = 512 }); } }\n",
     '((invocation_expression function: (member_access_expression name: (identifier) @method)) (#eq? @method "CompleteChat"))',
     "method", ["CompleteChat"]),
]


def probe(gid, dist, module, entry, src, query_src, cap, expected):
    row = {"grammar": gid, "distribution": dist, "module": f"{module}.{entry}()", "ok": False}
    try:
        row["version"] = version(dist)
    except PackageNotFoundError:
        row["error"] = f"{dist} is not installed"
        return row
    try:
        lang = ts.Language(getattr(importlib.import_module(module), entry)())
        row["abi_version"] = lang.abi_version
        row["semantic_version"] = ".".join(map(str, lang.semantic_version)) if lang.semantic_version else None
        row["name"] = lang.name
        if not ts.MIN_COMPATIBLE_LANGUAGE_VERSION <= lang.abi_version <= ts.LANGUAGE_VERSION:
            row["error"] = f"ABI {lang.abi_version} outside core range {ts.MIN_COMPATIBLE_LANGUAGE_VERSION}..{ts.LANGUAGE_VERSION}"
            return row
        tree = ts.Parser(lang).parse(src)
        row["root"] = tree.root_node.type
        row["parse_error"] = tree.root_node.has_error  # true if any ERROR or MISSING node is in the tree
        captures = ts.QueryCursor(ts.Query(lang, query_src)).captures(tree.root_node)  # dict[str, list[Node]]
        got = sorted(n.text.decode() for n in captures.get(cap, []))
        row["captures"] = got
        row["ok"] = not row["parse_error"] and got == sorted(expected)
        if not row["ok"]:
            row["error"] = "sample has parse errors" if row["parse_error"] else f"captures {got} != {expected}"
    except Exception as e:  # report every failure, never stop at the first
        row["error"] = f"{type(e).__name__}: {e}"
    return row


# Known hazard: in tree-sitter 0.26.0 any `progress_callback` (Parser.parse with a read callback, or
# QueryCursor.captures) crashed the interpreter with a segfault on CPython 3.11 / win_amd64 during the
# Day-0 probe, even a callback that always returns False. spendlint never passes one. These run in a
# subprocess so a crash is reported instead of killing the probe; they do not affect the exit status.
HAZARDS = {
    "parse(progress_callback=)":
        "import tree_sitter as ts, tree_sitter_python as tp\n"
        "p = ts.Parser(ts.Language(tp.language())); src = b'x = 1\\n' * 20000\n"
        "p.parse(lambda off, pt: src[off:off + 65536], progress_callback=lambda st: False)\n",
    "captures(progress_callback=)":
        "import tree_sitter as ts, tree_sitter_python as tp\n"
        "L = ts.Language(tp.language()); t = ts.Parser(L).parse(b'f(a=1)\\n' * 100)\n"
        "ts.QueryCursor(ts.Query(L, '(keyword_argument) @k')).captures(t.root_node, progress_callback=lambda st: False)\n",
}


def hazards():
    out = {}
    for name, code in HAZARDS.items():
        p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=120)
        out[name] = "ok" if p.returncode == 0 else f"crashes (exit {p.returncode})"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print one JSON object per grammar")
    ap.add_argument("--no-hazards", action="store_true", help="skip the progress_callback crash checks")
    a = ap.parse_args(argv)
    rows = [probe(*g) for g in GRAMMARS]
    header = {"python": platform.python_version(), "platform": f"{sys.platform}/{platform.machine()}",
              "tree-sitter": version("tree-sitter"),
              "core_abi_range": [ts.MIN_COMPATIBLE_LANGUAGE_VERSION, ts.LANGUAGE_VERSION]}
    if not a.no_hazards:
        header["hazards"] = hazards()
    if a.json:
        print(json.dumps(header))
        for r in rows:
            print(json.dumps(r))
    else:
        print(f"tree-sitter {header['tree-sitter']} (ABI {header['core_abi_range'][0]}..{header['core_abi_range'][1]})"
              f" on Python {header['python']} {header['platform']}")
        for r in rows:
            status = "ok" if r["ok"] else "FAIL"
            pin = f"{r['distribution']}=={r.get('version', '?')}"
            print(f"  {status:<4} {r['grammar']:<10} {pin:<30} ABI {r.get('abi_version', '?')}"
                  f"  {r['module']:<44} captures={r.get('captures')}" + (f"  error: {r['error']}" if r.get("error") else ""))
        for name, result in header.get("hazards", {}).items():
            print(f"  hazard {name}: {result}")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
