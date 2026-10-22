"""Connector tools shared by assistants, leaders, team members and workflows."""
from __future__ import annotations

from typing import Any

import feishu
import mail
import zotero
import zotero_sync


def tool_specs() -> list[dict]:
    definitions = [
        ("mail_accounts", "列出已配置邮箱及其账户ID，不含密码。", {}),
        ("mail_list", "读取邮箱邮件列表。", {"account_id": "string", "folder": "string", "page": "integer"}),
        ("mail_read", "按UID读取指定邮件正文。", {"account_id": "string", "folder": "string", "uid": "string"}),
        ("zotero_search", "通过本机 API 查询 Zotero 题录，不修改数据库或附件。", {"query": "string", "start": "integer", "limit": "integer"}),
        ("zotero_item", "通过本机 API 读取指定 Zotero 条目的题录。", {"key": "string"}),
        ("zotero_import_pdf", "将 Zotero 条目的 PDF 复制并入库到当前项目，返回 paper_id 和副本路径；原附件始终只读。", {"key": "string"}),
        ("mail_send", "Send an email; requires user enabled Full Access", {"account_id": "string", "to": "string", "cc": "string", "subject": "string", "text": "string"}),
        ("feishu_send", "Send a Feishu message; requires user enabled Full Access", {"text": "string", "receive_id": "string"}),
        ("zotero_collections", "读取已配置 Zotero Web API 的全部集合。", {}),
        ("zotero_local_state", "查本机 Zotero 写权限是否已授权，不修改任何数据。", {}),
        ("zotero_local_authorize", "申请本机 Zotero 写权限；用户在 Zotero 弹窗点「始终允许」才能复用，需 Full Access。", {}),
        ("zotero_local_collection_create", "在本机 Zotero 新建分类（＝项目文献夹），需 Full Access。", {"name": "string", "parent": "string"}),
        ("zotero_local_item_add", "把一条题录写进本机 Zotero 库，需 Full Access；字段口径同 Zotero item data。", {"item": "object"}),
        ("zotero_local_link_pdf", "把项目文件夹里的 PDF 挂到 Zotero 条目上（链接文件，文件不搬走），需 Full Access。", {"key": "string", "pdf": "string", "title": "string"}),
        ("zotero_write", "Write a Zotero item; requires Full Access and key/version for updates", {"item": "object"}),
        ("zotero_sync", "预览或执行项目与绑定Collection的元数据同步；默认只预览，执行需plan_token与确认。", {"task_id": "string", "mode": "string", "dry_run": "boolean", "plan_token": "string", "confirm": "boolean"}),
    ]
    return [{"type": "function", "function": {"name": name, "description": desc,
             "parameters": {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "additionalProperties": False}}}
            for name, desc, props in definitions]


def invoke(name: str, db: Any, task_id: str, args: dict, assistant_id: str = "", permission: str = "auto") -> dict:
    if name in {"zotero_search", "zotero_item", "zotero_import_pdf"}:
        if name == "zotero_search":
            return zotero.items(q=str(args.get("query") or ""), start=int(args.get("start") or 0), limit=int(args.get("limit") or 20))
        if name == "zotero_item":
            return zotero.item(str(args.get("key") or ""))
        if permission in {"read_only", "read-only"}:
            raise PermissionError("只读权限不能创建项目副本")
        import project_sources
        return project_sources.import_zotero_pdf(db, task_id, str(args.get("key") or ""))
    external_write = name in {"mail_send", "feishu_send", "zotero_write", "zotero_local_authorize",
                             "zotero_local_collection_create", "zotero_local_item_add",
                             "zotero_local_link_pdf"} or (name == "zotero_sync" and args.get("dry_run") is False)
    if external_write:
        if permission in {"read_only", "read-only"}:
            raise ValueError("当前助手为只读权限，禁止外发/写入")
        if permission not in {"full_access", "full-access"}:
            raise ValueError("此操作会外发或修改远端数据，请由用户在专家设置中开启 Full Access")
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
    if name == "zotero_local_state":
        return zotero.local_write_state(db)
    if name == "zotero_local_authorize":
        return zotero.authorize_local_write(db)
    if name == "zotero_local_collection_create":
        return zotero.create_collection(db, str(args.get("name") or ""), str(args.get("parent") or ""))
    if name == "zotero_local_item_add":
        return zotero.add_item(db, args.get("item") or {})
    if name == "zotero_local_link_pdf":
        from pathlib import Path
        root = Path(db.base_dir(task_id)).resolve()      # ponytail: 只挡路径越界，不查是否该条目的论文
        path = Path(str(args.get("pdf") or "")).expanduser().resolve()
        if not path.is_relative_to(root):
            raise PermissionError("只能挂当前项目文件夹里的 PDF：%s" % root)
        return zotero.link_pdf(db, str(args.get("key") or ""), str(path), str(args.get("title") or "PDF"))
    if name == "zotero_write":
        return zotero_sync.write_item(db, args.get("item") or {})
    if name == "zotero_sync":
        return zotero_sync.sync(db, str(args.get("task_id") or task_id), str(args.get("mode") or "merge"),
                                args.get("dry_run") is not False, str(args.get("plan_token") or ""))
    raise ValueError("未知连接器工具：" + name)
