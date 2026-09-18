"""Read public documents without datasets, using a bounded process-local snapshot cache."""

import hashlib
import io
import json
import re
import threading
import time
from collections import OrderedDict
from html.parser import HTMLParser

from .catalogs import guid
from .network import BASE, SourceError
from .storage import now


class DocumentHTML(HTMLParser):
    """Keep text order, paragraph breaks and source table cells without executing HTML."""

    BREAKS = {"p", "div", "li", "section", "article", "header", "caption", "pre", "blockquote"}
    HEADINGS = {f"h{i}" for i in range(1, 7)}
    IGNORED = {"script", "style", "head", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = io.StringIO()
        self.last = ""
        self.position = 0
        self.tables = []
        self.stack = []
        self.headings = []
        self.heading = None
        self.ignored = []
        self.warnings = set()

    def emit(self, text):
        self.output.write(text)
        self.position += len(text)
        if text:
            self.last = text[-1]
        for frame in self.stack:
            if frame["cell"] is not None:
                frame["cell"]["parts"].append(text)

    def newline(self):
        if self.position and self.last != "\n":
            self.emit("\n")

    def close_cell(self):
        if self.stack and self.stack[-1]["cell"] is not None:
            frame = self.stack[-1]
            cell = frame["cell"]
            cell["text"] = "".join(cell.pop("parts")).strip()
            cell["end_offset"] = self.position
            frame["cell"] = None

    def span(self, attrs, name):
        value = attrs.get(name, "1")
        try:
            parsed = int(value)
            if parsed >= (0 if name == "rowspan" else 1):
                return parsed
        except (TypeError, ValueError):
            pass
        self.warnings.add("Invalid table span in source HTML; represented as 1")
        return 1

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORED:
            self.ignored.append(tag)
            return
        if self.ignored:
            return
        attrs = dict(attrs)
        if tag in self.BREAKS or tag in self.HEADINGS or tag in {"br", "hr"}:
            self.newline()
        if tag in self.HEADINGS:
            self.heading = (tag, self.position)
        elif tag == "table":
            if len(self.stack) >= 32:
                raise SourceError("HTML_TOO_DEEP", "Document table nesting exceeds 32 levels")
            self.newline()
            table = {"table_index": len(self.tables), "offset": self.position, "rows": []}
            if self.stack:
                table["parent_table_index"] = self.stack[-1]["table"]["table_index"]
            self.tables.append(table)
            self.stack.append({"table": table, "cell": None})
        elif tag == "tr" and self.stack:
            self.close_cell()
            self.newline()
            self.stack[-1]["table"]["rows"].append([])
        elif tag in {"td", "th"} and self.stack:
            self.close_cell()
            frame = self.stack[-1]
            if not frame["table"]["rows"]:
                frame["table"]["rows"].append([])
            row = frame["table"]["rows"][-1]
            if row:
                self.emit("\t")
            cell = {
                "parts": [],
                "header": tag == "th",
                "offset": self.position,
                "rowspan": self.span(attrs, "rowspan"),
                "colspan": self.span(attrs, "colspan"),
            }
            row.append(cell)
            frame["cell"] = cell
        elif tag == "img":
            self.emit(attrs.get("alt") or "[Изображение]")
            self.warnings.add("Embedded images are not OCR-processed; consult the original for image content")
        elif tag in {"sup", "sub"}:
            self.emit("^{" if tag == "sup" else "_{")

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag in {"td", "th"}:
            self.close_cell()
        elif tag == "tr" and self.stack:
            self.close_cell()
            self.newline()
        elif tag == "table" and self.stack:
            self.close_cell()
            frame = self.stack.pop()
            frame["table"]["end_offset"] = self.position
            self.newline()
        elif tag in {"sup", "sub"}:
            self.emit("}")
        if tag in self.HEADINGS and self.heading:
            heading_tag, start = self.heading
            self.headings.append((start, self.position, int(heading_tag[1])))
            self.heading = None
        if tag in self.BREAKS or tag in self.HEADINGS:
            self.newline()

    def handle_data(self, data):
        if self.ignored:
            return
        text = re.sub(r"\s+", " ", data)
        if not self.last or self.last.isspace():
            text = text.lstrip(" ")
        self.emit(text)

    def result(self):
        if self.ignored:
            self.warnings.add("Source HTML contains an unclosed non-text element")
            self.ignored.clear()
        while self.stack:
            self.warnings.add("Source HTML contains an unclosed table")
            self.handle_endtag("table")
        text = self.output.getvalue()
        blocks = []
        outer = [t for t in self.tables if "parent_table_index" not in t]
        outer_index = 0
        heading_index = 0
        offset = 0
        for line in text.splitlines(keepends=True):
            start = offset + len(line) - len(line.lstrip())
            end = offset + len(line.rstrip())
            offset += len(line)
            while outer_index < len(outer) and outer[outer_index]["end_offset"] <= start:
                outer_index += 1
            in_table = outer_index < len(outer) and outer[outer_index]["offset"] <= start
            if start >= end or in_table:
                continue
            block = {
                "kind": "paragraph",
                "offset": start,
                "end_offset": end,
                "preview": text[start:end][:240],
            }
            while heading_index < len(self.headings) and self.headings[heading_index][1] <= start:
                heading_index += 1
            if heading_index < len(self.headings):
                h_start, h_end, level = self.headings[heading_index]
                if h_start <= start < h_end:
                    block.update(kind="heading", level=level)
            blocks.append(block)
        for table in self.tables:
            blocks.append(
                {
                    "kind": "table",
                    "offset": table["offset"],
                    "end_offset": table["end_offset"],
                    "table_index": table["table_index"],
                    "rows": len(table["rows"]),
                    "preview": text[table["offset"] : table["end_offset"]][:240],
                }
            )
        blocks.sort(key=lambda b: (b["offset"], b.get("table_index", -1)))
        return text, blocks, self.tables, sorted(self.warnings)


def document_path(source, document_guid):
    if source == "normative":
        return "FrsnDocument/DocDataByGuid/" + guid(document_guid), guid(document_guid)
    if source in {"fssc", "fsem"} and document_guid is None:
        return f"FsRegistryPublic/{'Fssc' if source == 'fssc' else 'Fsem'}/DocData/", None
    raise ValueError("Use source='normative' with document_guid, or source='fssc'/'fsem' without GUID")


def bounds(offset, limit, maximum):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError(f"offset must be nonnegative; limit must be 1..{maximum}")


class OnlineDocuments:
    def __init__(self, network, *, max_bytes=32 * 1024 * 1024, max_documents=8, ttl=900):
        self.network = network
        self.max_bytes, self.max_documents, self.ttl = max_bytes, max_documents, ttl
        self.cache = OrderedDict()
        self.size = 0
        self.lock = threading.RLock()

    def get(self, document_guid=None, source="normative", expected_sha256=None, refresh=False):
        path, document_guid = document_path(source, document_guid)
        if expected_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 from a prior document response")
        with self.lock:
            stamp = time.monotonic()
            for key in list(self.cache):
                if stamp - self.cache[key][0] >= self.ttl or (refresh and key == path):
                    _, size, _ = self.cache.pop(key)
                    self.size -= size
            cached = path in self.cache
            if cached:
                _, _, doc = self.cache[path]
                self.cache.move_to_end(path)
            else:
                payload, _, meta = self.network.get_value(path, large=True)
                if not isinstance(payload, dict):
                    raise SourceError("SCHEMA_CHANGED", "Document response must be an object")
                html = payload.get("fullPublishedText")
                if html is not None and not isinstance(html, str):
                    raise SourceError("SCHEMA_CHANGED", "Published document text must be a string")
                if not html and not (payload.get("name") or payload.get("filePath")):
                    raise SourceError("SCHEMA_CHANGED", "Response has neither document text nor metadata")
                parser = DocumentHTML()
                parser.feed(html or "")
                parser.close()
                text, blocks, tables, warnings = parser.result()
                doc = {
                    "source": source,
                    "document_guid": document_guid,
                    "name": payload.get("name", ""),
                    "text_status": "available" if text.strip() else "unavailable",
                    "text": text,
                    "blocks": blocks,
                    "tables": tables,
                    "warnings": warnings,
                    "file_reference": payload.get("filePath"),
                    "provenance": {**meta, "fetched_at": now()},
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
                if document_guid:
                    doc["original_file_url"] = BASE + "NormLegalDocFilePublished/GetByGuid/" + document_guid
                if doc["text_status"] == "unavailable":
                    doc["warnings"].append("No public text available; PDF extraction/OCR is not performed")
                size = len(json.dumps(doc, ensure_ascii=False).encode())
                while self.cache and (
                    self.size + size > self.max_bytes or len(self.cache) >= self.max_documents
                ):
                    _, (_, removed_size, _) = self.cache.popitem(last=False)
                    self.size -= removed_size
                if size <= self.max_bytes and self.max_documents > 0:
                    self.cache[path] = (time.monotonic(), size, doc)
                    self.size += size
            if expected_sha256 and expected_sha256 != doc["provenance"]["sha256"]:
                raise SourceError(
                    "SNAPSHOT_CHANGED", "Document changed; reopen without expected_sha256 and restart offsets"
                )
            info = {k: v for k, v in doc.items() if k not in {"text", "blocks", "tables"}}
            info.update(
                total_characters=len(doc["text"]),
                total_blocks=len(doc["blocks"]),
                total_tables=len(doc["tables"]),
                cache_hit=cached,
                cache_retained=path in self.cache,
                offset_unit="Unicode code points",
            )
            return doc, info

    def read(
        self,
        document_guid=None,
        source="normative",
        offset=0,
        limit=12000,
        expected_sha256=None,
        refresh=False,
    ):
        bounds(offset, limit, 50000)
        doc, info = self.get(document_guid, source, expected_sha256, refresh)
        return info | {
            "offset": offset,
            "text": doc["text"][offset : offset + limit],
            "next_offset": offset + limit if offset + limit < len(doc["text"]) else None,
        }

    def outline(self, document_guid=None, source="normative", offset=0, limit=30, expected_sha256=None):
        bounds(offset, limit, 100)
        doc, info = self.get(document_guid, source, expected_sha256)
        return info | {
            "block_offset": offset,
            "items": doc["blocks"][offset : offset + limit],
            "next_block_offset": offset + limit if offset + limit < len(doc["blocks"]) else None,
        }

    def search(
        self,
        query,
        document_guid=None,
        source="normative",
        offset=0,
        limit=10,
        context=200,
        expected_sha256=None,
    ):
        bounds(offset, limit, 30)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("query must contain 1..200 characters")
        if type(context) is not int or not 0 <= context <= 1000:
            raise ValueError("context must be 0..1000 characters")
        doc, info = self.get(document_guid, source, expected_sha256)
        text = doc["text"]
        pattern = re.compile(r"\s+".join(re.escape(part) for part in query.split()), re.IGNORECASE)
        matches, next_offset = [], None
        for match in pattern.finditer(text, offset):
            if len(matches) == limit:
                next_offset = match.start()
                break
            start, end = max(0, match.start() - context), min(len(text), match.end() + context)
            matches.append(
                {
                    "offset": match.start(),
                    "end_offset": match.end(),
                    "match": match[0],
                    "excerpt_offset": start,
                    "excerpt": text[start:end],
                }
            )
        return info | {
            "query": query,
            "search_offset": offset,
            "matches": matches,
            "next_offset": next_offset,
        }

    def table(
        self,
        table_index,
        document_guid=None,
        source="normative",
        row_offset=0,
        limit=20,
        expected_sha256=None,
        cell_offset=0,
        cell_limit=20,
    ):
        bounds(row_offset, limit, 100)
        bounds(cell_offset, cell_limit, 50)
        if type(table_index) is not int or table_index < 0:
            raise ValueError("table_index must be a nonnegative integer from the document outline")
        doc, info = self.get(document_guid, source, expected_sha256)
        if table_index >= len(doc["tables"]):
            raise ValueError("Table index is outside this document")
        table = doc["tables"][table_index]
        rows, budget = [], 24000
        for row_index, row in enumerate(table["rows"][row_offset : row_offset + limit], row_offset):
            cells = []
            for index, cell in enumerate(row[cell_offset : cell_offset + cell_limit], cell_offset):
                size = min(len(cell["text"]), 2000, budget)
                cells.append(
                    cell
                    | {
                        "cell_index": index,
                        "text": cell["text"][:size],
                        "text_length": len(cell["text"]),
                        "text_truncated": size < len(cell["text"]),
                    }
                )
                budget -= size
            rows.append(
                {
                    "row_index": row_index,
                    "cells": cells,
                    "total_cells": len(row),
                    "next_cell_offset": cell_offset + cell_limit
                    if cell_offset + cell_limit < len(row)
                    else None,
                }
            )
        return (
            info
            | {k: v for k, v in table.items() if k != "rows"}
            | {
                "row_offset": row_offset,
                "total_rows": len(table["rows"]),
                "rows": rows,
                "cell_offset": cell_offset,
                "next_row_offset": row_offset + limit if row_offset + limit < len(table["rows"]) else None,
                "context_before": doc["text"][max(0, table["offset"] - 500) : table["offset"]],
                "context_after": doc["text"][table["end_offset"] : table["end_offset"] + 500],
                "table_semantics": "Source cells with rowspan/colspan; no coefficient applicability or price inference",
            }
        )
