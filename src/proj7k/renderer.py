from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Dict, Tuple
from PIL import Image, ImageDraw, ImageFont

from proj7k.parser import HitObject, NoteType
from proj7k.window import SliceWindow


class ScrollDirection(Enum):
    UP = "up"      # Time increases upward (standard VSRG/mania)
    DOWN = "down"  # Time increases downward


def format_timestamp(ms: float) -> str:
    """Format milliseconds into [hh:]mm:ss.xx string."""
    ms_val = max(0.0, ms)
    total_seconds = ms_val / 1000.0
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60.0

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:05.2f}"
    return f"{minutes:02d}:{seconds:05.2f}"


@dataclass
class RenderOptions:
    pixels_per_second: float = 400.0
    column_width: int = 40
    note_height: int = 12
    margin_left: int = 60
    margin_right: int = 65
    margin_top: int = 30
    margin_bottom: int = 30
    scroll_direction: ScrollDirection = ScrollDirection.UP
    
    # 7K Lane colors: W, B, W, Y, W, B, W
    lane_colors: Tuple[Tuple[int, int, int], ...] = (
        (235, 235, 245),  # Lane 0: White
        (65, 135, 245),   # Lane 1: Blue
        (235, 235, 245),  # Lane 2: White
        (250, 205, 45),   # Lane 3: Yellow (Center / Space)
        (235, 235, 245),  # Lane 4: White
        (65, 135, 245),   # Lane 5: Blue
        (235, 235, 245),  # Lane 6: White
    )
    bg_color: Tuple[int, int, int] = (20, 22, 28)
    margin_bg_color: Tuple[int, int, int] = (14, 15, 20)
    divider_color: Tuple[int, int, int] = (45, 48, 58)
    measure_line_color: Tuple[int, int, int] = (195, 200, 215)
    beat_line_color: Tuple[int, int, int] = (60, 65, 80)
    ruler_tick_color: Tuple[int, int, int] = (85, 90, 110)
    text_color: Tuple[int, int, int] = (160, 165, 180)


def render_slice(window: SliceWindow, options: RenderOptions = RenderOptions()) -> Image.Image:
    duration_ms = max(1.0, window.end_ms - window.start_ms)
    time_height = int((duration_ms / 1000.0) * options.pixels_per_second)
    
    playfield_w = 7 * options.column_width
    total_w = options.margin_left + playfield_w + options.margin_right
    total_h = options.margin_top + time_height + options.margin_bottom

    img = Image.new("RGB", (total_w, total_h), options.margin_bg_color)
    draw = ImageDraw.Draw(img)

    playfield_x0 = options.margin_left
    playfield_x1 = playfield_x0 + playfield_w
    draw.rectangle([playfield_x0, 0, playfield_x1, total_h], fill=options.bg_color)

    def time_to_y(t_ms: float) -> float:
        frac = (t_ms - window.start_ms) / duration_ms
        if options.scroll_direction == ScrollDirection.UP or options.scroll_direction == "up":
            return (options.margin_top + time_height) - (frac * time_height)
        else:
            return options.margin_top + (frac * time_height)

    def column_bounds(col: int) -> Tuple[int, int]:
        x0 = playfield_x0 + col * options.column_width + 2
        x1 = x0 + options.column_width - 4
        return x0, x1

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    # Draw barlines and beatlines
    for bar in window.barlines:
        barline_y = time_to_y(bar.time)
        if bar.is_measure_start:
            draw.line([(playfield_x0, barline_y), (playfield_x1, barline_y)], fill=options.measure_line_color, width=2)
            lbl = f"M{bar.measure_index + 1}"
            draw.text((10, barline_y - 6), lbl, fill=options.measure_line_color, font=font)
        else:
            draw.line([(playfield_x0, barline_y), (playfield_x1, barline_y)], fill=options.beat_line_color, width=1)

    # Draw lane dividers
    for col in range(8):
        dx = playfield_x0 + col * options.column_width
        draw.line([(dx, 0), (dx, total_h)], fill=options.divider_color, width=1)

    # Draw LN bodies
    for ho in window.hit_objects:
        if ho.note_type == NoteType.LN:
            col_x0, col_x1 = column_bounds(ho.column)
            end_t = ho.end_time if ho.end_time is not None else ho.time
            vis_start_t = max(window.start_ms, ho.time)
            vis_end_t = min(window.end_ms, end_t)

            y_start = time_to_y(vis_start_t)
            y_end = time_to_y(vis_end_t)

            top_y = min(y_start, y_end)
            bottom_y = max(y_start, y_end)

            lane_rgb = options.lane_colors[ho.column]
            body_rgb = (lane_rgb[0] // 3, lane_rgb[1] // 3, lane_rgb[2] // 3)
            border_rgb = (lane_rgb[0] // 2, lane_rgb[1] // 2, lane_rgb[2] // 2)

            draw.rectangle([col_x0 + 2, top_y, col_x1 - 2, bottom_y], fill=body_rgb, outline=border_rgb)

            # Tail release bar
            if window.start_ms <= end_t <= window.end_ms:
                tail_y = time_to_y(end_t)
                draw.rectangle([col_x0, tail_y - 2, col_x1, tail_y + 2], fill=lane_rgb, outline=(255, 255, 255))

    # Draw Rice notes and LN heads
    for ho in window.hit_objects:
        if ho.time < window.start_ms or ho.time > window.end_ms:
            continue

        col_x0, col_x1 = column_bounds(ho.column)
        y = time_to_y(ho.time)
        half_h = options.note_height // 2
        lane_rgb = options.lane_colors[ho.column]

        draw.rectangle([col_x0, y - half_h, col_x1, y + half_h], fill=lane_rgb, outline=(255, 255, 255))

    # Draw timeline ruler ticks and labels along right margin
    # Step interval: 1000ms if duration > 3000ms, else 500ms
    tick_step_ms = 1000.0 if duration_ms > 3000.0 else 500.0
    first_tick = math.ceil(window.start_ms / tick_step_ms) * tick_step_ms
    
    current_tick = first_tick
    while current_tick <= window.end_ms:
        ty = time_to_y(current_tick)
        draw.line([(playfield_x1, ty), (playfield_x1 + 6, ty)], fill=options.ruler_tick_color, width=1)
        tick_label = format_timestamp(current_tick)
        draw.text((playfield_x1 + 8, ty - 6), tick_label, fill=options.text_color, font=font)
        current_tick += tick_step_ms

    # Boundary timestamps (always drawn if not overlapping tick)
    y_start_edge = time_to_y(window.start_ms)
    draw.text((playfield_x1 + 8, y_start_edge - 6), format_timestamp(window.start_ms), fill=options.text_color, font=font)
    
    y_end_edge = time_to_y(window.end_ms)
    draw.text((playfield_x1 + 8, y_end_edge - 6), format_timestamp(window.end_ms), fill=options.text_color, font=font)

    return img
