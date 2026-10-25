"""Repository config: `.spendlint.yml` / `.spendlint.yaml` / `.spendlint.json`.

    disable: [R-OUT-001, R-MODEL-001]     # rules to turn off
    ignore_paths:                         # globs, relative to the scanned path
      - legacy/*
      - generated/
    min_confidence: 0.5

YAML is read with PyYAML when it is installed (`pip install spendlint[yaml]`); otherwise a small built-in
reader handles the flat subset above (scalars, `[a, b]` lists and `- item` lists). Nothing else is required.
"""

import json
from pathlib import Path

from .meta import RULES

CONFIG_NAMES = (".spendlint.yml", ".spendlint.yaml", ".spendlint.json")
KNOWN_KEYS = {"disable", "ignore_paths", "min_confidence"}


class ConfigError(ValueError):
    pass


def find_config(root):
    """First config file found in the scanned directory, or None."""
    root = Path(root)
    if root.is_file():
        root = root.parent
    for name in CONFIG_NAMES:
        p = root / name
        if p.is_file():
            return p
    return None


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return float(v) if any(c in v for c in ".eE") else int(v)
    except ValueError:
        return v


def _strip_comment(line):
    out, quote = [], None
    for i, c in enumerate(line):
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(c)
    return "".join(out).rstrip()


def parse_simple_yaml(text):
    """Flat mapping of scalars and lists. Raises ConfigError on anything it does not understand."""
    data, current = {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line.strip() or line.strip() == "---":
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current is None or not isinstance(data.get(current), list):
                raise ConfigError(f"line {n}: list item outside a list")
            data[current].append(_scalar(stripped[2:]))
            continue
        if line[0] in " \t" or ":" not in line:
            raise ConfigError(f"line {n}: unsupported YAML (install PyYAML or use .spendlint.json)")
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [_scalar(x) for x in value[1:-1].split(",") if x.strip()]
            current = None
        elif value == "":
            data[key] = []
            current = key
        else:
            data[key] = _scalar(value)
            current = None
    return data


def load_config(path):
    """Read and validate a config file. Returns a dict with keys disable (set), ignore_paths (list), min_confidence."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read {path.name}: {e.strerror}") from None
    if path.suffix == ".json":
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path.name}: invalid JSON ({e.msg} at line {e.lineno})") from None
    else:
        try:
            import yaml  # optional
        except ImportError:
            data = parse_simple_yaml(text)
        else:
            try:
                data = yaml.safe_load(text) or {}
            except yaml.YAMLError as e:
                raise ConfigError(f"{path.name}: invalid YAML ({e})") from None
    return validate(data, path.name)


def validate(data, name="config"):
    if not isinstance(data, dict):
        raise ConfigError(f"{name}: top level must be a mapping")
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise ConfigError(
            f"{name}: unknown key(s) {', '.join(sorted(unknown))}; expected {', '.join(sorted(KNOWN_KEYS))}"
        )
    disable = data.get("disable") or []
    if isinstance(disable, str):
        disable = [x for x in disable.replace(",", " ").split() if x]
    bad = [r for r in disable if r not in RULES]
    if bad:
        raise ConfigError(f"{name}: unknown rule id(s) in disable: {', '.join(map(str, bad))}")
    ignore = data.get("ignore_paths") or []
    if isinstance(ignore, str):
        ignore = [ignore]
    if not all(isinstance(x, str) for x in ignore):
        raise ConfigError(f"{name}: ignore_paths must be a list of glob strings")
    mc = data.get("min_confidence")
    if mc is not None:
        if isinstance(mc, bool) or not isinstance(mc, (int, float)) or not 0 <= mc <= 1:
            raise ConfigError(f"{name}: min_confidence must be a number between 0 and 1")
        mc = float(mc)
    return {"disable": set(disable), "ignore_paths": list(ignore), "min_confidence": mc}
