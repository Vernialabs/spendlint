"""Run: python -m costlint.test_rules   (each case: source, language, expected rule ids)"""
import ast
import sys
import tempfile
from pathlib import Path

from .scan import scan_repo

CASES = [
    # --- R-LOOP-001
    ("py", "while True:\n    r = client.chat.completions.create(model='x', messages=m)\n    m.append(r)\n", {"R-LOOP-001", "R-OUT-001"}),
    ("py", "while True:\n    r = client.chat.completions.create(model='x', messages=m, max_tokens=5)\n    if step > max_steps: break\n    step += 1\n", set()),
    ("py", "while True:\n    q = input('> ')\n    client.chat.completions.create(model='x', messages=[q], max_tokens=5)\n", set()),
    # --- R-LOOP-002
    ("py", "import tenacity\nfrom tenacity import retry\n@retry(wait=wait_exponential())\ndef call_llm(x):\n    return client.chat.completions.create(model='x', messages=x, max_tokens=5)\n", {"R-LOOP-002"}),
    ("py", "from tenacity import retry, stop_after_attempt\n@retry(stop=stop_after_attempt(3))\ndef call_llm(x):\n    return client.chat.completions.create(model='x', messages=x, max_tokens=5)\n", set()),
    # --- R-CACHE-003
    ("py", "from datetime import datetime\nSYSTEM_PROMPT = f'You are helpful. Now: {datetime.now()}'\n", {"R-CACHE-003"}),
    ("py", "import os\nm = [{'role': 'system', 'content': f'cwd={os.getcwd()}'}]\n", {"R-CACHE-003"}),
    ("py", "def build_system_prompt():\n    return f'time {time.time()}'\n", {"R-CACHE-003"}),
    ("py", "SYSTEM_PROMPT = 'static text'\nlog = f'{datetime.now()}'\n", set()),
    ("ts", "const systemPrompt = `You are X. Today is ${new Date().toISOString()}`;\n", {"R-CACHE-003"}),
    ("ts", "const systemPrompt = `You are X.`;\nconst t = `${new Date()}`;\n", set()),
    # --- R-CTX-008
    ("py", "import json\np = f'Data: {json.dumps(d, indent=2)}'\n", {"R-CTX-008"}),
    ("py", "import json\np = f'Data: {json.dumps(d)}'\n", set()),
    # --- R-CALL-004
    ("py", "for t in texts:\n    v = embedder.embed_query(t)\n", {"R-CALL-004"}),
    ("py", "vs = [client.embeddings.create(model='m', input=t) for t in texts]\n", {"R-CALL-004"}),
    ("py", "vs = embedder.embed_documents(texts)\n", set()),
    # --- R-MEDIA-001
    ("py", "img = {'type': 'image_url', 'image_url': {'url': u, 'detail': 'high'}}\n", {"R-MEDIA-001"}),
    ("ts", "const img = { type: 'image_url', image_url: { url: u, detail: 'high' } };\n", {"R-MEDIA-001"}),
    # --- R-OUT-001
    ("py", "r = client.chat.completions.create(model='x', messages=m)\n", {"R-OUT-001"}),
    ("py", "r = client.chat.completions.create(model='x', messages=m, max_completion_tokens=100)\n", set()),
    ("py", "r = client.chat.completions.create(model='x', messages=m, **kw)\n", set()),
    # --- R-MODEL-001
    ("py", "def generate_title(t):\n    return client.chat.completions.create(model='gpt-4o', messages=[t], max_tokens=20)\n", {"R-MODEL-001"}),
    ("py", "def generate_title(t):\n    return client.chat.completions.create(model='gpt-4o-mini', messages=[t], max_tokens=20)\n", set()),
    # --- false-positive regressions found on held-out repos
    ("py", "import logging\nlogging.debug(f'params: {json.dumps(d, indent=2)}')\n", set()),
    ("py", "print(f'H: {json.dumps(d, indent=2)}')\n", set()),
    ("py", "for batch in batched(texts, 64):\n    r = client.embeddings.create(input=batch, model='m')\n", set()),
    ("py", "from datetime import datetime\nSYSTEM_PROMPT = f'Today: {datetime.now().date().isoformat()}'\n", set()),
    ("py", "from datetime import datetime\nSYSTEM_PROMPT = f\"Today: {datetime.now().strftime('%Y-%m-%d')}\"\n", set()),
    ("py", "def f():\n    \"\"\"Example {'detail': 'high'} in docs\"\"\"\n    return 1\n", set()),
    ("py", "def f():\n    \"\"\"cost = input_tokens * price\"\"\"\n    return 1\n", set()),
    ("ts", "/**\n * Each adapter maps input_tokens and prompt_tokens, price per M\n */\nexport interface U {}\n", set()),
    # --- shapes seen in real-world cost fixes
    ("py", "import uuid\npayload = {'model': m, 'prompt_cache_key': str(uuid.uuid4())}\n", {"R-CACHE-003"}),
    ("py", "def get_sdk_supplement(cwd):\n    return f'Working directory: {cwd}\\nRules...'\n", {"R-CACHE-003"}),
    ("py", "SYSTEM_PROMPT = f'You are X. Session: {session_id}. ' + RULES\n", {"R-CACHE-003"}),                                      # per-request id
    ("py", "SYSTEM_PROMPT = 'A' * 1000 + f'... cwd={cwd}'\n", set()),                                                                 # path far from the head: fine
    ("ts", "const systemPrompt = `<env>\\n  Current conversation session ID: ${sessionID}\\n  Working directory: ${location.directory}\\n</env>`;\n", {"R-CACHE-003"}),
    ("ts", "const x = { prompt_cache_key: randomUUID(), model };\n", {"R-CACHE-003"}),
    ("ts", "export const bash = { description: DESCRIPTION.replaceAll('${directory}', Instance.directory) };\n", {"R-CACHE-003"}),
    ("py", "def embed_documents(self, texts):\n    return [self._generate_embedding(t) for t in texts]\n", {"R-CALL-004"}),
    ("py", "def _get_text_embeddings(self, texts):\n    out = []\n    for text in texts:\n        out.append(self.get_general_text_embedding(text))\n    return out\n", {"R-CALL-004"}),
    ("py", "await semaphore_gather(*[edge.generate_embedding(embedder) for edge in edges])\n", {"R-CALL-004"}),
    ("py", "for i, text in enumerate(texts):\n    vec = self.embedding_function(text)\n", {"R-CALL-004"}),
    ("py", "def to_prompt_json(data, indent: int = 2):\n    return json.dumps(data, indent=indent)\n", {"R-CTX-008"}),
    ("py", "p = f'<FACTS>{to_prompt_json(facts, indent=12)}</FACTS>'\n", {"R-CTX-008"}),
    ("py", "r = self.model.invoke(self.summary_prompt.format(messages=trimmed_messages))\n", {"R-CTX-008"}),
    ("py", "r = self.model.invoke(self.summary_prompt.format(messages=formatted_messages))\n", set()),                              # the fix
    ("py", "provider_kwargs['max_tokens'] = None\n", {"R-OUT-001"}),
    ("py", "r = client.chat.completions.create(model='x', messages=m, max_tokens=None)\n", {"R-OUT-001"}),
    ("py", "usage = {'input_tokens': int(usage.input_tokens), 'output_tokens': int(usage.output_tokens)}\n", {"R-ACCT-001"}),       # normalizer without cache fields
    ("ts", "const u = { promptTokens: usage.prompt_tokens || usage.input_tokens, completionTokens: usage.completion_tokens };\n", {"R-ACCT-001"}),
    ("py", "import litellm\nmetrics.cache_read_tokens = prompt_details.get('cached_tokens', 0)\nmetrics.usage = usage\n", {"R-ACCT-001"}),
    ("py", "import openai\nmetrics.cache_read_tokens = details.get('cached_tokens', 0)\nmetrics.usage = usage\n", set()),             # OpenAI has no write cost
    ("py", "from langchain_anthropic import ChatAnthropic\nllm = ChatAnthropic(model=model, max_tokens=5)\nr = llm.invoke(prompt)\n", {"R-CACHE-001"}),
    ("py", "from langchain_anthropic import ChatAnthropic\nllm = ChatAnthropic(model=model)\nmsg = {'type': 'text', 'text': t, 'cache_control': {'type': 'ephemeral'}}\n", set()),
    # --- false-positive classes from the out-of-sample scan of the second pass
    ("py", "def usage(self):\n    '''Keys: input_tokens, cached_tokens (litellm/anthropic breakdown), usage'''\n    return dict(self._usage)\n", set()),   # docstring only
    ("py", "p = f'{json.dumps(d, indent=2)}'\nassert x, f'bad: {json.dumps(d, indent=2)}'\n", {"R-CTX-008"}),                                       # assert context ignored, plain one kept
    ("py", "path.write_text(f'{json.dumps(d, indent=2)}\\n')\n", set()),                                                                            # file output
    ("py", "try:\n    e = f.embed_documents(texts)\nexcept NotImplementedError:\n    e = [f.embed_query(x) for x in texts]\n", set()),              # deliberate fallback
    ("py", "p = TEMPLATE.format(chat_history=chat_history)\n", set()),                                                                              # history is a string
    ("ts", "const r = { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } };\n", set()),                                           # mock zeros, nothing read
    ("ts", "toast({ description: `Browser session ID found: ${opts.browserSessionId}` });\n", set()),                                               # UI, no prompt code
    # --- R-ACCT-001
    ("py", "cost = usage.input_tokens * PRICE_IN + usage.output_tokens * PRICE_OUT\n", {"R-ACCT-001"}),
    ("py", "cost = usage.input_tokens * PRICE_IN\nread = usage.cache_read_input_tokens\n", set()),
]


def run():
    fails = 0
    for i, (lang, src, expect) in enumerate(CASES):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / f"case.{'py' if lang == 'py' else 'ts'}"
            p.write_text(src, encoding="utf-8")
            found = {f["rule_id"] for f in scan_repo(td)[0]}
        ok = found == expect
        fails += not ok
        if not ok:
            print(f"FAIL case {i} ({lang}): expected {sorted(expect)} got {sorted(found)}\n{src}")
    print(f"{len(CASES) - fails}/{len(CASES)} cases pass")
    return fails


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
