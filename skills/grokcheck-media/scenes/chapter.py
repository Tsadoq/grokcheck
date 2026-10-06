"""The Manim scene for one concept video chapter, read from `beats.json` beside it."""  # noqa: INP001

import json
import math
from itertools import pairwise
from pathlib import Path

import numpy as np
from manim import (
    DOWN,
    LEFT,
    PI,
    RIGHT,
    UL,
    UP,
    AnimationGroup,
    ArcBetweenPoints,
    Arrow,
    BackgroundRectangle,
    Code,
    Create,
    DashedLine,
    Dot,
    FadeIn,
    FadeOut,
    LaggedStart,
    Line,
    MoveAlongPath,
    MoveToTarget,
    Paragraph,
    Polygon,
    RoundedRectangle,
    Scene,
    Text,
    Triangle,
    VGroup,
    VMobject,
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
FG = "#1b1e2b"
MUTED = "#5b6075"
BORDER = "#d5d8e2"
PANEL = "#e9ebf1"
ACCENT = "#3346d3"
ACCENT_SOFT = "#e3e6fb"
DIM = 0.22
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
    """The lesson page's code colours from style.css, as a Pygments style."""

    background_color = PANEL
    styles = {  # noqa: RUF012
        Token: FG,
        Comment: "italic #7b8094",
        Keyword: "#7a3fb8",
        Name.Function: "#2350b8",
        Name.Class: "#2350b8",
        Name.Builtin: "#a3480f",
        Name.Decorator: "#2350b8",
        String: "#19744a",
        Number: "#a3480f",
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

SCENE_LEFT = -6.25
SCENE_RIGHT = 6.25
SCENE_TOP = 2.35
SCENE_BOTTOM = -2.85
PANEL_LEFT = -0.75
GROW = 2.4
CAPTION_Y = -3.45
NODE_SIZE = 32
EDGE_SIZE = 26
BOX_GAP = 0.45
LAYER_GAP = 1.0
CHAR = 0.0068
BUS = 0.35
ROW = 1.0
FRAME = 1 / 30


def node_box(lines, *, decision=False):  # noqa: ANN001, ANN201
    """Draw a labelled box, or a diamond for a decision, in the normal style."""
    text = Paragraph(
        *lines, font=SANS, font_size=NODE_SIZE, color=FG, alignment="center"
    )
    if decision:
        w, h = text.width + 1.6, text.height + 0.9
        shape = Polygon([0, h / 2, 0], [w / 2, 0, 0], [0, -h / 2, 0], [-w / 2, 0, 0])
    else:
        shape = RoundedRectangle(
            corner_radius=0.14,
            width=max(text.width + 0.6, 1.6),
            height=text.height + 0.5,
        )
    shape.set_stroke(MUTED, 3).set_fill(BG, 1)
    return VGroup(shape, text.move_to(shape))


def link(points, lines, *, arc=None):  # noqa: ANN001, ANN201
    """Draw a path through `points` (or an arc), its arrow tip and its label."""
    if arc is None:
        line = VMobject().set_points_as_corners([np.array(p) for p in points])
    else:
        line = ArcBetweenPoints(np.array(points[0]), np.array(points[-1]), angle=arc)
    line.set_stroke(MUTED, 3.5)
    end = line.point_from_proportion(1)
    before = line.point_from_proportion(0.98)
    tip = Triangle().set_stroke(width=0).set_fill(MUTED, 1).scale(0.13)
    angle = math.atan2(*(end - before)[[1, 0]])
    tip.rotate(angle - PI / 2).move_to(
        end - (end - before) / np.linalg.norm(end - before) * 0.1
    )
    parts = [line, tip]
    if lines:
        text = Paragraph(
            *lines, font=SANS, font_size=EDGE_SIZE, color=MUTED, alignment="center"
        )
        parts += [BackgroundRectangle(text, color=BG, fill_opacity=1, buff=0.07), text]
    return VGroup(*parts)


def flow(scene):  # noqa: ANN001, ANN201
    """Every node and edge of a flow scene, placed on its layered grid."""
    boxes = {
        n["id"]: node_box(n["label"], decision=n["decision"]) for n in scene["nodes"]
    }
    tall = max(box.height for box in boxes.values())
    wide = max(box.width for box in boxes.values())
    labels = max((len(e["label"]) for e in scene["edges"]), default=0)
    if scene["direction"] == "TD":
        across_gap = td_unit(scene, boxes)
        down_gap = tall + LAYER_GAP + 0.32 * max(labels - 1, 0)
        place = {
            i: [c * across_gap, -r * down_gap, 0]
            for i, (c, r) in scene["positions"].items()
        }
    else:
        right_gap = wide + LAYER_GAP + 1.6
        place = {
            i: [c * right_gap, -r * (tall + 0.6), 0]
            for i, (c, r) in scene["positions"].items()
        }
    for ident, box in boxes.items():
        box.move_to(place[ident])
    parts = dict(boxes)
    for edge in scene["edges"]:
        start, end = boxes[edge["from"]], boxes[edge["to"]]
        if edge["id"] in scene["back"]:
            parts[edge["id"]] = loop(start, end, edge["label"], scene["direction"])
            continue
        if scene["direction"] == "TD":
            a, b = start.get_bottom(), end.get_top()
            bus = a[1] - BUS
            points = [a, [a[0], bus, 0], [b[0], bus, 0], b]
            label_at = [b[0], (bus + b[1]) / 2, 0]
        else:
            a, b = start.get_right(), end.get_left()
            mid = (a[0] + b[0]) / 2
            points = [a, [mid - 0.4, a[1], 0], [mid - 0.4, b[1], 0], b]
            label_at = [(mid - 0.4 + b[0]) / 2, b[1] + 0.3, 0]
        drawn = link(points, edge["label"])
        if len(drawn) > 2:  # noqa: PLR2004
            tag = VGroup(*drawn[2:])
            if scene["direction"] == "TD":
                label_at[1] = bus - 0.12 - tag.height / 2
            tag.move_to(label_at)
        parts[edge["id"]] = drawn
    return parts


def td_unit(scene, boxes):  # noqa: ANN001, ANN201
    """Return the narrowest column width that keeps neighbours and labels apart."""
    room = {i: box.width for i, box in boxes.items()}
    for edge in scene["edges"]:
        if edge["id"] not in scene["back"]:
            longest = max((len(line) for line in edge["label"]), default=0)
            room[edge["to"]] = max(room[edge["to"]], longest * EDGE_SIZE * CHAR)
    rows = {}
    for ident, (column, row) in scene["positions"].items():
        rows.setdefault(row, []).append((column, ident))
    unit = max(room.values()) / 2 + BOX_GAP
    for row in rows.values():
        ordered = sorted(row)
        for (a, left), (b, right) in pairwise(ordered):
            unit = max(unit, ((room[left] + room[right]) / 2 + BOX_GAP) / (b - a))
    return unit


def loop(start, end, lines, direction):  # noqa: ANN001, ANN201
    """Draw an edge that closes a loop, curving out to the right of its boxes."""
    if start is end:
        side = start.get_right() if direction == "TD" else start.get_bottom()
        offset = UP * 0.28 if direction == "TD" else RIGHT * 0.28
        drawn = link([side + offset, side - offset], lines, arc=-1.5 * PI)
        if len(drawn) > 2:  # noqa: PLR2004
            VGroup(*drawn[2:]).next_to(drawn[0], DOWN, buff=0.08, aligned_edge=LEFT)
        return drawn
    drawn = link([start.get_right(), end.get_right()], lines, arc=PI / 2)
    if len(drawn) > 2:  # noqa: PLR2004
        VGroup(*drawn[2:]).next_to(drawn[0], RIGHT, buff=0.12)
    return drawn


def sequence(scene):  # noqa: ANN001, ANN201
    """Actors in columns with lifelines, messages in rows below them."""
    boxes = {a["id"]: node_box(a["label"]) for a in scene["actors"]}
    gap = max(box.width for box in boxes.values()) + 1.6
    rows = len(scene["messages"])
    parts = {}
    for ident, box in boxes.items():
        box.move_to([scene["positions"][ident][0] * gap, 0, 0])
        life = DashedLine(
            box.get_bottom(), box.get_bottom() + DOWN * (rows + 0.6) * ROW
        )
        life.set_stroke(BORDER, 3)
        parts[ident] = VGroup(box[0], box[1], life)
    for message in scene["messages"]:
        y = boxes[message["from"]].get_bottom()[1] - scene["rows"][message["id"]] * ROW
        a = boxes[message["from"]].get_x()
        b = boxes[message["to"]].get_x()
        if a == b:
            drawn = link(
                [[a, y + 0.2, 0], [a, y - 0.2, 0]], message["label"], arc=-1.6 * PI
            )
        else:
            shift = 0.08 if b > a else -0.08
            drawn = link([[a + shift, y, 0], [b - shift, y, 0]], message["label"])
        if len(drawn) > 2:  # noqa: PLR2004
            VGroup(*drawn[2:]).next_to(drawn[0], UP, buff=0.06)
        parts[message["id"]] = drawn
    return parts


def state(scene):  # noqa: ANN001, ANN201
    """Items in one row, ready for pointers underneath."""
    boxes = {i["id"]: node_box(i["label"]) for i in scene["items"]}
    wide = max(box.width for box in boxes.values())
    for ident, box in boxes.items():
        box[0].stretch_to_fit_width(wide)
        box.move_to([scene["positions"][ident][0] * (wide + 0.25), 0, 0])
    return boxes


def pointer(name, box, slot, zoom):  # noqa: ANN001, ANN201
    """Draw a named arrow up at `box`, `slot` widths aside, sized by `zoom`."""
    x = box.get_x() + slot * 1.3 * zoom
    top = box.get_bottom()[1] - 0.05 * zoom
    arrow = Arrow(
        [x, top - 0.8 * zoom, 0],
        [x, top, 0],
        buff=0,
        color=ACCENT,
        stroke_width=6,
        max_tip_length_to_length_ratio=0.3,
    )
    text = Text(name, font=MONO, font_size=24, color=ACCENT).scale(zoom)
    return VGroup(arrow, text.next_to(arrow, DOWN, buff=0.08 * zoom))


def paint(part, kind, mode):  # noqa: ANN001, ANN201
    """Style a box or a link as normal, bright or dim, in place."""
    opacity = DIM if mode == "dim" else 1
    color = ACCENT if mode == "bright" else MUTED
    if kind == "box":
        part[0].set_stroke(color, 5 if mode == "bright" else 3, opacity=opacity)
        part[0].set_fill(ACCENT_SOFT if mode == "bright" else BG, opacity=opacity)
        part[1].set_opacity(opacity)
        if len(part) > 2:  # noqa: PLR2004
            part[2].set_stroke(opacity=opacity)
    else:
        part[0].set_stroke(color, 6 if mode == "bright" else 3.5, opacity=opacity)
        part[1].set_fill(color, opacity=opacity)
        if len(part) > 2:  # noqa: PLR2004
            part[3].set_color(ACCENT if mode == "bright" else MUTED).set_opacity(
                opacity
            )
    return part


def fitting(mobjects, right, cap):  # noqa: ANN001, ANN201
    """Return the scale, centre and target that fit `mobjects` left of `right`."""
    group = VGroup(*mobjects)
    width, height = right - SCENE_LEFT, SCENE_TOP - SCENE_BOTTOM
    scale = min(cap, width / group.width, height / group.height)
    target = np.array([(SCENE_LEFT + right) / 2, (SCENE_TOP + SCENE_BOTTOM) / 2, 0])
    return scale, group.get_center(), target


def moved(mobject, layout):  # noqa: ANN001, ANN201
    """Scale and shift `mobject` in place by `layout`, if there is one."""
    if layout is None:
        return mobject
    scale, centre, target = layout
    return mobject.scale(scale, about_point=centre).shift(target - centre)


class Chapter(Scene):
    """Each beat replaces the body, or changes the one scene, under a fixed header."""

    def construct(self):  # noqa: ANN201, D102
        self.add(header(CHAPTER["title"]))
        if CHAPTER.get("scene"):
            self.scened(CHAPTER["scene"])
            return
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

    def scened(self, scene):  # noqa: ANN001, ANN201
        """Keep one scene on screen and animate what each beat changes."""
        build = {"flow": flow, "sequence": sequence, "state": state}[scene["kind"]]
        self.parts = build(scene)
        links = {e["id"] for e in scene.get("edges", []) + scene.get("messages", [])}
        self.kinds = {i: "link" if i in links else "box" for i in self.parts}
        room = Dot().set_opacity(0)
        if scene["kind"] == "state":
            room.move_to(VGroup(*self.parts.values()).get_bottom() + DOWN * 1.4)
        self.world = [*self.parts.values(), room]
        layout = fitting(self.world, SCENE_RIGHT, GROW)
        for mobject in self.world:
            moved(mobject, layout)
        self.zoom, self.narrow = layout[0], False
        self.modes = dict.fromkeys(self.parts, "normal")
        self.shown = set()
        self.pointers, self.extras = {}, []
        for beat in CHAPTER["beats"]:
            step = beat["step"]
            self.add_sound(beat["wav"])
            self.timed(self.settle(beat), step["timing"]["settle"])
            adding = [enter(self.parts[i], self.kinds[i]) for i in step["add"]]
            lagged = [LaggedStart(*adding, lag_ratio=0.6)] if adding else []
            self.timed(lagged, step["timing"]["add"])
            self.shown |= set(step["add"])
            self.travel(step)
            self.wait(max(step["timing"]["hold"], FRAME))

    def settle(self, beat):  # noqa: ANN001, ANN201
        """Return the animations that clear the last beat and restyle for this one."""
        step = beat["step"]
        settle = [FadeOut(extra) for extra in self.extras]
        self.extras = []
        panel = code_panel(beat["code"]) if beat["code"] else None
        relayout = self.relayout(beat, panel)
        self.extras += [panel] if panel else []
        if beat["caption"]:
            caption = Paragraph(
                *beat["caption"], font=SANS, font_size=30, color=FG, alignment="center"
            )
            self.extras.append(fit(caption).move_to([0, CAPTION_Y, 0]))
        settle += [FadeIn(extra) for extra in self.extras]
        wanted = {
            i: "bright"
            if i in step["bright"]
            else "dim"
            if i in step["dim"]
            else "normal"
            for i in self.parts
        }
        for ident, part in self.parts.items():
            if ident not in self.shown:
                moved(paint(part, self.kinds[ident], wanted[ident]), relayout)
            elif relayout or wanted[ident] != self.modes[ident]:
                part.generate_target()
                paint(part.target, self.kinds[ident], wanted[ident])
                moved(part.target, relayout)
                settle.append(MoveToTarget(part))
        moved(self.world[-1], relayout)
        for drawn in self.pointers.values() if relayout else ():
            moved(drawn.generate_target(), relayout)
            settle.append(MoveToTarget(drawn))
        self.modes = wanted
        return settle

    def relayout(self, beat, panel):  # noqa: ANN001, ANN201
        """Return the layout change a code panel opening or closing needs."""
        if bool(beat["code"]) == self.narrow:
            return None
        self.narrow = bool(beat["code"])
        if self.narrow:
            on = [
                part
                for ident, part in self.parts.items()
                if ident in self.shown or ident in beat["step"]["add"]
            ]
            change = fitting(
                [*on, *self.pointers.values()],
                panel.get_left()[0] - 0.4,
                GROW / self.zoom,
            )
        else:
            everything = [*self.world, *self.pointers.values()]
            change = fitting(everything, SCENE_RIGHT, GROW / self.zoom)
        self.zoom *= change[0]
        return change

    def travel(self, step):  # noqa: ANN001, ANN201
        """Move this beat's pointers, or run a marker along its path."""
        shifting = []
        for name in step["moved"]:
            item, slot = step["pointers"][name]
            drawn = pointer(name, self.parts[item], slot, self.zoom)
            if name in self.pointers:
                self.pointers[name].generate_target()
                self.pointers[name].target.become(drawn)
                shifting.append(MoveToTarget(self.pointers[name]))
            else:
                self.pointers[name] = drawn
                shifting.append(FadeIn(drawn, shift=UP * 0.2))
        if shifting:
            self.timed(shifting, step["timing"]["move"])
        if step["path"]:
            marker = Dot(radius=0.14, color=ACCENT).set_stroke(BG, 3)
            marker.move_to(self.parts[step["path"][0]][0].point_from_proportion(0))
            self.add(marker)
            each = max(step["timing"]["move"] / len(step["path"]), FRAME)
            for ident in step["path"]:
                self.play(MoveAlongPath(marker, self.parts[ident][0]), run_time=each)
            self.extras.append(marker)

    def timed(self, animations, seconds):  # noqa: ANN001, ANN201
        """Play `animations` over `seconds`, or wait when there are none."""
        if animations:
            self.play(AnimationGroup(*animations), run_time=max(seconds, FRAME))
        elif seconds > 0:
            self.wait(max(seconds, FRAME))


def enter(part, kind):  # noqa: ANN001, ANN201
    """Draw a link's path, then its tip and label; fade a box in."""
    if kind == "box":
        return FadeIn(part, scale=0.92)
    return AnimationGroup(Create(part[0]), FadeIn(VGroup(*part[1:])), lag_ratio=0.5)


def code_panel(frame):  # noqa: ANN001, ANN201
    """At most five lines of code, in a panel right of the scene."""
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
            "buff": 0.35,
        },
        paragraph_config={"font": MONO, "font_size": 34},
    )
    fit(body, width=SCENE_RIGHT - PANEL_LEFT, height=SCENE_TOP - SCENE_BOTTOM)
    body.move_to([0, (SCENE_TOP + SCENE_BOTTOM) / 2, 0])
    return body.align_to([SCENE_RIGHT, 0, 0], RIGHT)
