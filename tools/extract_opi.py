#!/usr/bin/env python3
"""Extract the TSRS indicator map from the as-built CS-Studio BOY .opi files.

Reads   ../CSS/bfo/*.opi          (the as-built displays)
        reference/plc_bits.csv    (2015 design authority)
Writes  tsrs_indicators.csv       (as-built map -- source of truth for the panel)
        reference/gaps.csv        (design bits with no indicator on any screen)

Pairs each LED with its caption geometrically, because BOY stores widgets as a
flat positional list with no label/PV association. Grouping containers are
descended into and coordinates resolved to absolute.

    python3 tools/extract_opi.py
"""
import csv
import os
import pathlib
import re
import sys
from xml.etree import ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Where the as-built CS-Studio workspace lives. This repo is separate from the
# cssapp workspace repo, so the path is configurable; the default assumes the
# two are checked out side by side.
OPI_DIR = pathlib.Path(
    os.environ.get("TSRS_OPI_DIR", ROOT.parent / "cssapp" / "CSS" / "bfo")
).expanduser()

# bfo:<x>Status -> summary bit, per "BFO PLC bit-grouping for TSRS Status Screen.xlsx".
# Lights (B3/262) and PLC (B3/267) have no *Status record and no screen indicator.
SUMMARY_BITS = {
    "bfo:mcsStatus":  "B3/256", "bfo:crcsStatus": "B3/257",
    "bfo:ecsStatus":  "B3/258", "bfo:hbsStatus":  "B3/259",
    "bfo:mcStatus":   "B3/260", "bfo:pinStatus":  "B3/261",
    "bfo:icStatus":   "B3/263", "bfo:lotoStatus": "B3/264",
    "bfo:azStatus":   "B3/265", "bfo:elStatus":   "B3/266",
    "bfo:emoStatus":  "B3/268",
}

STOP = {"the", "and", "a", "of", "to", "in", "no", "not", "or", "is", "for", "ok"}


def text(el, tag, default=""):
    child = el.find(tag)
    return (child.text or default) if child is not None else default


def walk(el, ox=0, oy=0, out=None):
    """Flatten widgets to absolute coordinates, descending grouping containers."""
    if out is None:
        out = []
    for w in el.findall("widget"):
        tid = w.get("typeId", "").split(".")[-1]
        x, y = int(text(w, "x", "0") or 0), int(text(w, "y", "0") or 0)
        ax, ay = ox + x, oy + y
        out.append({
            "type": tid, "x": ax, "y": ay,
            "w": int(text(w, "width", "0") or 0),
            "h": int(text(w, "height", "0") or 0),
            "pv": text(w, "pv_name").strip(),
            "text": " ".join(text(w, "text").split()),
            "links": [text(a, "path").strip() for a in w.findall("./actions/action")
                      if a.get("type") == "OPEN_DISPLAY"],
        })
        if tid == "groupingContainer":
            walk(w, ax, ay, out)
    return out


def nearest_label(led, labels):
    """Caption for an LED: vertically overlapping, nearest horizontally."""
    mid = led["y"] + led["h"] / 2
    cands = [l for l in labels
             if l["text"] and l["y"] - 6 <= mid <= l["y"] + l["h"] + 6]
    if not cands:
        cands = [l for l in labels
                 if l["text"] and abs(l["y"] + l["h"] / 2 - mid) < 18]
    if not cands:
        return ""
    return min(cands, key=lambda l: abs(l["x"] - led["x"]))["text"]


def tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP and len(t) > 1}


def drift(label, design):
    """Rough agreement between screen caption and design description."""
    a, b = tokens(label), tokens(design)
    if not a or not b:
        return ""
    overlap = len(a & b) / min(len(a), len(b))
    return "" if overlap >= 0.4 else "CHECK(%.0f%%)" % (overlap * 100)


def pv_to_plc(pv):
    m = re.fullmatch(r"bfo:cond(\d+)bits\.B([0-9A-F])", pv)
    if not m:
        return ""
    return "B3/%d" % (64 + (int(m.group(1)) - 1) * 16 + int(m.group(2), 16))


