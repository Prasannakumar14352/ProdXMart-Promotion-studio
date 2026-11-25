"""Zero-dependency OOXML Excel (.xlsx) reader and writer built on Python stdlib."""

from __future__ import annotations

from io import BytesIO
import re
from typing import Any
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

_MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _col_letter(idx: int) -> str:
    """Convert 1-based column index (1 -> A, 27 -> AA) to Excel column letters."""
    out = []
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out.append(chr(65 + rem))
    return ''.join(reversed(out))


def _col_index(cell_ref: str) -> int:
    match = re.match(r'^([A-Z]+)', cell_ref.upper())
    if not match:
        return 0
    col = 0
    for ch in match.group(1):
        col = col * 26 + (ord(ch) - 64)
    return col - 1


def _sheet_name(raw: str) -> str:
    cleaned = re.sub(r'[:\\/?*\[\]]', ' ', str(raw or 'Sheet')).strip()
    return (cleaned or 'Sheet')[:31]


def write_xlsx_sheets(sheets: list[tuple[str, list[list[Any]]]]) -> bytes:
    """Serialize one or more (sheet_name, rows) tables to valid OOXML .xlsx bytes."""
    if not sheets:
        sheets = [('Sheet1', [[]])]

    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        content_overrides = [
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        ]
        wb_sheets = []
        wb_rels = [
            '<Relationship Id="rIdStyle" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        ]

        for idx, (raw_name, rows) in enumerate(sheets, start=1):
            sname = escape(_sheet_name(raw_name))
            content_overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
            wb_sheets.append(f'<sheet name="{sname}" sheetId="{idx}" r:id="rId{idx}"/>')
            wb_rels.append(
                f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
            )

            row_xml = []
            for r_i, row in enumerate(rows, start=1):
                cells_xml = []
                style_attr = ' s="1"' if r_i == 1 else ''
                for c_i, val in enumerate(row, start=1):
                    ref = f'{_col_letter(c_i)}{r_i}'
                    if val is None or val == '':
                        continue
                    if isinstance(val, bool):
                        cells_xml.append(f'<c r="{ref}" t="b"{style_attr}><v>{1 if val else 0}</v></c>')
                    elif isinstance(val, (int, float)):
                        cells_xml.append(f'<c r="{ref}"{style_attr}><v>{val}</v></c>')
                    else:
                        txt = escape(str(val))
                        cells_xml.append(f'<c r="{ref}" t="inlineStr"{style_attr}><is><t xml:space="preserve">{txt}</t></is></c>')
                row_xml.append(f'<row r="{r_i}">{"".join(cells_xml)}</row>')

            sheet_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="{_MAIN_NS}"><sheetData>{"".join(row_xml)}</sheetData></worksheet>'
            )
            zf.writestr(f'xl/worksheets/sheet{idx}.xml', sheet_xml)

        zf.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            + ''.join(content_overrides)
            + '</Types>',
        )
        zf.writestr(
            '_rels/.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        zf.writestr(
            'xl/workbook.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}"><sheets>{"".join(wb_sheets)}</sheets></workbook>',
        )
        zf.writestr(
            'xl/_rels/workbook.xml.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join(wb_rels)
            + '</Relationships>',
        )
        zf.writestr(
            'xl/styles.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{_MAIN_NS}">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '</styleSheet>',
        )
    return buf.getvalue()


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    ctype = cell.attrib.get('t', '')
    if ctype == 'inlineStr':
        t_el = cell.find(f'.//{{{_MAIN_NS}}}t')
        return t_el.text or '' if t_el is not None else ''
    v_el = cell.find(f'{{{_MAIN_NS}}}v')
    raw = (v_el.text or '') if v_el is not None else ''
    if ctype == 's':
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if ctype == 'b':
        return 'TRUE' if raw == '1' else 'FALSE'
    return raw


def read_xlsx_sheets(raw_bytes: bytes) -> dict[str, list[dict[str, str]]]:
    """Read sheets from OOXML .xlsx bytes into {sheet_name: [{header: val, ...}]}."""
    if not raw_bytes:
        raise ValueError('Uploaded Excel file is empty.')
    try:
        zf = zipfile.ZipFile(BytesIO(raw_bytes), 'r')
    except zipfile.BadZipFile:
        raise ValueError('Invalid .xlsx Excel file format.') from None

    with zf:
        shared: list[str] = []
        if 'xl/sharedStrings.xml' in zf.namelist():
            root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in root.findall(f'{{{_MAIN_NS}}}si'):
                parts = [t.text or '' for t in si.findall(f'.//{{{_MAIN_NS}}}t')]
                shared.append(''.join(parts))

        wb_root = ET.fromstring(zf.read('xl/workbook.xml'))
        rel_root = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
        rel_map = {
            rel.attrib['Id']: rel.attrib['Target'].lstrip('/')
            for rel in rel_root
        }

        out: dict[str, list[dict[str, str]]] = {}
        for sheet_el in wb_root.findall(f'.//{{{_MAIN_NS}}}sheet'):
            name = sheet_el.attrib.get('name', 'Sheet')
            rid = sheet_el.attrib.get(f'{{{_REL_NS}}}id', '')
            target = rel_map.get(rid, '')
            if target and not target.startswith('xl/'):
                target = 'xl/' + target
            if target not in zf.namelist():
                continue

            s_root = ET.fromstring(zf.read(target))
            matrix: list[list[str]] = []
            for row_el in s_root.findall(f'.//{{{_MAIN_NS}}}row'):
                row_map: dict[int, str] = {}
                max_col = -1
                for c_el in row_el.findall(f'{{{_MAIN_NS}}}c'):
                    ref = c_el.attrib.get('r', '')
                    idx = _col_index(ref) if ref else (max_col + 1)
                    max_col = max(max_col, idx)
                    row_map[idx] = _cell_value(c_el, shared)
                if max_col >= 0:
                    matrix.append([row_map.get(i, '') for i in range(max_col + 1)])

            if not matrix:
                out[name] = []
                continue
            headers = [str(h).strip() for h in matrix[0]]
            dict_rows: list[dict[str, str]] = []
            for r in matrix[1:]:
                if not any(str(v).strip() for v in r):
                    continue
                item = {headers[i]: (r[i] if i < len(r) else '') for i in range(len(headers)) if headers[i]}
                dict_rows.append(item)
            out[name] = dict_rows
        return out
