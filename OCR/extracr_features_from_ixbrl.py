import re
import math
from lxml import etree

IX = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI = "http://www.xbrl.org/2003/instance"

def parse_value(el, raw):
    text = raw.strip().replace(",", "").replace("£", "").strip()

    if text.strip("-–—") == "":
        return 0.0

    negative = el.get("sign") == "-"

    value = abs(float(text)) * 10 ** int(el.get("scale", 0))

    return -value if negative else value

def ixbrl_financials_extract(path):
    root = etree.parse(path).getroot()

    dates = {}
    for ctx in root.iter(f"{{{XBRLI}}}context"):
        instant = ctx.find(f".//{{{XBRLI}}}instant")
        end = ctx.find(f".//{{{XBRLI}}}endDate")
        node = instant if instant is not None else end
        if node is not None:
            dates[ctx.get("id")] = node.text

    out = {}
    for el in root.iter(f"{{{IX}}}nonFraction"):
        raw = re.sub(r"[^\d.\-]", "", "".join(el.itertext()))
        if not raw:
            continue
        value = parse_value(el, raw)
        date = dates.get(el.get("contextRef"))

        concept = el.get("name")         
        concept = concept.split(":")[-1]

        if date not in out:               
            out[date] = {}

        out[date][concept] = value

    return out
