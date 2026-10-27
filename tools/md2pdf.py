#!/usr/bin/env python3
"""Markdown -> PDF renderer for the dossier documents in this repo.

Usage:
    python tools/md2pdf.py IN.md OUT.pdf "Title" "Subtitle"

Requires: reportlab  (pip install reportlab)
Fonts: DejaVu Sans / DejaVu Sans Mono from the system font path.

Supports: ATX headings (h1 starts a new page), paragraphs, bullet lists with
one nesting level, GFM tables (with `code spans` and escaped pipes), fenced
code blocks, blockquotes and horizontal rules.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Preformatted, Spacer, Table, TableStyle,
)

FDIR = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DJ", f"{FDIR}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DJ-B", f"{FDIR}/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DJ-I", f"{FDIR}/DejaVuSerif.ttf"))  # no oblique shipped
pdfmetrics.registerFont(TTFont("DJM", f"{FDIR}/DejaVuSansMono.ttf"))
pdfmetrics.registerFont(TTFont("DJM-B", f"{FDIR}/DejaVuSansMono-Bold.ttf"))
pdfmetrics.registerFontFamily("DJ", normal="DJ", bold="DJ-B",
                              italic="DJ-I", boldItalic="DJ-B")

INK = colors.HexColor("#14181f")
MUTED = colors.HexColor("#5b6472")
ACCENT = colors.HexColor("#1a4d8f")
CODEBG = colors.HexColor("#f4f5f7")
RULE = colors.HexColor("#c9d1db")
ZEBRA = colors.HexColor("#f7f9fc")

# Glyphs DejaVu cannot render: map or drop.
EMOJI = {
    "\u2705": "[OK]", "\u274c": "[X]", "\u2714": "[OK]", "\u2713": "[OK]",
    "\u2717": "[X]", "\u2718": "[X]", "\u26a0": "[!]", "\u2757": "!",
    "\u2b50": "\u2605", "\u1f31f": "\u2605", "\u1f3af": "\u25b8",
    "\u1f4ca": "", "\u1f4c8": "", "\u1f4c9": "", "\u1f4c4": "",
    "\u1f4cb": "", "\u1f4c1": "", "\u1f5c2": "", "\u1f5c3": "",
    "\u1f50d": "", "\u1f504": "", "\u23ed": "", "\u1f9e0": "",
    "\u1f48a": "", "\u1fa7a": "", "\u1f3e5": "", "\u1f986": "",
    "\u1f916": "", "\u1f465": "", "\u2696": "", "\u1f7e3": "",
    "\u2192": "->", "\u2b05": "<-", "\u2b06": "^", "\u2b07": "v",
    "\ufe0f": "", "\u200b": "", "\u200d": "",
}


def clean(text: str) -> str:
    out = []
    for ch in text:
        if ch in EMOJI:
            out.append(EMOJI[ch])
        elif ord(ch) >= 0x1F000 or ord(ch) in (0xFE0F, 0x200B, 0x200D):
            continue
        else:
            out.append(ch)
    return "".join(out)


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CODE_RE = re.compile(r"`([^`]+)`")


def inline(t: str) -> str:
    """Markdown inline markup -> ReportLab paragraph markup."""
    t = clean(t)
    stash = []

    def _code(m):
        stash.append('<font face="DJM" size="8.4" color="#b02a5b">%s</font>'
                     % esc(m.group(1)))
        return "\x00%d\x00" % (len(stash) - 1)

    t = CODE_RE.sub(_code, t)
    t = esc(t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<font color="#1a4d8f">\1</font>', t)
    t = re.sub(r"\*\*\*([^*]+)\*\*\*", r"<b><i>\1</i></b>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"(?<![A-Za-z0-9])__([^_]+)__(?![A-Za-z0-9])", r"<b>\1</b>", t)
    t = re.sub("\x00(\\d+)\x00", lambda m: stash[int(m.group(1))], t)
    return t


def split_row(row):
    """Split a GFM row honouring `code spans` and escaped pipes."""
    row = row.strip().strip("|")
    cells, cur, in_code, i = [], [], False, 0
    while i < len(row):
        ch = row[i]
        if ch == "`":
            in_code = not in_code
            cur.append(ch)
        elif ch == "\\" and i + 1 < len(row) and row[i + 1] == "|":
            cur.append("|")
            i += 1
        elif ch == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def parse_blocks(lines):
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].rstrip()

        if s.startswith("```"):
            lang = s[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].rstrip().startswith("```"):
                buf.append(lines[i].rstrip("\n"))
                i += 1
            i += 1
            yield ("code", (lang, "\n".join(buf)))
            continue

        if (re.match(r"^\s*\|.*\|\s*$", s) and i + 1 < n
                and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1])):
            rows, i2 = [], i
            while i2 < n and re.match(r"^\s*\|.*\|\s*$", lines[i2]):
                rows.append(lines[i2].strip())
                i2 += 1
            yield ("table", rows)
            i = i2
            continue

        if re.match(r"^\s*(---+|\*\*\*+|___+)\s*$", s):
            yield ("hr", None)
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            yield ("heading", (len(m.group(1)), m.group(2).strip()))
            i += 1
            continue

        if s.startswith(">"):
            buf = []
            while i < n and lines[i].rstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]).rstrip())
                i += 1
            yield ("quote", "\n".join(buf))
            continue

        if re.match(r"^\s*([-*+]|\d+[.)])\s+", s):
            items, i2 = [], i
            while i2 < n:
                cur = lines[i2]
                if re.match(r"^\s{2,}([-*+]|\d+[.)])\s+", cur) and items:
                    items.append((1, re.sub(r"^\s*([-*+]|\d+[.)])\s+", "",
                                            cur).rstrip()))
                elif re.match(r"^\s*([-*+]|\d+[.)])\s+", cur):
                    items.append((0, re.sub(r"^\s*([-*+]|\d+[.)])\s+", "",
                                            cur).rstrip()))
                elif cur.strip() == "":
                    if i2 + 1 < n and re.match(
                            r"^\s*([-*+]|\d+[.)]|\s{2,}[-*+])\s+", lines[i2 + 1]):
                        i2 += 1
                        continue
                    break
                elif items and re.match(r"^\s{2,}\S", cur):
                    items[-1] = (items[-1][0], items[-1][1] + " " + cur.strip())
                else:
                    break
                i2 += 1
            yield ("list", items)
            i = i2
            continue

        if s.strip() == "":
            i += 1
            continue

        buf = []
        while i < n:
            cur = lines[i].rstrip()
            if (cur.strip() == "" or cur.startswith("```") or cur.startswith(">")
                    or re.match(r"^#{1,6}\s", cur)
                    or re.match(r"^\s*([-*+]|\d+[.)])\s+", cur)
                    or re.match(r"^\s*(---+|\*\*\*+|___+)\s*$", cur)
                    or re.match(r"^\s*\|.*\|\s*$", cur)):
                break
            buf.append(cur)
            i += 1
        if buf:
            yield ("para", "\n".join(buf))
        else:
            i += 1


def make_styles():
    base = ParagraphStyle("base", fontName="DJ", fontSize=9.3, leading=13.4,
                          textColor=INK, alignment=TA_LEFT, spaceAfter=6)
    st = {"body": base}
    st["h1"] = ParagraphStyle("h1", parent=base, fontName="DJ-B", fontSize=18,
                              leading=23, textColor=ACCENT,
                              spaceBefore=4, spaceAfter=10)
    st["h2"] = ParagraphStyle("h2", parent=base, fontName="DJ-B", fontSize=13.5,
                              leading=18, textColor=ACCENT,
                              spaceBefore=15, spaceAfter=6)
    st["h3"] = ParagraphStyle("h3", parent=base, fontName="DJ-B", fontSize=11.2,
                              leading=15, textColor=INK,
                              spaceBefore=11, spaceAfter=5)
    st["h4"] = ParagraphStyle("h4", parent=base, fontName="DJ-B", fontSize=10.0,
                              leading=13.5, textColor=INK,
                              spaceBefore=9, spaceAfter=4)
    st["h5"] = ParagraphStyle("h5", parent=base, fontName="DJ-B", fontSize=9.4,
                              leading=13, textColor=MUTED,
                              spaceBefore=7, spaceAfter=3)
    st["h6"] = st["h5"]
    st["quote"] = ParagraphStyle("quote", parent=base, leftIndent=14,
                                 rightIndent=8, fontName="DJ-I", textColor=MUTED,
                                 fontSize=9.1, leading=13.6,
                                 spaceBefore=4, spaceAfter=8)
    st["cell"] = ParagraphStyle("cell", parent=base, fontSize=7.8,
                                leading=10.2, spaceAfter=0)
    st["cellb"] = ParagraphStyle("cellb", parent=st["cell"], fontName="DJ-B",
                                 textColor=colors.white)
    st["title"] = ParagraphStyle("title", parent=base, fontName="DJ-B",
                                 fontSize=23, leading=29, textColor=ACCENT,
                                 spaceAfter=6)
    st["subtitle"] = ParagraphStyle("sub", parent=base, fontSize=10.8,
                                    leading=15, textColor=MUTED, spaceAfter=4)
    st["code"] = ParagraphStyle("code", fontName="DJM", fontSize=7.2,
                                leading=9.4, textColor=INK)
    return st


ST = make_styles()


def wrap_code(text, width=98):
    out = []
    for line in text.split("\n"):
        line = line.replace("\t", "    ")
        while len(line) > width:
            cut = line.rfind(" ", 0, width)
            cut = cut if cut > 30 else width
            out.append(line[:cut])
            line = "    " + line[cut:].lstrip()
        out.append(line)
    return "\n".join(out)


def _cell_styles(size):
    cell = ParagraphStyle("c%d" % int(size * 10), fontName="DJ", fontSize=size,
                          leading=size * 1.30, textColor=INK, spaceAfter=0)
    head = ParagraphStyle("h%d" % int(size * 10), fontName="DJ-B", fontSize=size,
                          leading=size * 1.30, textColor=colors.white,
                          spaceAfter=0)
    return cell, head


def build_table(rows, avail):
    header = split_row(rows[0])
    body = [split_row(r) for r in rows[1:] if not re.match(r"^[\s:\-|]+$", r)]
    ncol = len(header)
    pad = 8.0

    # largest font size at which the longest word of every column still fits
    size, floors = 7.8, []
    for candidate in (7.8, 7.4, 7.0, 6.6, 6.2, 5.8):
        floors = []
        for c in range(ncol):
            cells_c = [r[c] for r in body if c < len(r)]
            cands = [pdfmetrics.stringWidth(w, "DJ", candidate)
                     for cell in cells_c for w in clean(cell).split()]
            cands += [pdfmetrics.stringWidth(w, "DJ-B", candidate)
                      for w in clean(header[c]).split()]
            floors.append(max(cands, default=candidate * 3) + pad)
        if sum(floors) <= avail:
            size = candidate
            break
    else:
        floors = [f * avail / sum(floors) for f in floors]
        size = 5.8

    cell_st, head_st = _cell_styles(size)
    data = [[Paragraph(inline(h), head_st) for h in header]]
    for r in body:
        while len(r) < ncol:
            r.append("")
        data.append([Paragraph(inline(c) if c else "", cell_st) for c in r[:ncol]])

    weights = []
    for c in range(ncol):
        longest = max([len(header[c])] + [len(r[c]) for r in body if c < len(r)]
                      or [8])
        weights.append(max(longest, 6))
    total = sum(weights)
    widths = [avail * w / total for w in weights]

    shortfall = sum(max(0.0, f - w) for f, w in zip(floors, widths))
    if shortfall > 0:
        widths = [max(w, f) for w, f in zip(widths, floors)]
        excess = sum(max(0.0, w - f) for w, f in zip(widths, floors))
        if excess > 0:
            widths = [w - shortfall * max(0.0, w - f) / excess if w > f else w
                      for w, f in zip(widths, floors)]
    scale = avail / sum(widths)
    widths = [w * scale for w in widths]

    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def build_list(items, avail):
    flow = [Spacer(1, 2)]
    bullet_style = ParagraphStyle("bul", parent=ST["body"], fontName="DJ",
                                  fontSize=9.3, leading=13.4,
                                  spaceAfter=0, textColor=ACCENT)
    body = ParagraphStyle("li", parent=ST["body"], leftIndent=0, spaceAfter=3)
    for level, text in items:
        indent = 0.45 * cm + level * 0.75 * cm
        marker = "\u2022" if level == 0 else "\u2013"
        row = Table([[Paragraph(marker, bullet_style),
                      Paragraph(inline(text).replace("\n", "<br/>"), body)]],
                    colWidths=[indent, avail - indent], hAlign="LEFT")
        row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        flow.append(row)
    flow.append(Spacer(1, 5))
    return flow


def render(md, out_path, title, subtitle):
    blocks = list(parse_blocks(md.split("\n")))
    avail = A4[0] - 3.4 * cm
    story = [Spacer(1, 2.6 * cm),
             Paragraph(clean(esc(title)).replace("\n", "<br/>"), ST["title"]),
             Spacer(1, 0.35 * cm),
             Paragraph(inline(subtitle), ST["subtitle"]),
             Spacer(1, 0.6 * cm)]
    hr = Table([[""]], colWidths=[avail], rowHeights=[2])
    hr.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    story += [hr, Spacer(1, 0.6 * cm)]

    first = True
    for kind, payload in blocks:
        if kind == "heading":
            lvl, text = payload
            if lvl == 1 and first:
                first = False
                story.append(PageBreak())
            story.append(Paragraph(inline(text), ST["h%d" % min(lvl, 6)]))
        elif kind == "para":
            story.append(Paragraph(inline(payload).replace("\n", "<br/>"),
                                   ST["body"]))
        elif kind == "quote":
            story.append(Paragraph(inline(payload).replace("\n", "<br/>"),
                                   ST["quote"]))
        elif kind == "hr":
            story.append(Spacer(1, 3))
            t = Table([[""]], colWidths=[avail], rowHeights=[1])
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RULE)]))
            story += [t, Spacer(1, 7)]
        elif kind == "code":
            box = Table([[Preformatted(wrap_code(clean(payload[1])),
                                       ST["code"])]],
                        colWidths=[avail], hAlign="LEFT")
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), CODEBG),
                ("BOX", (0, 0), (-1, -1), 0.4, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story += [Spacer(1, 3), KeepTogether([box]), Spacer(1, 8)]
        elif kind == "table":
            story += [Spacer(1, 3), build_table(payload, avail), Spacer(1, 9)]
        elif kind == "list":
            story.extend(build_list(payload, avail))

    def furniture(canvas, doc):
        canvas.saveState()
        canvas.setFont("DJ", 7.4)
        canvas.setFillColor(MUTED)
        canvas.drawString(1.7 * cm, A4[1] - 1.15 * cm, clean(title)[:90])
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(1.7 * cm, A4[1] - 1.35 * cm, A4[0] - 1.7 * cm,
                    A4[1] - 1.35 * cm)
        canvas.line(1.7 * cm, 1.35 * cm, A4[0] - 1.7 * cm, 1.35 * cm)
        canvas.drawRightString(A4[0] - 1.7 * cm, 1.0 * cm,
                               "Pagina %d" % doc.page)
        canvas.drawString(1.7 * cm, 1.0 * cm, clean(subtitle)[:85])
        canvas.restoreState()

    doc = BaseDocTemplate(str(out_path), pagesize=A4,
                          leftMargin=1.7 * cm, rightMargin=1.7 * cm,
                          topMargin=1.75 * cm, bottomMargin=1.75 * cm,
                          title=clean(title), author="RWE / OMOP CDM")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="f")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=furniture)])
    doc.build(story)


if __name__ == "__main__":
    src, out, title, subtitle = sys.argv[1:5]
    render(Path(src).read_text(encoding="utf-8"), Path(out), title, subtitle)
    print("OK ->", out, "(%d KB)" % (Path(out).stat().st_size // 1024))
