"""Print the header of an htcp(5) cloud file and the liquid water range.

    python3 htcp_info.py clouds.htcp
"""

import struct
import sys


def main() -> None:
    path = sys.argv[1]
    with open(path, "rb") as f:
        pagesize, irregular = struct.unpack("<Qb", f.read(9))
        nx, ny, nz, nt = struct.unpack("<4I", f.read(16))
        lower = struct.unpack("<3d", f.read(24))
        nvz = nz if irregular else 1
        dx, dy = struct.unpack("<2d", f.read(16))
        dz = struct.unpack(f"<{nvz}d", f.read(8 * nvz))
        header_end = f.tell()

        def align(off: int) -> int:
            return (off + pagesize - 1) // pagesize * pagesize

        n = nx * ny * nz * nt
        rvt = align(header_end)
        rct = align(rvt + 8 * n)
        f.seek(rct)
        # Liquid water mixing ratio, scanned in chunks to keep memory flat.
        lo, hi, cloudy, left = float("inf"), 0.0, 0, n
        while left:
            k = min(left, 1 << 20)
            vals = struct.unpack(f"<{k}d", f.read(8 * k))
            lo = min(lo, min(vals))
            hi = max(hi, max(vals))
            cloudy += sum(1 for v in vals if v > 1e-6)
            left -= k

    ztop = lower[2] + sum(dz) if irregular else lower[2] + nz * dz[0]
    print(f"definition {nx} x {ny} x {nz} x {nt} (time)")
    print(f"lower {lower}, cell {dx} x {dy} m, z {'irregular' if irregular else f'{dz[0]} m'}")
    print(f"extent x [{lower[0]}, {lower[0] + nx * dx}] y [{lower[1]}, {lower[1] + ny * dy}] z [{lower[2]}, {ztop}] m")
    print(f"RCT range [{lo:.3g}, {hi:.3g}] kg/kg, cloudy cells (> 1e-6) {cloudy} of {n} ({100 * cloudy / n:.2f} %)")


if __name__ == "__main__":
    main()
