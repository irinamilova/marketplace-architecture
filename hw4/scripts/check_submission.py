import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "a" and "href" in values:
            self.links.append(values["href"])


def main() -> None:
    requirements = (ROOT / "requirements.md").read_text(encoding="utf-8")
    rfc = (ROOT / "RFC-001.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^\| FR-\d+ \|", requirements, re.M)) == 10
    assert len(re.findall(r"^\| NFR-\d+ \|", requirements, re.M)) == 10
    assert len(re.findall(r"^\| ASR-\d+\. ", requirements, re.M)) == 6
    for i in range(1, 7):
        assert f"ASR-{i:02}" in rfc
    for i in range(1, 14):
        assert re.search(rf"^## {i}\. ", rfc, re.M), f"Missing RFC section {i}"
    dau = 3_000_000
    daily = [dau * n for n in (2, 3, 5)]
    assert daily == [6_000_000, 9_000_000, 15_000_000]
    assert sum(daily) == 30_000_000
    assert round(daily[0] / 86400, 1) == 69.4
    assert round(sum(daily) / 86400, 1) == 347.2
    assert round(1_000_000 / 1800) == 556
    attempts = Decimal('1') + Decimal('0.08') + Decimal('0.01')
    assert attempts * daily[0] == 6_540_000
    assert (Decimal(30) + Decimal('6.54')) * 7 * 3 == Decimal('767.34')
    assert 3 * 10_000 + 12 * 2_100 == 55_200
    assert (Decimal('.99') - Decimal('.92')) / Decimal('.08') == Decimal('.875')
    assert (Decimal('.9995') - Decimal('.92')) / Decimal('.08') == Decimal('.99375')
    for prefix in ('FR', 'NFR', 'ASR'):
        maximum = 6 if prefix == 'ASR' else 10
        for code in re.findall(rf'\b{prefix}-(\d\d)\b', requirements + rfc):
            assert 1 <= int(code) <= maximum, (prefix, code)
    sources = sorted((ROOT / "diagrams").glob("*.puml"))
    assert len(sources) == 6
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert text.count("@startuml") == text.count("@enduml") == 1
        assert "!include https" not in text
        svg = source.with_suffix(".svg")
        data = svg.read_text(encoding="utf-8")
        assert "Syntax Error" not in data and "An error has occured" not in data, svg
        assert ET.fromstring(data).tag == "{http://www.w3.org/2000/svg}svg", svg
    for page in ROOT.glob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", page.read_text(encoding="utf-8")):
            if not target.startswith(("https://", "http://", "#")):
                assert (ROOT / target.split("#")[0]).exists(), (page, target)
    output = (ROOT / "submission.html").read_text(encoding="utf-8")
    assert output.count("<svg ") == 6
    assert '<section id="requirements">' in output and '<section id="rfc">' in output
    assert "<script" not in output and '<img src="http' not in output
    assert "<table>" in output and "![" not in output
    parser = DocumentParser()
    parser.feed(output)
    duplicates = [key for key, count in Counter(parser.ids).items() if count > 1]
    assert not duplicates, f'Duplicate HTML/SVG IDs: {duplicates}'
    for target in parser.links:
        if target.startswith('#'):
            assert target[1:] in parser.ids, target
        elif not target.startswith(('https://', 'http://')):
            assert (ROOT / target.split('#')[0]).exists(), target
    sources = sorted(
        [ROOT / p for p in ('requirements.md', 'RFC-001.md')]
        + list((ROOT / 'diagrams').glob('*.svg'))
        + list((ROOT / 'diagrams').glob('*.puml'))
        + [ROOT / 'scripts/build_submission.py']
    )
    digest = hashlib.sha256()
    for path in sources:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0' + path.read_bytes())
    assert f'content="{digest.hexdigest()}"' in output, 'Rebuild stale submission.html'
    print("PASS: IDs/sections, recalculated capacity, 6 PlantUML/SVG pairs, links, unique inline IDs, current standalone HTML")


if __name__ == "__main__":
    main()
