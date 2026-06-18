#!/usr/bin/env python3
"""
Generate a printable PDF with annotation comments rendered in a sidebar.

Uses insert_pdf to copy pages exactly (preserving all annotations, highlights,
underlines, colors, and orientation), then expands pages that have text comments
to add a sidebar on the right.  Sidebar width is capped so total page width
never exceeds page height (keeps portrait orientation for printing).  If
comments overflow the available sidebar height, continuation pages are added.

Usage:
    python print_with_comments.py [input.pdf]

If no argument is given, all PDFs in ./input/ are processed.
Output goes to ./output/.
"""

import sys
import os
import fitz  # PyMuPDF

SIDEBAR_PAD = 8
MARGIN_V = 8                  # minimal vertical margin to maximise sidebar space
COMMENT_GAP = 6
FONT_SIZE = 7.5
LABEL_FONT_SIZE = 7
LINE_HEIGHT = FONT_SIZE * 1.35
CONNECTOR_COLOR = (0.5, 0.5, 0.5)
COMMENT_BG = (1.0, 1.0, 0.92)
SIDEBAR_LABEL_COLORS = {
    "Highlight": (0.6, 0.4, 0.0),
    "Underline": (0.7, 0.1, 0.15),
    "StrikeOut": (0.8, 0.0, 0.0),
    "Squiggly":  (0.0, 0.5, 0.0),
}


def collect_comments(page):
    """Return list of (y_position, annotation_type, comment_text, color) for annotations with content."""
    comments = []
    for annot in page.annots() or []:
        content = (annot.info.get("content") or "").strip()
        if not content:
            continue
        atype = annot.type[1]
        y_mid = (annot.rect.y0 + annot.rect.y1) / 2
        stroke = annot.colors.get("stroke") or []
        color = tuple(stroke) if len(stroke) == 3 else None
        comments.append((y_mid, atype, content, color))
    comments.sort(key=lambda c: c[0])
    return comments


def measure_block_height(text, text_area_width):
    """Measure exact height needed by doing a trial insert_textbox."""
    header = LABEL_FONT_SIZE + 10
    trial_h = 2000
    doc = fitz.open()
    page = doc.new_page(width=text_area_width, height=trial_h)
    remainder = page.insert_textbox(
        fitz.Rect(0, 0, text_area_width, trial_h),
        text, fontsize=FONT_SIZE, fontname="helv",
    )
    doc.close()
    text_height = trial_h - remainder
    return header + text_height + 12


def layout_comments(blocks, sidebar_top, sidebar_bottom):
    """Position comment blocks, avoiding overlaps. Returns index of first block that doesn't fit, or len(blocks)."""
    if not blocks:
        return 0

    for b in blocks:
        ideal_y = b.get("y_anchor", sidebar_top) - b["height"] / 2
        b["y"] = max(sidebar_top, min(ideal_y, sidebar_bottom - b["height"]))

    for i in range(1, len(blocks)):
        prev_bottom = blocks[i - 1]["y"] + blocks[i - 1]["height"] + COMMENT_GAP
        if blocks[i]["y"] < prev_bottom:
            blocks[i]["y"] = prev_bottom

    # Push up if last block overflows
    if blocks:
        overflow = (blocks[-1]["y"] + blocks[-1]["height"]) - sidebar_bottom
        if overflow > 0:
            for b in reversed(blocks):
                b["y"] -= overflow
                overflow = max(0, sidebar_top - b["y"])
                b["y"] = max(sidebar_top, b["y"])
                if overflow <= 0:
                    break
            for i in range(1, len(blocks)):
                prev_bottom = blocks[i - 1]["y"] + blocks[i - 1]["height"] + COMMENT_GAP
                if blocks[i]["y"] < prev_bottom:
                    blocks[i]["y"] = prev_bottom

    # Find how many actually fit
    fit_count = len(blocks)
    for i, b in enumerate(blocks):
        if b["y"] + b["height"] > sidebar_bottom + 2:
            fit_count = i
            break

    return fit_count


def draw_comment_blocks(page, blocks, sb_left, sb_right, sb_top, sb_bottom, content_x, show_connectors=True):
    """Draw comment blocks onto a page."""
    for block in blocks:
        y = block["y"]
        atype = block["atype"]
        height = block["height"]
        annot_color = block["color"]

        bg_rect = fitz.Rect(sb_left, y, sb_right, y + height)
        page.draw_rect(bg_rect, color=None, fill=COMMENT_BG)

        accent_color = annot_color or SIDEBAR_LABEL_COLORS.get(atype, (0.5, 0.5, 0.5))
        page.draw_rect(fitz.Rect(sb_left, y, sb_left + 3, y + height),
                        color=None, fill=accent_color)
        page.draw_rect(bg_rect, color=(0.78, 0.78, 0.78), width=0.3)

        if show_connectors and content_x is not None:
            block_mid_y = y + height / 2
            page.draw_line(fitz.Point(content_x - 4, block["y_anchor"]),
                           fitz.Point(sb_left, block_mid_y),
                           color=CONNECTOR_COLOR, width=0.4, dashes="[2 2]")
            page.draw_circle(fitz.Point(content_x - 4, block["y_anchor"]),
                             1.5, color=None, fill=accent_color)

        # Label
        page.insert_textbox(
            fitz.Rect(sb_left + SIDEBAR_PAD, y + 2, sb_right - SIDEBAR_PAD, y + LABEL_FONT_SIZE + 6),
            f"[{atype}]",
            fontsize=LABEL_FONT_SIZE, fontname="helv",
            color=SIDEBAR_LABEL_COLORS.get(atype, (0.4, 0.4, 0.4)),
        )

        # Comment text
        text_rect = fitz.Rect(sb_left + SIDEBAR_PAD, y + LABEL_FONT_SIZE + 8,
                              sb_right - SIDEBAR_PAD, y + height - 4)
        page.insert_textbox(
            text_rect, block["text"],
            fontsize=FONT_SIZE, fontname="helv",
            color=(0.1, 0.1, 0.1),
        )


