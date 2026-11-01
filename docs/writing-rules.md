# Writing spendlint rules

This guide is for anyone adding or changing a rule. Read [architecture.md](architecture.md) §3 to §7 first:
the model, value resolution, the rule API and RuleTester are defined there, and this guide doesn't repeat
them. §15 of that document is a complete worked rule (R-OUT-001) to copy from.

A spendlint rule is held to two bars:

1. **One documented practice per rule.** A finding means "this code breaks *that* practice". A reviewer can
   check the finding against the practice text without guessing.
2. **Zero false positives.** Code that already follows the practice is never flagged. When the engine can't
   prove a violation, the rule stays silent.

The bars rule out heuristics. A rule that flags `generate_title()` because the name sounds cheap, or flags a
string because the variable is called `SYSTEM_PROMPT`, breaks bar 2 the first time a compliant repo uses
that name.

---

## 1. Workflow (one rule per PR)

1. **Pick the practice.** Rules come from `practices.json` / `PRACTICES.md` in the private research repo.
   Each entry gives the rule id, the practice sentence, the tier (P0/P1/P2), the release day, the SDK
   variants and the source rows. Take the id and the practice text as written. If you think the text is
   wrong, fix the catalog first (in a private PR), then write the rule.
2. **Check that it is one practice.** Can you state the violation in one sentence that names one missing
   or wrong thing? If a source row bundles two things, they are two rules. For example, ANTH-OUT-01 "set
   `max_tokens` and keep it ≤ the model ceiling" became R-OUT-001 (a cap is present) and R-OUT-005 (the
   cap is within the model maximum).
3. **Verify every source today** (§2). Record the URL and today's date in `Source(...)`.
4. **Collect the evidence** (§3): the golden datapoints mapped to the id, and the good and bad examples from
   the docs.
5. **Write the cases first** (§4): golden before → `INVALID` with exact lines, golden after → `VALID`, then
   the doc examples, then near misses.
6. **Implement the rule** in `spendlint/rules/<cat>/r_<cat>_<nnn>.py`, one class with the `@rule`
   decorator (§5). Use the model and `ctx.param` / `ctx.resolve` / `ctx.candidates`. Never read source
   text with regexes.
7. **Run the zero-false-positive checklist** (§6) and the measurements (§8).
8. **Generate the docs page**: `python -m spendlint.tools.gendocs` (§7).
9. **Open the PR** (§9): one rule, at most 400 changed lines excluding fixtures, a CHANGELOG line, and the
   checklist filled in.

---

## 2. Citing the documentation

- **Cite the page that states the behavior** the rule relies on: the parameter name, the default, the
  error, the price multiplier. Link to the anchor when the page has one. If the behavior is only visible in
  SDK source, cite the file at a released version with a line number, the way LC-MAXTOK-01 cites
  `langchain_anthropic/chat_models.py` at 1.7.6.
- **`verified` is the date you read the page**, in ISO format (`"2026-10-25"`). Never copy a date from the
  catalog without opening the page. Docs change, and the date says when someone last checked.
- **If the page now says something different** from the catalog row, stop:
  - fix the catalog row;
  - update `models.toml` if a number changed;
  - note it in the tracker;
  - write the rule against the current behavior.
- **Numbers go in `sdk/data/models.toml`, not in rules**: maximum output tokens, cache minimums, prices,
  retirement dates. Each entry carries its own `source` and `verified`. A rule that needs a number it can't
  find there adds the entry, with a source, in the same PR.
- **Never cite from memory.** That includes your memory of a page you read last week.

---

## 3. Evidence: golden datapoints and doc examples

- **Golden datapoints.** These are real cost-defect fixes in the private dataset. Each has a
  `rule_candidate.rule_id`, `defect.before_snippet`, `fix.after_snippet`, file paths, and a detectability
  label. The release gate (M1) requires, for every static datapoint mapped to your rule:
  - the **pre-fix** file gets a finding from your rule on a line that overlaps the `before_snippet` lines;
  - the **post-fix** file gets **no** finding from your rule there.

  So report at the defect, on the line where the cap is nulled or the call is made, not on the enclosing
  function header.
- **Doc-backed rules** (no golden datapoints) take their cases from the docs' own good and bad examples, and
  they still need 0 false positives on the hard negatives and the fresh-repo scan (M2).
- **If a datapoint can't be caught statically** after a real attempt, it does not get dropped. It moves to
  `needs_reasoning` with a written reason, an independent verifier's agreement, and a tracker entry (M3).
  Say so in the PR.

---

## 4. Golden before/after cases

