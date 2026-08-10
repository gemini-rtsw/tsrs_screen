#!/usr/bin/env python3
"""Extract the authoritative PLC bit map from the TSRS design spreadsheet.

Source: "BFO PLC bit-grouping for TSRS Status Screen.xlsx" (TSRS design docs, Mar 2015).
Output: reference/plc_bits.csv  -- one row per PLC condition bit, plus which
        summary bit(s) it feeds.

This freezes the 2015 design authority into the repo so the panel can be diffed
against it. Re-run only if the spreadsheet is revised.

    python3 tools/plc_bits_from_xlsx.py "/path/to/BFO PLC bit-grouping....xlsx"
"""
import csv
import pathlib
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
QN = "{%s}" % NS["m"]


def col_index(ref):
    letters = re.match(r"([A-Z]+)", ref).group(1)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheet(path):
    """Yield rows of the first worksheet as lists of stripped strings."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(QN + "t")))

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target")
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    rid = wb.find("m:sheets", NS)[0].get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    target = rels[rid]
    target = ("xl/" + target) if not target.startswith("/") else target[1:]

    for row in ET.fromstring(z.read(target)).iter(QN + "row"):
        cells = {}
        for c in row.findall("m:c", NS):
            v, is_ = c.find("m:v", NS), c.find("m:is", NS)
            if is_ is not None:
                val = "".join(x.text or "" for x in is_.iter(QN + "t"))
            elif v is None:
                val = ""
            elif c.get("t") == "s":
                val = shared[int(v.text)]
            else:
                val = v.text or ""
            cells[col_index(c.get("r"))] = " ".join(val.split())
        if cells:
            yield [cells.get(i, "") for i in range(max(cells) + 1)]


def main():
    src = pathlib.Path(sys.argv[1]).expanduser()
    rows = list(read_sheet(src))
    header = rows[0]

    # Summary columns are the ones naming a B3/25x-26x bit in the header.
    summaries = {}
    for i, h in enumerate(header):
        m = re.search(r"\bB3/(2[0-9]{2})\b", h)
        if m:
            summaries[i] = (h.split(" B3/")[0].strip(), "B3/" + m.group(1))

    out = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        desc, bit = row[0].strip(), row[1].strip()
        if not re.fullmatch(r"B3/\d+", bit):
            continue
        feeds = [summaries[i][1] for i in summaries
                 if i < len(row) and row[i].strip()]
        out.append({
            "plc_bit": bit,
            "description": desc,
            "feeds": ";".join(feeds),
            "epics_pv": plc_to_pv(bit),
        })

    dest = pathlib.Path(__file__).resolve().parent.parent / "reference" / "plc_bits.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["plc_bit", "epics_pv", "description", "feeds"])
        w.writeheader()
        w.writerows(out)

    smry = pathlib.Path(__file__).resolve().parent.parent / "reference" / "plc_summary_bits.csv"
    with smry.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["summary_bit", "name", "derivation"])
        for i in sorted(summaries):
            w.writerow([summaries[i][1], summaries[i][0], header[i]])

    print("wrote %s (%d condition bits)" % (dest, len(out)))
    print("wrote %s (%d summary bits)" % (smry, len(summaries)))


def plc_to_pv(bit):
    """B3/n -> bfo:cond{N}bits.B{h}.

    Verified against six independent bits in the as-built OPI files:
      B3/133 -> cond5bits.B5 (M1 Cover VFD Ready)
      B3/111 -> cond3bits.BF (Locking Pins OK)
      B3/134 -> cond5bits.B6 (Instrument Cover Open or Closed)
      B3/130 -> cond5bits.B2 (PLX CB closed)
      B3/131 -> cond5bits.B3 (All E-stops OK)
      B3/144 -> cond6bits.B0 (ECS PLC DH+ access enabled)
    """
    n = int(bit.split("/")[1])
    if n < 64:
        return ""
    off = n - 64
    return "bfo:cond%dbits.B%X" % (off // 16 + 1, off % 16)


if __name__ == "__main__":
    main()
