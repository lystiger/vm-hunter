#!/usr/bin/env python3
"""Static checks for the exported n8n workflows in workflows/ (Python standard library only).

check_files(paths, env_example_path) returns error strings shaped '<file>: <rule>: <detail>'.
main() checks workflows/ by default, prints PASS/FAIL lines and exits 0 or 1.

Rule ids (stable; tests and the verifier refer to them):
  missing-path                 a path given to the checker does not exist
  unreadable-file              a workflow file cannot be read
  invalid-json                 the file is not valid JSON
  missing-field                id/name/nodes/connections missing or of the wrong type
  active-not-false             "active" is not exactly false
  duplicate-workflow-id        the same workflow id in two files
  duplicate-node-name          the same node name twice in one file
  connection-shape             a connection entry is not the expected list/dict structure
  dangling-connection          a connection source or target names a node that does not exist
  secret-value                 a value looks like a secret (webhook token, PEM, fingerprint, long run)
  env-example-missing          the .env.example file cannot be read
  undeclared-env               $env.NAME used but NAME is not listed in .env.example
  oracle-launch-without-cost-guard
                               an Oracle instance launch is not reached from the cost-guard node
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOWS = ROOT / "workflows"
DEFAULT_ENV_EXAMPLE = ROOT / ".env.example"

# Secret patterns. Thresholds: a hex run of 40+ characters (SHA-1 length and longer), a base64 run of
# 40+ characters that mixes letters and digits. A UUID is never reported, with or without hyphens.
DISCORD_WEBHOOK = re.compile(r"https://discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d{17,20}/[A-Za-z0-9_-]{20,}")
PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]+-----")
OCI_FINGERPRINT = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){15}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")
HEX_RUN = re.compile(r"[0-9A-Fa-f]{40,}")
BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
UUID = re.compile(r"[0-9A-Fa-f]{8}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{12}")

ENV_REF = re.compile(r"\$env(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\])")
LAUNCH_PATH = re.compile(r"/20160918/instances(?![\w/-])")
COST_GUARD_NAME = re.compile(r"cost[\s_-]*guard", re.IGNORECASE)


def collect_files(paths):
    """Expand directories to their *.json files. Returns (files, errors)."""
    files, errors = [], []
    for given in paths:
        path = Path(given)
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            files.append(path)
        else:
            errors.append(f"{path}: missing-path: no such file or directory")
    return files, errors


def load_env_names(env_example_path):
    """Names declared in .env.example (lines 'NAME=...', comments and blanks skipped), or None if unreadable."""
    try:
        text = Path(env_example_path).read_text(encoding="utf-8")
    except OSError:
        return None
    names = set()
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            names.add(line.partition("=")[0].strip())
    return names


def walk_strings(value, path="$"):
    """Yield (location, string) for every string in a JSON value, including object keys."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield path, key
            yield from walk_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")


def secret_kinds(text):
    """Describe each kind of secret-like value found in one string. The value itself is never reported."""
    kinds = []
    if DISCORD_WEBHOOK.search(text):
        kinds.append("Discord webhook URL with id and token")
    if PEM_BEGIN.search(text):
        kinds.append("PEM block")
    if OCI_FINGERPRINT.search(text):
        kinds.append("OCI key fingerprint")
    if any(not UUID.fullmatch(run) for run in HEX_RUN.findall(text)):
        kinds.append("long hex run")
    for run in BASE64_RUN.findall(text):
        if re.search(r"[A-Za-z]", run) and re.search(r"[0-9]", run):
            kinds.append("long base64 run")
            break
    return kinds


def is_oracle_launch(node):
    """True for a node that POSTs to /20160918/instances (an HTTP node) or a Code node that does."""
    params = node.get("parameters") or {}
    if not isinstance(params, dict):
        return False
    strings = [text for _, text in walk_strings(params)]
    method = str(params.get("method", "GET")).upper()
    if method == "POST" and any(LAUNCH_PATH.search(text) for text in strings):
        return True
    code = params.get("jsCode", "")
    return isinstance(code, str) and "POST" in code and bool(LAUNCH_PATH.search(code))


