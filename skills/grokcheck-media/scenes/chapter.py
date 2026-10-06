"""The Manim scene for one concept video chapter, read from `beats.json` beside it."""  # noqa: INP001

import json
from itertools import pairwise
from pathlib import Path

from manim import (
    DOWN,
    LEFT,
    RIGHT,
    UL,
    Arrow,
    Code,
    Dot,
    FadeIn,
    FadeOut,
    LaggedStart,
    Line,
    Paragraph,
    RoundedRectangle,
    Scene,
    Text,
    VGroup,
    config,
)
from pygments.style import Style
from pygments.token import (
    Comment,
    Keyword,
    Name,
    Number,
    Operator,
    String,
    Token,
)

BG = "#ffffff"
FG = "#1f2328"
MUTED = "#59636e"
BORDER = "#d1d9e0"
PANEL = "#f6f8fa"
ACCENT = "#0969da"
SANS = "DejaVu Sans"
MONO = "DejaVu Sans Mono"
TRANSITION = 0.3
BODY_WIDTH = 13.0
BODY_HEIGHT = 5.6
BODY_TOP = 2.55
BODY_LEFT = -6.5

CHAPTER = json.loads(Path(__file__).with_name("beats.json").read_text("utf-8"))
config.background_color = BG


class GithubLight(Style):
    """The lesson page's highlight.js github theme, as a Pygments style."""

    background_color = PANEL
    styles = {  # noqa: RUF012
        Token: FG,
        Comment: "italic #59636e",
        Keyword: "#cf222e",
        Name.Function: "#8250df",
        Name.Class: "#8250df",
        Name.Builtin: "#953800",
        Name.Decorator: "#8250df",
        String: "#0a3069",
        Number: "#0550ae",
        Operator: FG,
    }


def fit(mobject, width=BODY_WIDTH, height=BODY_HEIGHT):  # noqa: ANN001, ANN201
    """Shrink `mobject` to fit the body area, never grow it."""
    if mobject.width > width:
        mobject.scale_to_fit_width(width)
    if mobject.height > height:
        mobject.scale_to_fit_height(height)
    return mobject


def centred(mobject):  # noqa: ANN001, ANN201
    """Place `mobject` in the middle of the body area."""
    return mobject.move_to([0, BODY_TOP - BODY_HEIGHT / 2, 0])


def header(title):  # noqa: ANN001, ANN201
    """Draw the chapter title, top left, over a short accent rule."""
    text = Text(title, font=SANS, font_size=34, color=MUTED, weight="BOLD")
    fit(text, width=BODY_WIDTH)
    text.to_corner(UL, buff=0.55)
    rule = Line(LEFT, RIGHT, color=ACCENT, stroke_width=6).set_width(1.2)
    rule.next_to(text, DOWN, buff=0.18, aligned_edge=LEFT)
    return VGroup(text, rule)


def label(lines, size, alignment="left"):  # noqa: ANN001, ANN201
    """Set `lines` so every line has the same height, descenders or not."""
    paragraph = Paragraph(
        *(f"|{line}|" for line in lines),
        font=SANS,
        font_size=size,
        color=FG,
        alignment=alignment,
        line_spacing=0.9,
        disable_ligatures=True,
    )
    for line in paragraph.chars:
        line[0].set_opacity(0)
        line[-1].set_opacity(0)
    return paragraph


def text_frame(frame):  # noqa: ANN001, ANN201
    """One short statement, large and centred."""
    return centred(fit(label(frame["lines"], 76, "center")))


def list_frame(frame):  # noqa: ANN001, ANN201
    """Items left-aligned under each other, each with a number or a dot."""
    rows = []
    for number, item in enumerate(frame["items"], start=1):
        if frame["numbered"]:
            prefix = f"{number}. "
            text = label([prefix + item[0], *item[1:]], 56)
            text.chars[0][1 : len(prefix) + 1].set_color(ACCENT)
            rows.append(text)
        else:
            text = label(item, 56)
            mark = Dot(radius=0.12, color=ACCENT)
            mark.next_to(text.chars[0], LEFT, buff=0.4)
            rows.append(VGroup(mark, text))
    body = VGroup(*rows).arrange(DOWN, buff=0.6, aligned_edge=LEFT)
    return centred(fit(body))


def flow_frame(frame):  # noqa: ANN001, ANN201
    """Boxes of one height joined by arrows, left to right."""
    labels = [label(node, 52, "center") for node in frame["nodes"]]
    height = max(text.height for text in labels) + 0.8
    boxes = []
    for text in labels:
        box = RoundedRectangle(
            corner_radius=0.2,
            width=max(text.width + 0.9, 2.6),
            height=height,
            stroke_color=ACCENT,
            stroke_width=5,
            fill_color=PANEL,
            fill_opacity=1,
        )
        boxes.append(VGroup(box, text.move_to(box)))
    VGroup(*boxes).arrange(RIGHT, buff=1.2)
    arrows = [
        Arrow(
            left.get_right(),
            right.get_left(),
            buff=0.12,
            color=MUTED,
            stroke_width=8,
            max_tip_length_to_length_ratio=0.4,
            max_stroke_width_to_length_ratio=8,
        )
        for left, right in pairwise(boxes)
    ]
    parts = [boxes[0]]
    for arrow, box in zip(arrows, boxes[1:], strict=True):
        parts += [arrow, box]
    return centred(fit(VGroup(*parts)))


def code_frame(frame):  # noqa: ANN001, ANN201
    """Highlighted code, left-aligned against the body's left edge."""
    body = Code(
        code_string=frame["code"],
        language=frame.get("language") or "python",
        formatter_style=GithubLight,
        add_line_numbers=False,
        background="rectangle",
        background_config={
            "fill_color": PANEL,
            "stroke_color": BORDER,
            "stroke_width": 2,
            "buff": 0.45,
        },
        paragraph_config={"font": MONO, "font_size": 52},
    )
    centred(fit(body)).align_to([BODY_LEFT, 0, 0], LEFT)
    return body


DRAW = {"text": text_frame, "list": list_frame, "flow": flow_frame, "code": code_frame}


class Chapter(Scene):
    """Each beat replaces the body under a fixed chapter header."""

    def construct(self):  # noqa: ANN201, D102
        self.add(header(CHAPTER["title"]))
        shown = None
        for beat in CHAPTER["beats"]:
            body = DRAW[beat["frame"]["kind"]](beat["frame"])
            self.add_sound(beat["wav"])
            entering = (
                LaggedStart(*(FadeIn(part) for part in body), lag_ratio=0.35)
                if beat["frame"]["kind"] in {"flow", "list"}
                else FadeIn(body)
            )
            if shown is not None:
                self.play(FadeOut(shown), run_time=TRANSITION)
            self.play(entering, run_time=TRANSITION * (1 if shown is not None else 2))
            self.wait(max(beat["seconds"] - TRANSITION * 2, 0.05))
            shown = body
