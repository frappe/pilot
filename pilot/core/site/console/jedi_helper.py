import json
import re
import select
import sys

import jedi

# The bench interpreter itself; Jedi would otherwise follow an inherited VIRTUAL_ENV.
ENVIRONMENT = jedi.InterpreterEnvironment()
TYPES = {
    "module": "namespace",
    "class": "class",
    "function": "function",
    "keyword": "keyword",
    "property": "property",
}
DOC_PARAM = re.compile(r":param (\w+):(.*)")
DOCUMENT = re.compile(
    r"(\s*)(\w+)(?=\s*=\s*frappe\.(?:get_doc|new_doc|get_cached_doc|get_last_doc)\(\s*[\"']([^\"'\n]+))"
)


def complete(code, line, column, controllers, name=None):
    """Completions at the cursor, or the docstring of `name`. `controllers` maps a doctype to its controller class,
    so `doc = frappe.get_doc("ToDo", ...)` is typed as ToDo."""
    lines = code.split("\n")
    # The console always has `frappe` in scope, even when the code does not import it.
    imports = ["import frappe"]
    for index, text in enumerate(lines):
        match = DOCUMENT.match(text)
        if match and index + 1 != line and match[3] in controllers:
            module, _, controller = controllers[match[3]].rpartition(".")
            imports.append(f"from {module} import {controller} as _Controller{index}")
            lines[index] = f"{match[1]}{match[2]}: _Controller{index}{text[match.end() :]}"
    source = imports + lines
    line += len(imports)
    script = jedi.Script("\n".join(source), environment=ENVIRONMENT)
    found = script.complete(line, column)
    if name is not None:
        return next((item.docstring() for item in found if item.name == name), "")
    typed = re.search(r"\w*$", source[line - 1][:column])[0].lower()
    params, documented = get_params(script.get_signatures(line, column), source, (line, column), typed)
    completions = [
        {
            "label": item.name,
            "type": TYPES.get(item.type, "variable"),
            "detail": params.get(item.name, item.type),
        }
        for item in found
    ]
    completions += [{"label": label, "type": "variable", "detail": params[label]} for label in documented]
    # Right after `(` or `,` only the call's parameters help; names in scope come once a word is typed.
    if source[line - 1][:column].rstrip().endswith(("(", ",")):
        completions = [item for item in completions if item["label"] in params]
    # Jedi already puts private and dunder names last; parameters of the call around the cursor go first.
    order = list(params)
    completions.sort(key=lambda item: order.index(item["label"]) if item["label"] in params else len(order))
    return completions


# `name=` labels with their details. Jedi cannot see keyword arguments behind **kwargs, as in frappe.get_all,
# so names a `:param name:` docstring line gives come first, unless the call already passes them.
def get_params(signatures, source, cursor, typed):
    """The parameters of the call at the cursor, with the ones already typed left out."""
    params, documented = {}, []
    for signature in signatures:
        names = {param.name for param in signature.params}
        call = get_text(source, signature.bracket_start, cursor)
        for name, text in DOC_PARAM.findall(signature.docstring(raw=True)):
            label = f"{name}="
            if name in names or label in params or re.search(rf"\b{name}\s*=", call):
                continue
            params[label] = text.strip()
            if label.lower().startswith(typed):
                documented.append(label)
        for param in signature.params:
            params.setdefault(f"{param.name}=", param.to_string())
    return params, documented


# Text between two 1-based line, 0-based column positions.
def get_text(source, start, end):
    """The source text between two positions."""
    chunk = source[start[0] - 1 : end[0]]
    chunk[-1] = chunk[-1][: end[1]]
    chunk[0] = chunk[0][start[1] :]
    return "\n".join(chunk)


while select.select([sys.stdin], [], [], 300)[0]:
    request = sys.stdin.readline()
    if not request:
        break
    try:
        reply = complete(**json.loads(request))
    except Exception:
        reply = []
    print(json.dumps(reply), flush=True)
