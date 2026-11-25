"""Bind new workflow artifact basenames without touching files or templates."""
from __future__ import annotations

from copy import deepcopy
from pathlib import PureWindowsPath
import re


_DECLARATION = re.compile(r"(?:目标产出文件|目标文件|产物路径)\s*[:：]\s*([^\r\n]+)")
_COMPILER_LINE = re.compile(r"^(目标产出文件[:：]\s*)([^\r\n]+)(\r?\n)?$")
_SEPARATOR = re.compile(r"[、,，；]\s*")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)", re.I)


def _path_key(relative: str) -> tuple[str, ...]:
    value = relative.replace("\\", "/")
    path = PureWindowsPath(value)
    parts = value.split("/")
    if not value or path.drive or path.is_absolute():
        raise ValueError(f"托管产物必须使用项目内相对路径：{relative}")
    for part in parts:
        if (not part or part.startswith(".") or part.endswith((".", " ")) or
                len(part.encode("utf-16-le")) > 510 or
                any(ord(c) < 32 or c in '<>:"|?*' for c in part) or
                _DEVICE.fullmatch(part.split(".", 1)[0].rstrip(" "))):
            raise ValueError(f"产物路径包含无效的 Windows 文件名：{relative}")
    return tuple(part.casefold() for part in parts)


def _relative(item) -> str:
    if isinstance(item, dict):
        relative, path = item.get("relative"), item.get("path")
        if relative and path and relative != path:
            raise ValueError("产物 relative 和 path 声明不一致")
        item = relative or path
    if not isinstance(item, str):
        raise ValueError("产物路径必须是文本")
    _path_key(item)
    return item


def _rewrite_prompt(prompt: str, mapping: dict[str, str]) -> str:
    lines = []
    for line in prompt.splitlines(keepends=True):
        declarations = list(_DECLARATION.finditer(line))
        if any(mapping[path] != path for path in mapping):
            if any(path not in mapping for match in declarations
                   for path in _SEPARATOR.split(match[1].strip())):
                raise ValueError("托管产物的目标声明包含未声明路径或其他说明，请单独声明目标文件")
        changed = any(mapping.get(path) not in (None, path)
                      for match in declarations
                      for path in _SEPARATOR.split(match[1].strip()))
        if not changed:
            lines.append(line)
            continue
        owned = _COMPILER_LINE.fullmatch(line)
        paths = _SEPARATOR.split(owned[2].strip()) if owned else []
        if not owned or any(path not in mapping for path in paths):
            raise ValueError("托管产物的目标声明存在歧义；请仅使用独立的“目标产出文件”声明行")
        lines.append(owned[1] + "、".join(mapping[path] for path in paths) + (owned[3] or ""))
    return "".join(lines)


def bind_managed_paths(layout: dict, *, run_segment: str) -> dict:
    """Copy a standard DAG and bind bare outputs to intermediate/final paths.

    Directory-qualified paths and edge artifact basenames remain unchanged.
    A standalone compiler-shaped target declaration may be rewritten only when
    every path on it belongs to the node's declared outputs.
    """
    if not isinstance(run_segment, str) or len(_path_key(run_segment)) != 1:
        raise ValueError("托管运行目录必须是单个安全路径段")
    result = deepcopy(layout)
    nonterminals = {edge["source"] for edge in result.get("edges", [])}
    targets = {}
    node_names = set()
    for node in result["nodes"]:
        node_id = node["id"]
        node_key = _path_key(node_id)
        if len(node_key) != 1:
            raise ValueError("托管节点目录必须是单个安全路径段")
        if node_key in node_names:
            raise ValueError(f"托管节点目录存在 Windows 大小写冲突：{node_id}")
        node_names.add(node_key)
        contract = node.get("output_contract") or {}
        declared = []
        for values in (contract.get("required_files", []), node.get("outputs", [])):
            if not isinstance(values, list):
                raise ValueError("产物路径必须用数组声明")
            declared.extend(_relative(item) for item in values)
        declared = list(dict.fromkeys(declared))
        mapping = {}
        for relative in declared:
            if len(_path_key(relative)) == 1:
                folder = (f"研究过程/{run_segment}/{node_id}" if node_id in nonterminals
                          else f"最终产物/{run_segment}")
                target = f"{folder}/{relative}"
            else:
                target = relative
            key = _path_key(target)
            for previous, owner in targets.items():
                if key[:len(previous)] == previous or previous[:len(key)] == key:
                    raise ValueError(f"产物路径冲突：{owner} 与 {node_id}/{relative}")
            targets[key] = f"{node_id}/{relative}"
            mapping[relative] = target
        if not declared:
            continue
        contract["required_files"] = [mapping[path] for path in declared]
        if "file_rules" in contract:
            rules = contract["file_rules"]
            if not isinstance(rules, dict) or any(path not in mapping for path in rules):
                raise ValueError("file_rules 必须按声明的产物路径设置")
            contract["file_rules"] = {mapping[path]: rule for path, rule in rules.items()}
        node["output_contract"] = contract
        if "outputs" in node:
            node["outputs"] = [mapping[_relative(item)] for item in node["outputs"]]
        for field in ("prompt", "goal"):
            if node.get(field):
                node[field] = _rewrite_prompt(node[field], mapping)
    return result
