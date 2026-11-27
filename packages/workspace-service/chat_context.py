"""Select recent conversation and on-topic excerpts within a history budget."""
from __future__ import annotations

import re
from typing import Any


_TERMS = re.compile(r"[a-z][a-z0-9]+(?:[_.-][a-z0-9]+)*|\d+(?:\.\d+)?|[\u3400-\u9fff]+")
_OMIT = {"the", "and", "for", "what", "about", "please", "之前", "刚才", "上面",
         "那个", "这个", "一下", "什么", "怎么", "请问", "帮我", "继续", "我们", "是否"}
_GAP = "\n[…]\n"


def _query_terms(query: str) -> set[str]:
    request = re.split(r"\n\[(?:附件)\]|\n【(?:当前项目明确引用的材料|@上下文)】", query, maxsplit=1)[0]
    terms = set()
    for value in _TERMS.findall(request[:800].lower()):
        if "\u3400" <= value[0] <= "\u9fff":
            terms.update(value[i:i + 2] for i in range(len(value) - 1))
        else:
            terms.add(value)
    return terms - _OMIT


def _excerpt(content: str, limit: int, match_at: int | None = None) -> str:
    if len(content) <= limit:
        return content
    if limit <= 2 * len(_GAP):
        return content[:limit]
    if match_at is None:
        head = (limit - len(_GAP)) * 2 // 3
        tail = limit - len(_GAP) - head
        return content[:head] + _GAP + content[-tail:]
    length = limit - 2 * len(_GAP)
    start = min(max(0, match_at - length // 3), len(content) - length)
    end = start + length
    return (_GAP.lstrip() if start else "") + content[start:end] + (_GAP.rstrip() if end < len(content) else "")


def select(messages: list[dict[str, Any]], query: str, budget: int | None) -> list[dict[str, str]]:
    """Keep recent turns and at most three relevant excerpts from the same conversation."""
    history = [(m["role"], m["content"]) for m in messages
               if m.get("role") in {"user", "assistant"} and m.get("content")]
    if budget is None or sum(len(text) for _, text in history) <= budget:
        return [{"role": role, "content": text} for role, text in history]
    recent = history[-8:]
    per_message = max(500, min(6_000, budget // 4))
    needs_selection = len(history) > len(recent) or sum(min(per_message, len(text)) for _, text in recent) > budget
    recent_budget = budget * 3 // 4 if needs_selection else budget
    selected = []
    for role, content in reversed(recent):
        limit = min(recent_budget, per_message)
        if limit <= 0:
            break
        text = _excerpt(content, limit)
        selected.append({"role": role, "content": text})
        recent_budget -= len(text)

    older = history[:len(history) - len(selected)]
    terms = _query_terms(query)
    available = min(2_700, budget - sum(len(m["content"]) for m in selected))
    if not older or not terms or available < 200:
        return selected[::-1]

    alternatives = "|".join(
        re.escape(term) if "\u3400" <= term[0] <= "\u9fff"
        else r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        for term in sorted(terms, key=lambda term: (-len(term), term))
    )
    pattern = re.compile(r"(?=(" + alternatives + r"))", re.IGNORECASE)
    ranked = []
    for index, (role, content) in enumerate(older):
        matches = {}
        for match in pattern.finditer(content):
            matches.setdefault(match[1].lower(), match.start())
            if len(matches) == len(terms):
                break
        if matches:
            anchor = max(matches, key=len)
            score = sum(2 if term.isascii() else 1 for term in matches)
            ranked.append((score, index, role, content, matches[anchor]))

    excerpts = []
    for _, index, role, content, anchor in sorted(ranked, reverse=True)[:3]:
        if available < 200:
            break
        text = _excerpt(content, min(900, available), anchor)
        excerpts.append((index, {"role": role, "content": text}))
        available -= len(text)
    return [message for _, message in sorted(excerpts)] + selected[::-1]
