"""Finite response-linked purpose rules ported from the accepted donor. No execution."""
import json
from pathlib import PurePosixPath as Path
import re
import shlex

from .domain import canonical
from .invocations import CLASSIFICATION_VERSION


def _path_purpose(value, repository_root):
    path = value.strip('"\'').replace("\\", "/")
    root = str(repository_root).replace("\\", "/").rstrip("/")
    if path.startswith(root + "/"):
        path = path[len(root) + 1:]
    path = path.removeprefix("./")
    if path.startswith("worktrees/"):
        parts = path.split("/", 2)
        path = parts[2] if len(parts) == 3 else ""
    if path.startswith(("tasks/", ".runtime/", ".poise/", "var/")) or "/standalones/" in path:
        return "auxiliary"
    if path.startswith("/") or re.match(r"^[A-Za-z]:/", path) or ".." in path.split("/"):
        return "unclassified"
    if "/" in path or Path(path).suffix or path == "AGENTS.md":
        return "main"
    return "unclassified"


def _command_purpose(command, repository_root):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        segments = [[]]
        for word in lexer:
            if word and set(word) <= set(";&|"):
                segments.append([])
            else:
                segments[-1].append(word)
        segments = [words for words in segments if words]
    except ValueError:
        return "unclassified"
    if len(segments) > 1:
        purposes = {_command_purpose(shlex.join(words), repository_root) for words in segments}
        return "main" if "main" in purposes else "auxiliary" if purposes == {"auxiliary"} else "unclassified"
    words = segments[0] if segments else []
    if not words:
        return "unclassified"
    executable = Path(words[0]).name
    if executable in ("bash", "sh") and len(words) == 3 and words[1] in ("-c", "-lc"):
        return _command_purpose(words[2], repository_root)
    position = 1 if executable in ("bash", "sh") and len(words) > 1 else 0
    owner = words[position]
    if owner in ("work.sh", "task-management/task-ctl", "task-management/exec-ctl", "task-management/req-ctl"):
        return "auxiliary"
    if executable in ("pytest", "phpunit", "vitest", "playwright"):
        return "main"
    if executable in ("npm", "pnpm", "composer") and any(w in ("test", "check", "lint", "typecheck") for w in words[1:]):
        return "main"
    if executable in ("rg", "cat", "head", "tail", "sed", "ls"):
        paths = [w for w in words[1:] if not w.startswith("-") and ("/" in w or Path(w).suffix)]
        purposes = {_path_purpose(w, repository_root) for w in paths}
        if "main" in purposes:
            return "main"
        if purposes == {"auxiliary"}:
            return "auxiliary"
    return "unclassified"


def _explicit_exec_commands(script):
    """Read a supported literal cmd at an unquoted call site; never evaluate JS."""
    commands, index = [], 0
    pattern = re.compile(r'(?<![A-Za-z0-9_$\.])tools\.exec_command\(\s*\{\s*"?cmd"?\s*:\s*("(?:[^"\\]|\\.)*")\s*[,}]')
    while index < len(script):
        if script.startswith("//", index):
            end = script.find("\n", index)
            index = len(script) if end < 0 else end + 1
        elif script.startswith("/*", index):
            end = script.find("*/", index + 2)
            if end < 0:
                return []
            index = end + 2
        elif script[index] in ('"', "'", "`"):
            quote = script[index]
            index += 1
            while index < len(script):
                char = script[index]
                index += 1
                if char == "\\":
                    index += 1
                elif char == quote:
                    break
        elif script[index] == "/":
            return []  # Regex/arithmetic syntax is outside this literal contract.
        else:
            match = pattern.match(script, index)
            if match:
                try:
                    commands.append(json.loads(match[1]))
                except json.JSONDecodeError:
                    return []
                index = match.end()
            else:
                index += 1
    return commands


def classify_invocation_purpose(tools, *, repository_root):
    decisions = []
    reasons = []
    identities = {}
    for tool in tools:
        call_id = tool.get("call_id")
        if isinstance(call_id, str) and call_id:
            identities.setdefault(call_id, set()).add(canonical([tool.get("name"), tool.get("arguments")]))
    if any(len(variants) > 1 for variants in identities.values()):
        return {"classification": "unclassified", "classification_version": CLASSIFICATION_VERSION,
                "classification_reason": "Conflicting arguments for the same actual tool call ID."}
    for tool in tools:
        name, arguments = tool.get("name", ""), tool.get("arguments", "")
        if not isinstance(arguments, str) or not isinstance(name, str):
            continue
        if name in ("apply_patch", "functions.apply_patch"):
            paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", arguments, re.M)
            for path in paths:
                decisions.append(_path_purpose(path, repository_root))
                reasons.append("explicit patch path: " + path)
        elif name.endswith(("send_message_to_thread", "wait_threads", "send_message", "wait_agent")):
            decisions.append("auxiliary")
            reasons.append("explicit coordination tool: " + name)
        elif name.endswith("mcp__repo_checks__run"):
            decisions.append("main")
            reasons.append("explicit repository check")
        elif name in ("exec", "exec_command", "functions.exec", "functions.exec_command"):
            commands = []
            try:
                value = json.loads(arguments)
                if isinstance(value, dict) and isinstance(value.get("cmd"), str):
                    commands.append(value["cmd"])
            except json.JSONDecodeError:
                pass
            # Only an explicit supported call with a JSON string cmd is read.
            # This is not evaluation of JS, shell variables or arbitrary code.
            commands.extend(_explicit_exec_commands(arguments))
            for command in commands:
                decisions.append(_command_purpose(command, repository_root))
                reasons.append("explicit supported command")
    category = "main" if "main" in decisions else (
        "auxiliary" if decisions and set(decisions) == {"auxiliary"} else "unclassified")
    return {"classification": category, "classification_version": CLASSIFICATION_VERSION,
            "classification_reason": "; ".join(reasons) if reasons else "No verified response-linked purpose evidence."}
