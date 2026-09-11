import argparse
import os
import sys
from typing import List, Optional

from proj7k.parser import parse_osu_7k
from proj7k.renderer import RenderOptions, render_slice
from proj7k.window import extract_time_window


def parse_timestamp(ts_str: str) -> float:
    """Parse various timestamp representations into milliseconds.
    
    Supported formats:
    - "12500" or "12500.5" -> raw milliseconds
    - "12.5s" -> seconds
    - "mm:ss" or "mm:ss.xxx" -> minutes and seconds
    - "hh:mm:ss" or "hh:mm:ss.xxx" -> hours, minutes, seconds
    """
    ts = ts_str.strip().lower()
    if ts.endswith("s"):
        return float(ts[:-1]) * 1000.0

    if ":" in ts:
        parts = ts.split(":")
        if len(parts) == 2:
            m = float(parts[0])
            s = float(parts[1])
            return (m * 60.0 + s) * 1000.0
        elif len(parts) == 3:
            h = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            return (h * 3600.0 + m * 60.0 + s) * 1000.0

    return float(ts)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="proj7k-slice: Generate high-definition vertical scroll slices from 7k osu! beatmaps."
    )
    parser.add_argument("--osu", required=True, help="Path to .osu beatmap file (must be 7K)")
    parser.add_argument("--start", required=True, help="Start time (e.g. '12000', '01:23.500', '15.2s')")
    parser.add_argument("--end", required=True, help="End time (e.g. '18000', '01:29.500', '21.2s')")
    parser.add_argument("-o", "--output", help="Output PNG image path (default: slice_<start>_<end>.png)")
    parser.add_argument(
        "--pps", "--scale", dest="pps", type=float, default=400.0, help="Vertical scale in pixels per second (default: 400.0)"
    )
    parser.add_argument(
        "--direction", choices=["up", "down"], default="up", help="Scroll direction: 'up' (bottom-to-top) or 'down' (default: up)"
    )
    parser.add_argument("--col-width", type=int, default=40, help="Column width in pixels (default: 40)")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not os.path.exists(args.osu):
        print(f"Error: File not found: {args.osu}", file=sys.stderr)
        return 1

    start_ms = parse_timestamp(args.start)
    end_ms = parse_timestamp(args.end)

    if start_ms > end_ms:
        start_ms, end_ms = end_ms, start_ms

    try:
        beatmap = parse_osu_7k(args.osu)
    except Exception as e:
        print(f"Error parsing .osu file: {e}", file=sys.stderr)
        return 1

    window = extract_time_window(beatmap, start_ms, end_ms)

    opts = RenderOptions(
        pixels_per_second=args.pps,
        column_width=args.col_width,
        scroll_direction=args.direction,
    )
    img = render_slice(window, opts)

    out_path = args.output
    if not out_path:
        out_path = f"slice_{int(start_ms)}_{int(end_ms)}.png"

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path, format="PNG")

    print(
        f"Generated slice: {start_ms:.0f}ms -> {end_ms:.0f}ms "
        f"({len(window.hit_objects)} notes, {len(window.barlines)} barlines) -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
