"""Summarise the starter pack's SLUM mrumtl files: visible albedo and 9-10 um emissivity.

    python3 slum_table.py SPK/materials/slum

Visible albedo is the plain mean reflectivity over 400-700 nm; emissivity is
1 - mean reflectivity over 9-10 um (Kirchhoff, opaque surfaces). Also prints
the wavelength range each file covers and any gap wider than 1 um.
"""

import sys
from pathlib import Path


def read(path: Path) -> list[tuple[float, str, float]]:
    rows = []
    for line in path.read_text().splitlines()[1:]:
        line = line.split("#", 1)[0].split()
        if len(line) == 3:
            rows.append((float(line[0]), line[1], float(line[2])))
    return rows


def mean(rows, lo, hi) -> float:
    v = [r for wl, _, r in rows if lo <= wl <= hi]
    return sum(v) / len(v) if v else float("nan")


def main() -> None:
    print(f"{'name':6s} {'vis albedo':>10s} {'eps 9-10um':>10s} {'range um':>14s}  kinds      gaps > 1 um")
    for f in sorted(Path(sys.argv[1]).glob("*.mrumtl")):
        rows = read(f)
        wls = [wl for wl, _, _ in rows]
        gaps = [f"{a / 1000:.1f}-{b / 1000:.1f}" for a, b in zip(wls, wls[1:]) if b - a > 1000]
        kinds = "/".join(sorted({k for _, k, _ in rows}))
        print(f"{f.stem:6s} {mean(rows, 400, 700):10.3f} {1 - mean(rows, 9000, 10000):10.3f} "
              f"{wls[0] / 1000:6.2f}-{wls[-1] / 1000:6.2f}  {kinds:10s} {' '.join(gaps)}")


if __name__ == "__main__":
    main()
