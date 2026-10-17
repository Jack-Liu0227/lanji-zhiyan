"""Connector tools shared by assistants, leaders, team members and workflows."""
from __future__ import annotations

from typing import Any

import feishu
import mail
import zotero_sync


def tool_specs() -> list[dict]:
    definitions = [
        ("mail_accounts", "列出已配置邮箱及其账户ID，不含密码。", {}),
        ("mail_list", "读取邮箱邮件列表。", {"account_id": "string", "folder": "string", "page": "integer"}),
        ("mail_read", "按UID读取指定邮件正文。", {"account_id": "string", "folder": "string", "uid": "string"}),
        ("mail_send", "发送邮件（外发，需用户明确授权confirm；只读权限禁止）。", {"account_id": "string", "to": "string", "cc": "string", "subject": "string", "text": "string", "confirm": "boolean"}),
        ("feishu_send", "使用当前助手已配置的飞书机器人发送消息（外发需确认）。", {"text": "string", "receive_id": "string", "confirm": "boolean"}),
        ("zotero_collections", "读取已配置 Zotero Web API 的全部集合。", {}),
        ("zotero_write", "写入 Zotero 条目，修改需提供key/version；外写需确认。", {"item": "object", "confirm": "boolean"}),
        ("zotero_sync", "预览或执行项目与绑定Collection的元数据同步；默认只预览，执行需plan_token与确认。", {"task_id": "string", "mode": "string", "dry_run": "boolean", "plan_token": "string", "confirm": "boolean"}),
    ]
    return [{"type": "function", "function": {"name": name, "description": desc,
             "parameters": {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "additionalProperties": False}}}
            for name, desc, props in definitions]


def invoke(name: str, db: Any, task_id: str, args: dict, assistant_id: str = "", permission: str = "auto") -> dict:
    external_write = name in {"mail_send", "feishu_send", "zotero_write"} or (name == "zotero_sync" and args.get("dry_run") is False)
    if external_write:
        if permission in {"read_only", "read-only"}:
            raise ValueError("当前助手为只读权限，禁止外发/写入")
        if permission not in {"full_access", "full-access"} and args.get("confirm") is not True:
            raise ValueError("此操作会外发或修改远端数据，请取得用户明确授权后传入 confirm=true")
    if name == "mail_accounts":
        return {"accounts": [mail.public_account(a) for a in mail.list_mail_accounts()]}
    if name in {"mail_list", "mail_read", "mail_send"}:
        account = mail.get_mail_account(str(args.get("account_id") or ""))
        if not account:
            raise ValueError("邮箱账户不存在，请先调用 mail_accounts 获取账户ID")
        if name == "mail_list":
            return mail.list_messages(account, str(args.get("folder") or "INBOX"), max(1, int(args.get("page") or 1)), 20)
        if name == "mail_read":
            if not args.get("uid"):
                raise ValueError("读取邮件需指定 uid")
            return mail.get_message(account, str(args.get("folder") or "INBOX"), str(args["uid"]))
        if not str(args.get("to") or "").strip():
            raise ValueError("收件人不能为空")
        return mail.send_mail(account, str(args["to"]), str(args.get("cc") or ""), str(args.get("subject") or ""), str(args.get("text") or ""))
    if name == "feishu_send":
        return feishu.send(db, assistant_id, str(args.get("text") or ""), str(args.get("receive_id") or ""))
    if name == "zotero_collections":
        return {"collections": zotero_sync.all_pages(zotero_sync.config(db), "/collections")}
    if name == "zotero_write":
        return zotero_sync.write_item(db, args.get("item") or {})
    if name == "zotero_sync":
        return zotero_sync.sync(db, str(args.get("task_id") or task_id), str(args.get("mode") or "merge"),
                                args.get("dry_run") is not False, str(args.get("plan_token") or ""))
    raise ValueError("未知连接器工具：" + name)