def check_document(name, doc, env_names):
    """All rules that look at one file on its own. Returns a list of error strings."""
    errors = []

    def err(rule, detail):
        errors.append(f"{name}: {rule}: {detail}")

    if not isinstance(doc, dict):
        err("missing-field", "top level is not a JSON object")
        return errors
    for key, kind in (("id", str), ("name", str), ("nodes", list), ("connections", dict)):
        if key not in doc:
            err("missing-field", f"'{key}' is missing")
        elif not isinstance(doc[key], kind) or (kind is str and not doc[key]):
            err("missing-field", f"'{key}' must be a non-empty {kind.__name__}" if kind is str
                else f"'{key}' must be a {kind.__name__}")

    if doc.get("active") is not False:
        err("active-not-false", f"'active' is {doc.get('active', 'missing')!r}, must be false")

    nodes = [node for node in doc.get("nodes", []) if isinstance(node, dict)] if isinstance(doc.get("nodes"), list) else []
    names = set()
    for index, node in enumerate(nodes):
        node_name = node.get("name")
        if not isinstance(node_name, str) or not node_name:
            err("missing-field", f"nodes[{index}] has no name")
        elif node_name in names:
            err("duplicate-node-name", f"'{node_name}' appears more than once")
        else:
            names.add(node_name)

    edges = {}
    connections = doc.get("connections")
    if isinstance(connections, dict):
        for source, outputs in connections.items():
            if source not in names:
                err("dangling-connection", f"connection source '{source}' is not a node")
            if not isinstance(outputs, dict):
                err("connection-shape", f"connections['{source}'] must be an object of output types")
                continue
            for output_type, slots in outputs.items():
                if not isinstance(slots, list):
                    err("connection-shape", f"connections['{source}'].{output_type} must be a list of slots")
                    continue
                for slot_index, slot in enumerate(slots):
                    if slot is None:
                        continue
                    if not isinstance(slot, list):
                        err("connection-shape", f"'{source}' {output_type}[{slot_index}] must be a list")
                        continue
                    for target in slot:
                        target_name = target.get("node") if isinstance(target, dict) else None
                        if not isinstance(target_name, str) or target_name not in names:
                            err("dangling-connection",
                                f"'{source}' {output_type}[{slot_index}] targets missing node {target_name!r}")
                        elif source in names:
                            edges.setdefault(source, set()).add(target_name)

    launches = [node["name"] for node in nodes if is_oracle_launch(node) and isinstance(node.get("name"), str)]
    guards = [node_name for node_name in names if COST_GUARD_NAME.search(node_name)]
    reached = set(guards)
    stack = list(guards)
    while stack:
        for nxt in edges.get(stack.pop(), ()):
            if nxt not in reached:
                reached.add(nxt)
                stack.append(nxt)
    for launch in launches:
        if not guards:
            err("oracle-launch-without-cost-guard", f"Oracle launch '{launch}' and no cost-guard node exists")
        elif launch not in reached:
            err("oracle-launch-without-cost-guard",
                f"Oracle launch '{launch}' is not reached from the cost-guard node")

    for location, text in walk_strings(doc):
        for kind in secret_kinds(text):
            err("secret-value", f"{kind} at {location}")
        if env_names is not None:
            for match in ENV_REF.finditer(text):
                env_name = match.group(1) or match.group(2)
                if env_name not in env_names:
                    err("undeclared-env", f"${env_name} at {location} is not listed in .env.example")
    return errors


def run_checks(paths, env_example_path):
    """Check the given files and directories. Returns (files checked, error strings)."""
    files, errors = collect_files(paths)
    env_names = load_env_names(env_example_path)
    if env_names is None:
        errors.append(f"{env_example_path}: env-example-missing: cannot read the file")

    seen_ids = {}
    for path in files:
        name = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{name}: unreadable-file: {exc.strerror}")
            continue
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"{name}: invalid-json: line {exc.lineno}: {exc.msg}")
            continue
        errors.extend(check_document(name, doc, env_names))
        workflow_id = doc.get("id") if isinstance(doc, dict) else None
        if isinstance(workflow_id, str) and workflow_id:
            if workflow_id in seen_ids and seen_ids[workflow_id] != name:
                errors.append(f"{name}: duplicate-workflow-id: '{workflow_id}' is also in {seen_ids[workflow_id]}")
            else:
                seen_ids.setdefault(workflow_id, name)
    return files, errors


def check_files(paths, env_example_path):
    """Error strings for the given workflow files (or directories). An empty result means pass."""
    return run_checks(paths, env_example_path)[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Static checks for workflows/*.json.")
    parser.add_argument("paths", nargs="*", type=Path,
                        help="workflow files or directories (default: the repository's workflows/)")
    parser.add_argument("--env-example", type=Path, default=DEFAULT_ENV_EXAMPLE,
                        help="file listing every environment variable name (default: repository .env.example)")
    args = parser.parse_args(argv)

    files, errors = run_checks(args.paths or [DEFAULT_WORKFLOWS], args.env_example)
    attributed = set()
    for path in files:
        mine = [error for error in errors if error.startswith(f"{path}: ")]
        attributed.update(mine)
        if mine:
            for error in mine:
                print(f"FAIL {error}")
        else:
            print(f"PASS {path}")
    for error in errors:
        if error not in attributed:
            print(f"FAIL {error}")

    if errors:
        print(f"FAIL: {len(errors)} problem(s) in {len(files)} workflow file(s)")
        return 1
    print(f"PASS: {len(files)} workflow file(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
