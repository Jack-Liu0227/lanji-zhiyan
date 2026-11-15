"""Read toolbox inputs locally; only PDF translation imports a project asset."""
import base64
import io
from pathlib import Path
import zipfile

TEXT = {'.txt', '.md', '.markdown', '.tex'}
FORMATS = {
    'translate': TEXT | {'.docx', '.pdf'}, 'polish': TEXT | {'.docx', '.pdf'},
    'convert': TEXT | {'.docx', '.pdf'}, 'references': {'.bib', '.bibtex', '.ris', '.txt'},
    'mermaid': {'.mmd', '.mermaid', '.md', '.txt'}, 'pdf_translate': {'.pdf'},
}
MAX_BYTES = 20 * 1024 * 1024
MAX_CHARS = 200000


def import_file(db, body):
    tool = str(body.get('tool') or '')
    name = str(body.get('name') or '').replace('\\', '/').rsplit('/', 1)[-1]
    suffix = Path(name).suffix.lower()
    if tool not in FORMATS or suffix not in FORMATS[tool]:
        raise ValueError('此文件类型不适用于当前工具')
    encoded = str(body.get('data') or '').split(',', 1)[-1]
    if len(encoded) > (MAX_BYTES + 2) // 3 * 4:
        raise ValueError('每个文件最多 20 MB')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError('文件内容编码无效') from None
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('请选择非空文件，每个文件最多 20 MB')
    if suffix == '.pdf':
        import pypdfium2
        try:
            with pypdfium2.PdfDocument(raw) as document:
                if not 1 <= len(document) <= 500:
                    raise ValueError('每个 PDF 支持 1 至 500 页')
                parts = []
                count = 0
                if tool != 'pdf_translate':
                    for page in document:
                        try:
                            textpage = page.get_textpage()
                            try:
                                part = textpage.get_text_range()
                            finally:
                                textpage.close()
                        finally:
                            page.close()
                        parts.append(part); count += len(part)
                        if count > MAX_CHARS:
                            raise ValueError('文本超过 200000 字符，请拆分文件后导入')
                text = '\n\n'.join(parts)
        except ValueError:
            raise
        except Exception:
            raise ValueError('PDF 无法读取，请检查文件是否损坏或已加密') from None
        if tool == 'pdf_translate':
            import project_files
            import asset_service
            pid = str(body.get('project_id') or '')
            if not pid:
                raise ValueError('请先选择结果归属项目，再导入 PDF')
            saved = project_files.upload(db, pid, name, raw)
            asset = asset_service.get(db, saved['asset_id'])
            paper = db.ingest_pdf(pid, asset['canonical_uri'])
            return {'name': name, 'paper_id': paper['id'], 'pages': len(paper['pages'])}
        if not text.strip():
            raise ValueError('PDF 没有可提取的文字，请先 OCR，或使用 PDF 翻译工具')
    elif suffix == '.docx':
        from docx import Document
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > 2000 or sum(info.file_size for info in archive.infolist()) > 40 * 1024 * 1024:
                    raise ValueError('Word 解压后过大，请拆分文件')
            document = Document(io.BytesIO(raw))
            # Preserve body order, including tables between paragraphs.
            from docx.text.paragraph import Paragraph
            from docx.table import Table
            parts = []
            for element in document.element.body:
                if element.tag.endswith('}p'):
                    parts.append(Paragraph(element, document).text)
                elif element.tag.endswith('}tbl'):
                    parts.extend('\t'.join(cell.text for cell in row.cells) for row in Table(element, document).rows)
            text = '\n\n'.join(parts)
        except ValueError:
            raise
        except Exception:
            raise ValueError('Word 文件无法读取，请使用有效的 .docx 文件') from None
    else:
        text = None
        encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeError:
                pass
        if text is None or any(ord(char) < 32 and char not in '\t\r\n' for char in text):
            raise ValueError('文件不是可读取的文本，请保存为 UTF-8 后重试')
    if not text.strip():
        raise ValueError('文件中没有可读取的文字')
    if len(text) > MAX_CHARS:
        raise ValueError('文本超过 200000 字符，请拆分文件后导入')
    return {'name': name, 'text': text, 'characters': len(text)}