Every rule ships `tests/rules/<cat>/test_r_<cat>_<nnn>.py` with module-level `VALID` and `INVALID` lists.
The contract test requires:

- at least 3 valid and 3 invalid cases;
- at least one valid and one invalid case per declared language family (`python`, `typescript`,
  `javascript`, `go`, `java`, `rust`, `csharp`).

How to build them:

1. **Golden before → invalid.**
   - Reduce the `before_snippet` to the smallest source that keeps the shape that matters: the import that
     makes the call an SDK call, the branch that drops the value, the factory that builds the client.
   - Keep it valid syntax.
   - Give the exact `line` of every expected finding, and use `detail_contains` to pin the reason.
   - Put the datapoint id in a comment above the case.
2. **Golden after → valid.** Reduce the `after_snippet` the same way. This is the strongest case you have:
   it is real code that follows the practice.
3. **Doc examples.** Add the page's "bad" example to `INVALID` and its "good" example to `VALID`. Mark the
   clearest pair per language with `example=True`; those appear on the generated docs page.
4. **Near misses** (all valid):
   - the same call with the value supplied in another legal place (a client constructor, middleware, an
     env var, `extra_body`, `model_kwargs`);
   - a value from a parameter with no in-repo caller;
   - an unresolved `**kwargs` or spread;
   - a same-named method without the SDK import;
   - a call in a parse-error region.

   Each near miss documents one reason the rule stays silent. Give it a `name=`.
5. **Options.** For every option, add one case that flips the outcome with `options={...}`.
6. **Cross-file cases** use `files={...}` when resolution crosses modules. Add at least one when the golden
   defect spans files.

Cases run through the real pipeline (discover, parse, extract, the module index, your rule, suppression),
so a case that passes in RuleTester behaves the same in a scan.

---

## 5. The rule itself

### Metadata

| Field | How to write it |
|---|---|
| `id` | From `practices.json`. The module path follows from it (`R-OUT-001` → `rules/out/r_out_001.py`). |
| `title` | Names the violation, ≤ 80 characters: "Generation call without an output-token cap". It shows up as the annotation title and the SARIF rule name. |
| `practice` | The practice sentence from `practices.json`, word for word. |
| `why` | One or two sentences on the money: what gets billed, and how it scales. No marketing. |
| `fix` | The concrete change, with parameter names per SDK. It appears in every annotation. |
| `severity` | Impact, not certainty. Use `warning` for definite waste or an API error: a 400, an ignored parameter, a retired model, a missing cap on an API that requires one. Use `info` for an advisory practice. Teams change it with `rules:`. |
| `status` | `stable` once M2 shows 0 false positives. Use `experimental` (off by default) when you can't show that yet. |
| `languages` / `sdks` | Only what you have cases for. A rule never runs on an undeclared language. |
| `sources` / `golden` | At least one of the two. Both is better. |
| `options` | `{}` unless a team-specific threshold makes sense. An option's default must keep the rule zero-false-positive, and an option may never turn the rule into a heuristic. |
| `tier`, `introduced` | From the catalog; `introduced` is the release that first ships the rule. |

### Logic

- **Visit the model object the practice is about**: `visit_llm_call` for request parameters,
  `visit_cache_marker` for breakpoints, `visit_usage_read` for accounting, `visit_loop` for loop bounds.
  Use `visit_call` with `calls=(...)` for non-LLM calls such as `json.dumps`. `visit_capture` is the last
  resort, and it comes with an engine ticket to model the syntax properly.
- **Ask presence questions with `ctx.param(call, "name")`**, which returns `Param`, `Absent` or
  `Unknown`. Report on `Absent` only, never on `Unknown`.
- **Ask "is it this value on every path?" with `ctx.resolve`.** Ask "is there a path where it is bad?" with
  `ctx.candidates`, and report only on a candidate that is a concrete violation. Report at the candidate's
  span.
- **Get SDK facts from the catalog**: parameter names, defaults, required fields. If the catalog lacks one,
  add it there (with a source), not in the rule.
- **The `detail` message** is one line naming the concrete fact and the parameter: "`max_tokens` is None,
  which disables the output cap", "client.chat.completions.create() sends no output cap; set
  `max_completion_tokens`".
  - No hedging ("may", "looks like", "possibly").
  - No source code beyond identifiers.
  - At most about 120 characters.
- **Report through `ctx.report`.** Stable rules report `confidence=1.0`.
- **Keep it small.** A rule is usually 20 to 60 lines. If it grows past that, the missing piece is probably
  an engine or catalog feature.

---