def process_pdf(input_path, output_dir):
    basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{basename}_with_comments.pdf")

    src_doc = fitz.open(input_path)
    total_pages = len(src_doc)

    dst_doc = fitz.open()
    dst_doc.insert_pdf(src_doc)

    pages_with_comments = 0
    extra_pages_added = 0
    insert_offset = 0

    for page_idx in range(total_pages):
        src_page = src_doc[page_idx]
        comments = collect_comments(src_page)
        if not comments:
            continue

        pages_with_comments += 1
        actual_idx = page_idx + insert_offset
        dst_page = dst_doc[actual_idx]

        old_crop = dst_page.cropbox
        media = dst_page.mediabox
        page_height = old_crop.height
        page_width = old_crop.width

        # Sidebar width: fill remaining space so total width = page height (keeps portrait)
        sidebar_width = max(120, page_height - page_width - 6)
        content_x = page_width

        # Expand mediabox and cropbox
        content_edge = old_crop.x1
        new_right = content_edge + sidebar_width + 4
        dst_page.set_mediabox(fitz.Rect(media.x0, media.y0, max(media.x1, new_right), media.y1))
        dst_page.set_cropbox(fitz.Rect(old_crop.x0, old_crop.y0, new_right, old_crop.y1))

        page_rect = dst_page.rect
        sb_left = content_x + 4
        sb_right = content_x + sidebar_width
        sb_top = MARGIN_V
        sb_bottom = page_rect.height - MARGIN_V

        # White background for sidebar area
        dst_page.draw_rect(fitz.Rect(content_x - 1, 0, page_rect.width, page_rect.height),
                           color=None, fill=(1, 1, 1))
        dst_page.draw_line(fitz.Point(content_x, 0), fitz.Point(content_x, page_rect.height),
                           color=(0.82, 0.82, 0.82), width=0.5)

        text_area_width = sb_right - sb_left - SIDEBAR_PAD * 2

        # Build all comment blocks
        all_blocks = []
        for y_orig, atype, text, color in comments:
            height = measure_block_height(text, text_area_width)
            all_blocks.append({
                "y_anchor": y_orig,
                "height": height,
                "atype": atype,
                "color": color,
                "text": text,
            })

        # Layout and find how many fit on this page
        fit_count = layout_comments(all_blocks, sb_top, sb_bottom)

        if fit_count == 0:
            fit_count = 1

        fitting = all_blocks[:fit_count]
        overflow = all_blocks[fit_count:]

        draw_comment_blocks(dst_page, fitting, sb_left, sb_right, sb_top, sb_bottom, content_x)

        # Create continuation pages for overflow
        cont_page_num = 1
        while overflow:
            cont_page_width = sidebar_width + SIDEBAR_PAD * 2
            cont_page_height = page_height

            cont_idx = actual_idx + cont_page_num
            dst_doc.new_page(pno=cont_idx, width=cont_page_width, height=cont_page_height)
            cont_page = dst_doc[cont_idx]
            insert_offset += 1
            extra_pages_added += 1

            c_sb_left = SIDEBAR_PAD
            c_sb_right = cont_page_width - SIDEBAR_PAD
            c_sb_top = MARGIN_V + 14
            c_sb_bottom = cont_page_height - MARGIN_V

            # Header
            cont_page.insert_textbox(
                fitz.Rect(SIDEBAR_PAD, MARGIN_V, cont_page_width - SIDEBAR_PAD, MARGIN_V + 12),
                f"Comments continued (page {page_idx + 1})",
                fontsize=LABEL_FONT_SIZE, fontname="helv",
                color=(0.4, 0.4, 0.4),
            )

            # Re-layout overflow blocks for this continuation page (no anchors, stack from top)
            for b in overflow:
                b.pop("y", None)
                b["y_anchor"] = c_sb_top

            fit_count = layout_comments(overflow, c_sb_top, c_sb_bottom)
            if fit_count == 0:
                fit_count = 1

            fitting_cont = overflow[:fit_count]
            overflow = overflow[fit_count:]

            draw_comment_blocks(cont_page, fitting_cont, c_sb_left, c_sb_right,
                                c_sb_top, c_sb_bottom, None, show_connectors=False)
            cont_page_num += 1

    dst_doc.save(output_path, deflate=True, garbage=4)
    dst_doc.close()
    src_doc.close()

    print(f"Done: {output_path}")
    print(f"  {total_pages} pages, {pages_with_comments} with comments, {extra_pages_added} continuation pages added")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(script_dir, "input")
    output_dir = os.path.join(script_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        paths = sorted(
            os.path.join(input_dir, f)
            for f in os.listdir(input_dir)
            if f.lower().endswith(".pdf")
        )

    if not paths:
        print(f"No PDF files found in {input_dir}")
        sys.exit(1)

    for path in paths:
        print(f"Processing: {path}")
        process_pdf(path, output_dir)


if __name__ == "__main__":
    main()
