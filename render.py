"""Render a Yomitan structured-content glossary to plain text.

The tree mimics HTML but the useful handle is data.content, which labels each
node's role:

    ul[sense-groups] > li[sense-group] > span[part-of-speech-info]
                                       > ol > li[sense] > span[*-info]
                                                        > ul[glossary] > li
    div[attribution]

So a group shares a part of speech, and each sense carries its own field/misc
tags. Rendering those as structure rather than running them together is the whole
difference between a usable entry and a wall of words.
"""
import re

TAG_ROLES = ("field-info", "misc-info", "dialect-info", "part-of-speech-info")

def _role(n):
    return n.get("data", {}).get("content") if isinstance(n, dict) else None

def _inline(n, out):
    if n is None:
        return
    if isinstance(n, str):
        out.append(n)
    elif isinstance(n, list):
        for x in n:
            _inline(x, out)
    elif isinstance(n, dict):
        t = n.get("type")
        if t == "text":
            out.append(n.get("text", ""))
        elif t == "image":
            out.append("[img]")
        else:
            _inline(n.get("content"), out)

def _txt(n):
    out = []
    _inline(n, out)
    return re.sub(r"\s+", " ", "".join(out)).strip()

def _find(n, role, acc, stop_at=None):
    """Depth-first collect of nodes whose data.content == role."""
    if isinstance(n, list):
        for x in n:
            _find(x, role, acc, stop_at)
    elif isinstance(n, dict):
        r = _role(n)
        if r == role:
            acc.append(n)
            return                      # don't nest a role inside itself
        if stop_at and r == stop_at:
            return
        _find(n.get("content"), role, acc, stop_at)
    return acc

def _children(node, tag):
    """Immediate-ish children with a given html tag (content may be one or many)."""
    c = node.get("content")
    items = c if isinstance(c, list) else ([c] if c else [])
    out = []
    for i in items:
        if isinstance(i, dict) and i.get("tag") == tag:
            out.append(i)
    return out

def _glossary_items(sense):
    out = []
    for g in _find(sense, "glossary", []):
        lis = _children(g, "li")
        out.extend(_txt(li) for li in (lis or [g]))
    return [s for s in out if s]

def render(glossary, indent="  "):
    """glossary (the 6th field of a term entry) -> plain text block."""
    lines = []
    for g in glossary or []:
        if isinstance(g, str):
            lines.append(g.strip())
            continue
        if not isinstance(g, dict):
            continue
        if g.get("type") == "image":
            lines.append("[img]")
            continue
        sc = g.get("content", g)
        groups = _find(sc, "sense-group", [])
        if not groups:                               # older/simpler dictionaries
            t = _txt(sc)
            if t:
                lines.append(t)
        else:
            n = 0
            for grp in groups:
                pos = [_txt(p) for p in _find(grp, "part-of-speech-info", [])]
                pos = [p for p in pos if p]
                if pos:
                    lines.append(", ".join(dict.fromkeys(pos)))
                senses = _find(grp, "sense", [])
                for s in senses or [grp]:
                    n += 1
                    tags = []
                    for role in TAG_ROLES[:3]:
                        tags += [_txt(x) for x in _find(s, role, [])]
                    tags = [t for t in dict.fromkeys(tags) if t]
                    body = "; ".join(_glossary_items(s)) or _txt(s)
                    pre = f"[{', '.join(tags)}] " if tags else ""
                    lines.append(f"{indent}{n}. {pre}{body}".rstrip())
        for a in _find(sc, "attribution", []):
            t = _txt(a)
            if t:
                lines.append(f"{indent}— {t}")
    out = [l for l in (x.rstrip() for x in lines) if l]
    return "\n".join(out)