def main():
    if not OPI_DIR.is_dir():
        sys.exit("cannot find %s\n"
                 "Set TSRS_OPI_DIR to the cssapp CSS/bfo directory." % OPI_DIR)

    design = {}
    with (ROOT / "reference" / "plc_bits.csv").open() as fh:
        for r in csv.DictReader(fh):
            design[r["plc_bit"]] = r

    rows, seen_bits = [], set()
    for path in sorted(OPI_DIR.glob("*.opi")):
        screen = path.stem.replace("_detail", "").replace("bfo_", "")
        widgets = walk(ET.parse(path).getroot())
        labels = [w for w in widgets if w["type"] == "Label"]
        leds = [w for w in widgets if w["type"] in ("LED", "TextUpdate")]
        links = {w["pv"]: w["links"][0] for w in widgets if w["links"] and w["pv"]}
        # Overview links hang off Labels named after the PV, not the LED itself.
        by_name = {}
        for w in widgets:
            if w["links"]:
                by_name[w["links"][0].replace("_detail.opi", "").replace(".opi", "")] = w["links"][0]

        for order, led in enumerate(sorted(leds, key=lambda w: (w["y"], w["x"]))):
            pv = led["pv"]
            if not pv:
                continue
            plc = SUMMARY_BITS.get(pv) or pv_to_plc(pv)
            kind = "summary" if pv in SUMMARY_BITS else "condition"
            label = nearest_label(led, labels)
            d = design.get(plc, {})
            if kind == "condition":
                seen_bits.add(plc)
            rows.append({
                "screen": screen,
                "order": order,
                "kind": kind,
                "pv": pv,
                "plc_bit": plc,
                "label": label,
                "links_to": by_name.get(screen, "") if screen == "overview" else "",
                "design_description": d.get("description", ""),
                "review": drift(label, d.get("description", "")) if d else "",
                "source": "as-built",
            })

    # Overview: attach each summary LED's drill-down target.
    link_for = {"bfo:%sStatus" % k: "%s_detail" % k for k in
                ("mcs", "crcs", "ecs", "hbs", "mc", "pin", "ic", "loto", "az", "el", "emo")}
    for r in rows:
        if r["screen"] == "overview" and r["kind"] == "summary":
            r["links_to"] = link_for.get(r["pv"], "")

    dest = ROOT / "tsrs_indicators.csv"
    cols = ["screen", "order", "kind", "pv", "plc_bit", "label",
            "links_to", "design_description", "review", "source"]
    with dest.open("w", newline="") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        w.writerows(rows)

    # Design bits that reach no screen.
    gaps = []
    summary_on_screen = {r["plc_bit"] for r in rows if r["kind"] == "summary"}
    with (ROOT / "reference" / "plc_summary_bits.csv").open() as fh:
        for r in csv.DictReader(fh):
            if r["summary_bit"] not in summary_on_screen:
                gaps.append({"level": "summary", "plc_bit": r["summary_bit"],
                             "epics_pv": "", "description": r["name"],
                             "feeds": r["derivation"]})
    for bit, d in sorted(design.items(), key=lambda kv: int(kv[0].split("/")[1])):
        if bit not in seen_bits and d["description"] and d["description"] != "Unassigned":
            gaps.append({"level": "condition", "plc_bit": bit,
                         "epics_pv": d["epics_pv"], "description": d["description"],
                         "feeds": d["feeds"]})
    gdest = ROOT / "reference" / "gaps.csv"
    with gdest.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["level", "plc_bit", "epics_pv", "description", "feeds"])
        w.writeheader()
        w.writerows(gaps)

    pvs = {r["pv"] for r in rows}
    print("wrote %s" % dest)
    print("  %d indicators across %d screens, %d distinct PVs"
          % (len(rows), len({r['screen'] for r in rows}), len(pvs)))
    print("  %d summary, %d condition"
          % (sum(r["kind"] == "summary" for r in rows),
             sum(r["kind"] == "condition" for r in rows)))
    flagged = [r for r in rows if r["review"]]
    print("  %d captions to review vs design wording" % len(flagged))
    for r in flagged:
        print("      %-9s %-20s %-34s | design: %s"
              % (r["screen"], r["pv"], r["label"][:34], r["design_description"][:44]))
    print("wrote %s (%d design bits with no indicator)" % (gdest, len(gaps)))


if __name__ == "__main__":
    main()
