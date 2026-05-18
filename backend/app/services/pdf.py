import io
import re

from pypdf import PdfReader

_WS = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    raw = "\n\n".join(pages)
    # Strip excess whitespace: collapse runs of spaces/tabs, trim trailing
    # whitespace per line, collapse 3+ blank lines to 2.
    lines = [_WS.sub(" ", line).rstrip() for line in raw.splitlines()]
    cleaned = "\n".join(lines).strip()
    cleaned = _BLANK_LINES.sub("\n\n", cleaned)
    return cleaned
