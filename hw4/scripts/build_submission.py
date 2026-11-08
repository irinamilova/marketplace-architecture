import hashlib
import html
import re
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("requirements.md", "RFC-001.md")


def render_page(name: str) -> str:
    text = (ROOT / name).read_text(encoding="utf-8")

    def embed(match: re.Match) -> str:
        alt, relative = match.groups()
        svg = (ROOT / relative).read_text(encoding="utf-8")
        svg = re.sub(r"<\?xml.*?\?>", "", svg, flags=re.S)
        svg = re.sub(r"<!DOCTYPE[^>]*>", "", svg)
        svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
        prefix = Path(name).stem + "-" + Path(relative).stem + "-"
        ids = re.findall(r'\bid="([^"]+)"', svg)
        for original in ids:
            new = prefix + original
            svg = svg.replace(f'id="{original}"', f'id="{new}"')
            svg = svg.replace(f'url(#{original})', f'url(#{new})')
            svg = svg.replace(f'href="#{original}"', f'href="#{new}"')
        return f'\n<figure>{svg}<figcaption>{html.escape(alt)}</figcaption></figure>\n'

    text = re.sub(r"!\[([^\]]*)\]\((diagrams/[^)]+\.svg)\)", embed, text)
    rendered = MarkdownIt("commonmark", {"html": True}).enable("table").render(text)
    rendered = rendered.replace('href="RFC-001.md"', 'href="#rfc"')
    rendered = rendered.replace('href="requirements.md"', 'href="#requirements"')
    return rendered


CSS = """
:root { color-scheme: light; font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
color:#182332; background:#f0f3f7; font-size:16px; line-height:1.65; }
body { margin:0; } main { max-width:1180px; margin:32px auto; padding:40px 54px;
background:white; border-radius:12px; box-shadow:0 4px 24px #18233210; }
nav { padding:16px 20px; background:#eef4ff; border-left:4px solid #2865b5; }
nav a { margin-right:24px; } h1 { font-size:2rem; line-height:1.25; }
h2 { margin-top:2.4rem; border-bottom:1px solid #dce3ed; padding-bottom:8px; line-height:1.3; }
h3 { margin-top:1.7rem; line-height:1.4; } a { color:#205da6; overflow-wrap:anywhere; }
table { border-collapse:collapse; width:100%; margin:20px 0; font-size:.88rem; line-height:1.5; }
th,td { padding:10px 12px; border:1px solid #d8e0ea; vertical-align:top; overflow-wrap:anywhere; }
th { background:#eaf0f9; text-align:left; } tr:nth-child(even) td { background:#f8fafc; }
code { background:#f0f3f7; border-radius:4px; padding:1px 4px; font-size:.86em; }
figure { margin:24px 0; padding:12px; border:1px solid #dce3ed; border-radius:8px; overflow-x:auto; }
figure svg { display:block; height:auto; width:100%; min-width:1050px; margin:auto; }
figcaption { text-align:center; color:#526178; font-size:.85rem; margin-top:8px; }
.meta { color:#526178; } section + section { margin-top:70px; }
@media(max-width:760px) { main { padding:22px 16px; margin:0; border-radius:0; }
table { font-size:.78rem; } td,th { padding:6px; } }
@media print { :root { font-size:10pt; background:white; } main { margin:0; padding:0;
max-width:none; box-shadow:none; } nav { display:none; } section + section { break-before:page; }
h1,h2,h3 { break-after:avoid; } figure,tr { break-inside:avoid; }
figure { padding:4px; overflow:visible; } figure svg { min-width:0; max-width:100%; }
a { color:inherit; text-decoration:none; } }
"""


def main() -> None:
    sources = sorted(
        [ROOT / p for p in PAGES]
        + list((ROOT / "diagrams").glob("*.svg"))
        + list((ROOT / "diagrams").glob("*.puml"))
        + [Path(__file__).resolve()]
    )
    digest = hashlib.sha256()
    for path in sources:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    documents = "\n".join(
        f'<section id="{anchor}">{render_page(name)}</section>'
        for name, anchor in zip(PAGES, ("requirements", "rfc"))
    )
    output = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="source-sha256" content="{digest.hexdigest()}">
<title>Домашнее задание 4 — Notification Platform</title><style>{CSS}</style></head>
<body><main><p class="meta">Домашнее задание №4 · 7 ноября 2026 · Архитектурный проект</p>
<nav><a href="#requirements">Задания 1–7</a><a href="#rfc">RFC: задание 8</a></nav>
{documents}</main></body></html>"""
    (ROOT / "submission.html").write_text(output, encoding="utf-8")
    print("Built submission.html with 6 inline SVG diagrams")


if __name__ == "__main__":
    main()
