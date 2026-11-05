"""Turning real-world documents into knowledge sources: Word files to Markdown, and a BRD into its requirements.

A BRD is one file but carries many requirements (`BF-AS-VA-02: Accounts List ...`). Retrieval and traceability work on
sources, so such a document is also split into one requirement source per identified section, each linked back to the
document it came from. Nothing is invented: a section exists only where the document itself names an identifier.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ID = r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+"
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def plain(s: object) -> str:
    """Lower case, letters and digits only, one space between words: text compared regardless of Markdown and layout."""
    return _NON_ALNUM.sub(" ", str(s).lower()).strip()


_START = re.compile(rf"^({ID})\s*[:\-]?\s*(.*)$")  # "BF-AS-VA-02: Accounts List Information"
_PAREN = re.compile(rf"^(.*?)\(\s*({ID})\s*\)\s*$")  # "5.2.1 Accounts List Information (BF-AS-VA-02)"
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD = re.compile(r"^\*\*(.+?)\*\*\s*:?\s*$")
_PSF_LIST = re.compile(
    r"\bPSF\s+((?:C(?:\.\d+)+\.?(?:\s*(?:,|and)\s*)?)+)"
)  # "PSF C.1.5.1.6. and C.1.5.1.7."
_PSF_ONE = re.compile(r"C(?:\.\d+)+")
MAX_DOCX_BYTES = 20_000_000


@dataclass
class Section:
    id: str
    title: str
    text: str
    refines: str | None = (
        None  # set when the heading only mentions the id in brackets (a detail of that requirement)
    )
    references: tuple[str, ...] = ()


def _clean(s: str) -> str:
    s = re.sub(r"\{#[^}]*\}", "", s).replace("\\", "").replace("**", "").replace("​", "")
    return re.sub(r"\s+", " ", s).strip(" :\t")


_NOISE = re.compile(r"\s*[-–]\s*Meets the requirements of.*$", re.I)


def clean_line(s: str) -> str:
    """A line of an exported document as a plain title: no Markdown heading marks, bold, escapes or anchors."""
    return _clean(re.sub(r"^#+\s*", "", s.strip()))


_GENERIC_TITLE = re.compile(r"(?i)^(business requirements? document|brd|user stories|epics?)\b")


def first_title(text: str) -> str:
    """The document's own title: its first non-empty line, cleaned (exports often start with a bold line, not a heading).
    A generic first line ("BUSINESS REQUIREMENTS DOCUMENT") is followed by the next heading, so documents stay apart."""
    lines = [clean_line(ln) for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""
    if _GENERIC_TITLE.match(lines[0]) and len(lines) > 1:
        nxt = next((ln for ln in lines[1:4] if ln and not ln.startswith(("-", "|"))), "")
        if nxt:
            return f"{lines[0].title()}: {nxt}"[:200]
    return lines[0][:200]


def split_sections(text: str, skip_prefixes: tuple[str, ...] = ()) -> list[Section]:
    """Sections that start at a heading (or a bold line) beginning with an identifier, or ending with one in
    brackets. A section runs until the next heading or identified bold line."""
    lines = text.split("\n")
    marks: list[tuple[int, str, str, str | None]] = []  # line, id, title, refines
    boundaries: set[int] = set()
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        b = _BOLD.match(line.strip())
        title = _clean(m.group(2) if m else (b.group(1) if b else ""))
        if m:
            boundaries.add(i)
        if not title:
            continue
        s = _START.match(title)
        p = _PAREN.match(title)
        if s and (m or b):
            marks.append((i, s.group(1), _NOISE.sub("", s.group(2) or s.group(1)), None))
            boundaries.add(i)
        elif p and (m or b):
            marks.append(
                (
                    i,
                    f"{p.group(2)}:{re.sub(r'[^a-z0-9]+', '-', p.group(1).lower()).strip('-')}",
                    p.group(1).strip(),
                    p.group(2),
                )
            )
    out: list[Section] = []
    seen: set[str] = set()
    for start, sid, title, refines in marks:
        end = next((j for j in sorted(boundaries) if j > start), len(lines))
        body = "\n".join(lines[start:end]).strip()
        if sid in seen or len(body) < 40 or sid.startswith(skip_prefixes):
            continue
        seen.add(sid)
        refs = tuple(dict.fromkeys(r for lst in _PSF_LIST.findall(body) for r in _PSF_ONE.findall(lst)))
        out.append(Section(sid, title, body, refines, refs))
    return out


def docx_to_markdown(path) -> str:
    """Text, headings, lists and tables of a .docx as Markdown, with the standard library only. Images, comments and
    tracked-change history are ignored. Refuses oversized or malformed files."""
    p = path if hasattr(path, "stat") else None
    if p is not None and p.stat().st_size > MAX_DOCX_BYTES:
        raise ValueError("The Word file is larger than 20 MB")
    try:
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 10 * MAX_DOCX_BYTES:
                raise ValueError("The Word file expands to an unreasonable size")
            root = ET.fromstring(z.read("word/document.xml"))  # noqa: S314 - local file, size-checked, no external entities in ElementTree
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ValueError(f"Not a readable .docx file ({type(e).__name__})") from None
    body = root.find(f"{W}body")
    out: list[str] = []

    def runs(par) -> str:
        t = []
        for r in par.iter():
            if r.tag == f"{W}t":
                t.append(r.text or "")
            elif r.tag in (f"{W}tab",):
                t.append(" ")
            elif r.tag in (f"{W}br", f"{W}cr"):
                t.append(" ")
        return "".join(t).strip()

    def style(par) -> str:
        s = par.find(f"{W}pPr/{W}pStyle")
        return (s.get(f"{W}val") or "") if s is not None else ""

    def table(tbl) -> list[str]:
        rows = []
        for tr in tbl.findall(f"{W}tr"):
            rows.append(
                [
                    " ".join(runs(p) for p in tc.findall(f"{W}p")).replace("|", "/").strip()
                    for tc in tr.findall(f"{W}tc")
                ]
            )
        if not rows:
            return []
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return lines

    for el in list(body) if body is not None else []:
        if el.tag == f"{W}p":
            text = runs(el)
            if not text:
                continue
            st = style(el)
            m = re.match(r"(?i)heading\s*(\d)", st)
            if m:
                out.append("#" * min(int(m.group(1)), 6) + " " + text)
            elif st.lower() == "title":
                out.append("# " + text)
            elif el.find(f"{W}pPr/{W}numPr") is not None or st.lower().startswith("list"):
                out.append("- " + text)
            else:
                out.append(text)
            out.append("")
        elif el.tag == f"{W}tbl":
            out += table(el) + [""]
    return "\n".join(out)


# ---- backlog files: a user_story.md holds many stories, an epic.md many epics with their features ---------------
_STORY_HEAD = re.compile(rf"^##\s+({ID}):\s*(.+?)\s*$")
_EPIC_HEAD = re.compile(rf"^###\s+({ID}):\s*(.+?)\s*$")
_STORY_META = re.compile(r"^-\s*Epic:\s*(\S+)\s*\|\s*Feature:\s*(\S+)(?:\s*\|\s*Traces:\s*(.+?))?\s*$")
_PRIORITY = re.compile(r"^-\s*Priority:\s*([^|]+?)\s*(?:\|\s*Channel:\s*(.+?))?\s*$")
_FEATURE = re.compile(rf"^-\s*\*\*({ID})\s+(.+?)\*\*")
BACKLOG_PREFIXES = ("E-", "FEAT-", "US-", "ITEM-")  # backlog identifiers, not requirement identifiers


@dataclass
class BacklogItem:
    id: str
    title: str
    text: str
    epic: str | None = None
    feature: str | None = None
    traces: tuple[str, ...] = ()
    priority: str | None = None
    channel: str | None = None
    features: tuple[tuple[str, str], ...] = ()  # (id, title) of an epic's features


def _blocks(
    lines: list[str], head: re.Pattern[str], stop: re.Pattern[str]
) -> list[tuple[re.Match[str], list[str]]]:
    out: list[tuple[re.Match[str], list[str]]] = []
    cur: tuple[re.Match[str], list[str]] | None = None
    for line in lines:
        m = head.match(line)
        if m:
            cur = (m, [line])
            out.append(cur)
        elif cur is not None and stop.match(line):
            cur = None
        elif cur is not None:
            cur[1].append(line)
    return out


def leftover(text: str, head: re.Pattern[str], stop: re.Pattern[str]) -> str:
    """The lines of a backlog file that no item block took (file title, source line, an assumptions register, notes after
    the last item): kept as a note so nothing the file says is lost."""
    out: list[str] = []
    inside = False
    for line in text.split(chr(10)):
        if head.match(line):
            inside = True
        elif inside and stop.match(line):
            inside = False
            out.append(line)
        elif not inside:
            out.append(line)
    return chr(10).join(out).strip()


def story_notes(text: str) -> str:
    return leftover(text, _STORY_HEAD, re.compile(r"^##\s"))


def epic_notes(text: str) -> str:
    return leftover(text, _EPIC_HEAD, re.compile(r"^#{1,3}\s"))


def split_stories(text: str) -> list[BacklogItem]:
    """`## US-001: title` blocks (until the next `##` heading) with the epic, feature, traces and priority they declare."""
    items = []
    for m, body in _blocks(text.split("\n"), _STORY_HEAD, re.compile(r"^##\s")):
        it = BacklogItem(m.group(1), _clean(m.group(2)), "\n".join(body).strip())
        for line in body[:8]:
            if mm := _STORY_META.match(line.strip()):
                it.epic, it.feature = _clean(mm.group(1)), _clean(mm.group(2))
                it.traces = tuple(x.strip() for x in (mm.group(3) or "").split(",") if x.strip())
            elif mm := _PRIORITY.match(line.strip()):
                it.priority, it.channel = mm.group(1), mm.group(2)
        items.append(it)
    return items


def split_epics(text: str) -> list[BacklogItem]:
    """`### E-001: title` blocks with the `- **FEAT-001 title** (n stories)` lines under them."""
    items = []
    for m, body in _blocks(text.split("\n"), _EPIC_HEAD, re.compile(r"^#{1,3}\s")):
        feats = tuple(
            (f.group(1), _clean(f.group(2))) for line in body if (f := _FEATURE.match(line.strip()))
        )
        items.append(BacklogItem(m.group(1), _clean(m.group(2)), "\n".join(body).strip(), features=feats))
    return items
