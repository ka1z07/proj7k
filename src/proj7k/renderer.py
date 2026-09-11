from dataclasses import dataclass, field
import math
from typing import Dict, Tuple
from PIL import Image, ImageDraw, ImageFont

from proj7k.parser import HitObject, NoteType
from proj7k.window import SliceWindow


@dataclass
class RenderOptions:
    pixels_per_second: float = 400.0
    column_width: int = 40
    note_height: int = 12
    margin_left: int = 60
    margin_right: int = 20
    margin_top: int = 30
    margin_bottom: int = 30
    scroll_direction: str = "up"  # "up" (time increases upward) or "down"
    
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
    text_color: Tuple[int, int, int] = (160, 165, 180)


def render_slice(window: SliceWindow, options: RenderOptions = RenderOptions()) -> Image.Image:
    duration_ms = max(1.0, window.end_ms - window.start_ms)
    time_height = int((duration_ms / 1000.0) * options.pixels_per_second)
    
    playfield_w = 7 * options.column_width
    total_w = options.margin_left + playfield_w + options.margin_right
    total_h = options.margin_top + time_height + options.margin_bottom

    # Base image
    img = Image.new("RGB", (total_w, total_h), options.margin_bg_color)
    draw = ImageDraw.Draw(img)

    # Draw playfield background
    pf_x0 = options.margin_left
    pf_x1 = pf_x0 + playfield_w
    draw.rectangle([pf_x0, 0, pf_x1, total_h], fill=options.bg_color)

    # Time to Y coordinate conversion
    def time_to_y(t_ms: float) -> float:
        # Clamped fraction within [start_ms, end_ms]
        frac = (t_ms - window.start_ms) / duration_ms
        if options.scroll_direction == "up":
            # Time increases upward (bottom is start_ms, top is end_ms)
            y = (options.margin_top + time_height) - (frac * time_height)
        else:
            # Time increases downward (top is start_ms, bottom is end_ms)
            y = options.margin_top + (frac * time_height)
        return y

    # Try loading a font, fallback to default
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    # Draw barlines and beatlines
    for bar in window.barlines:
        by = time_to_y(bar.time)
        if bar.is_measure_start:
            # Thick measure line across playfield
            draw.line([(pf_x0, by), (pf_x1, by)], fill=options.measure_line_color, width=2)
            # Draw measure number in left margin
            lbl = f"M{bar.measure_index + 1}"
            draw.text((10, by - 6), lbl, fill=options.measure_line_color, font=font)
        else:
            # Subtle beat line
            draw.line([(pf_x0, by), (pf_x1, by)], fill=options.beat_line_color, width=1)

    # Draw lane dividers
    for col in range(8):
        dx = pf_x0 + col * options.column_width
        draw.line([(dx, 0), (dx, total_h)], fill=options.divider_color, width=1)

    # Draw LN bodies first so notes sit on top
    for ho in window.hit_objects:
        if ho.note_type == NoteType.LN:
            col_x0 = pf_x0 + ho.column * options.column_width + 2
            col_x1 = col_x0 + options.column_width - 4
            
            end_t = ho.end_time if ho.end_time is not None else ho.time
            # Clamp visual endpoints to window for drawing
            vis_start_t = max(window.start_ms, ho.time)
            vis_end_t = min(window.end_ms, end_t)

            y_start = time_to_y(vis_start_t)
            y_end = time_to_y(vis_end_t)

            top_y = min(y_start, y_end)
            bottom_y = max(y_start, y_end)

            lane_rgb = options.lane_colors[ho.column]
            # Darkened body color
            body_rgb = (lane_rgb[0] // 3, lane_rgb[1] // 3, lane_rgb[2] // 3)
            border_rgb = (lane_rgb[0] // 2, lane_rgb[1] // 2, lane_rgb[2] // 2)

            draw.rectangle([col_x0 + 2, top_y, col_x1 - 2, bottom_y], fill=body_rgb, outline=border_rgb)

            # Draw tail release line if inside window
            if window.start_ms <= end_t <= window.end_ms:
                tail_y = time_to_y(end_t)
                draw.rectangle(
                    [col_x0, tail_y - 2, col_x1, tail_y + 2],
                    fill=lane_rgb,
                    outline=(255, 255, 255),
                )

    # Draw Rice notes and LN heads
    for ho in window.hit_objects:
        if ho.time < window.start_ms or ho.time > window.end_ms:
            continue

        col_x0 = pf_x0 + ho.column * options.column_width + 2
        col_x1 = col_x0 + options.column_width - 4
        
        y = time_to_y(ho.time)
        half_h = options.note_height // 2
        lane_rgb = options.lane_colors[ho.column]

        # Draw note pill
        draw.rectangle(
            [col_x0, y - half_h, col_x1, y + half_h],
            fill=lane_rgb,
            outline=(255, 255, 255),
        )

    # Draw timestamp markings every 1 second or at edges
    # Start ms label
    s_min = int(window.start_ms // 60000)
    s_sec = (window.start_ms % 60000) / 1000.0
    start_lbl = f"{s_min:02d}:{s_sec:05.2f}"
    y_start_edge = time_to_y(window.start_ms)
    draw.text((pf_x1 + 4, y_start_edge - 6), start_lbl, fill=options.text_color, font=font)

    # End ms label
    e_min = int(window.end_ms // 60000)
    e_sec = (window.end_ms % 60000) / 1000.0
    end_lbl = f"{e_min:02d}:{e_sec:05.2f}"
    y_end_edge = time_to_y(window.end_ms)
    draw.text((pf_x1 + 4, y_end_edge - 6), end_lbl, fill=options.text_color, font=font)

    return img