## 6. Zero-false-positive checklist

Answer every item in the PR description. One "no" means the rule isn't ready, or ships `experimental`.

- [ ] **Silent on `Unknown`.** No branch reports because something is `Unknown`, `MemberRef`,
  `CallResult`, `ParamRef` or `EnvVar`. Environment values count as configured.
- [ ] **Absence is proven.** An "X is missing" report only comes from `Absent`: a closed argument set with
  every spread resolved and the inherited config checked.
- [ ] **Every legal place for the value is checked**, including:
  - the call itself, and `**kwargs` / spreads;
  - the client constructor, and model objects (LangChain constructors, `.bind`);
  - middleware and default settings (Vercel `defaultSettingsMiddleware`, Mastra `defaultOptions`);
  - `extra_body` and `model_kwargs`;
  - config files that the catalog models.

  The near-miss cases prove each one.
- [ ] **Defaults come from the SDK.** If the SDK or provider default already follows the practice (for
  example, a framework that defaults to a sane step cap), omitting the parameter is compliant. The default
  comes from the catalog, with a source.
- [ ] **SDK calls are matched by import.** The rule relies on catalog matching. No name heuristics on
  functions, variables or files.
- [ ] **Adapters and wrappers** that forward the value (`**kwargs` passed through, a wrapper with a
  default) don't produce a finding at the wrapper, unless that wrapper itself drops the value.
- [ ] **Parse errors don't produce findings.** Code inside ERROR/MISSING regions never does; the engine
  skips it, so don't work around that.
- [ ] **The golden post-fix files are silent** for this rule (RuleTester valid cases from every
  `after_snippet`, plus M1(b) in the harness).
- [ ] **The hard negatives are silent**: all 515, in whole-file and PR mode (M2a). Any finding there is
  checked against the practice text by a verifier who did not write the rule.
- [ ] **The fresh-repo scan** (15+ repos the rule has never seen) has only genuine breaches, each checked
  against the practice text by the verifier (M2c).
- [ ] **One practice.** The finding can be confirmed by reading the practice sentence and the reported
  lines, and nothing else.

---

## 7. The docs page

`python -m spendlint.tools.gendocs` writes `docs/rules/<id>.md` from the `@rule` metadata and the
`example=True` cases. CI runs `gendocs --check` and fails when a page is stale. Never edit a generated page
by hand; change the metadata or the cases. The page has, in this order:

1. The title and id, severity, status, and the release that introduced it.
2. **Practice**: the sentence.
3. **Why it costs money**: `why`.
4. **Bad / Good**: the example cases, per language, with the finding's line marked.
5. **How to fix**: `fix`.
6. **Options**: name, type, default and help, plus a `.spendlint.yml` snippet.
7. **Sources**: each URL with its verified date and catalog rows; then the golden evidence count.
8. **SDKs and languages** covered.
9. **Suppressing**: `# spendlint: ignore <id> reason`.

SARIF `helpUri` points to this page at the release tag, so the page must exist before the rule ships.

---

## 8. Measuring before you open the PR

From the public repo:

```
pytest tests/rules/<cat>/test_r_<cat>_<nnn>.py tests/test_rule_contract.py
python -m spendlint.tools.gendocs --check
```

From the private repo, with the engine on your branch:

```
python scripts/measure_release.py --quick --rules R-XXX-NNN    # M1 for this rule + hard-negative M2
```

Paste the M1 count (caught/mapped) and the M2 false-positive count (target 0) into the PR. A rule whose
datapoints are not all caught either gets fixed, or each miss goes through the relabel process (§3).

---

## 9. PR checklist

- [ ] One rule: `spendlint/rules/<cat>/r_<cat>_<nnn>.py` and `tests/rules/<cat>/test_r_<cat>_<nnn>.py`.
- [ ] Catalog or `models.toml` additions in the same PR, each with a source and a verified date.
- [ ] Metadata complete: the practice is word for word from `practices.json`, the sources were verified
  today, and the golden ids are listed.
- [ ] Cases cover: golden before and after for every mapped static datapoint, the doc examples, near
  misses, one case per option, and every declared language.
- [ ] The zero-false-positive checklist (§6) is answered in the PR description.
- [ ] `docs/rules/<id>.md` is regenerated.
- [ ] A CHANGELOG line under "Unreleased": `R-XXX-NNN <title> (<severity>)`.
- [ ] M1 and M2 numbers are pasted in.
- [ ] At most 400 changed lines, excluding fixtures.
- [ ] No new heuristics, no regexes over source text, and no `tree_sitter` import outside `visit_capture`.
