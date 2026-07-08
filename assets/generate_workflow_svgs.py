#!/usr/bin/env python3
"""Generate light/dark workflow + architecture SVGs for the root README."""
from pathlib import Path

OUT = Path(__file__).resolve().parent
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

LIGHT = dict(
    node_fill="#ffffff", node_stroke="#d0d7de", title="#1f2328", sub="#59636e",
    purple_fill="#f1eafe", purple_stroke="#c9b3f5", purple_title="#5b3fbc", purple_sub="#8a76c9",
    green_fill="#e9f7ef", green_stroke="#8fd6ac", green_title="#1a7f4b", green_sub="#549f77",
    entry_fill="#f6f8fa", entry_stroke="#d0d7de", entry_title="#59636e", entry_sub="#818b95",
    arrow="#6e7781", ret="#8b949e", label="#59636e", legend="#59636e",
    zone_fill="#f6f8fa", zone_stroke="#d0d7de", zone_label="#59636e",
    zone2_fill="#f0f6fe", zone2_stroke="#bcd3f0", zone2_label="#3b6bab",
    box_fill="#ffffff",
)
DARK = dict(
    node_fill="#161b22", node_stroke="#3d444d", title="#e6edf3", sub="#9198a1",
    purple_fill="#221a38", purple_stroke="#6e40c9", purple_title="#c3aaf9", purple_sub="#9d89d8",
    green_fill="#122b1d", green_stroke="#2ea043", green_title="#72dd9d", green_sub="#5aa878",
    entry_fill="#0d1117", entry_stroke="#3d444d", entry_title="#9198a1", entry_sub="#6e7781",
    arrow="#8b949e", ret="#8b949e", label="#9198a1", legend="#9198a1",
    zone_fill="#12161d", zone_stroke="#3d444d", zone_label="#9198a1",
    zone2_fill="#0f1a2b", zone2_stroke="#2d4468", zone2_label="#79a5dd",
    box_fill="#1b2129",
)

W = 992
NODE_W, NODE_H = 160, 64
XS = [24, 220, 416, 612, 808]          # left edges; centers +80

LEGEND = "solid arrow = forward · dashed = review sends work back · purple = adversarial review"

PLAIN_KEYS = {"fill": "node_fill", "stroke": "node_stroke", "title": "title", "sub": "sub"}


def box(p, x, y, w, h, title, sub, kind="plain", fill=None):
    key = lambda part: p[PLAIN_KEYS[part] if kind == "plain" else f"{kind}_{part}"]
    dash = ' stroke-dasharray="5 4"' if kind == "entry" else ""
    cx = x + w // 2
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" '
        f'fill="{fill or key("fill")}" stroke="{key("stroke")}" stroke-width="1.5"{dash}/>'
        f'<text x="{cx}" y="{y + 28}" text-anchor="middle" font-size="14" '
        f'font-weight="600" fill="{key("title")}">{title}</text>'
        f'<text x="{cx}" y="{y + 47}" text-anchor="middle" font-size="11" '
        f'fill="{key("sub")}">{sub}</text>'
    )


def node(p, x, title, sub, kind="plain", y=64):
    return box(p, x, y, NODE_W, NODE_H, title, sub, kind)


def fwd_arrow(p, x1, x2, y):
    return (f'<line x1="{x1 + 2}" y1="{y}" x2="{x2 - 3}" y2="{y}" '
            f'stroke="{p["arrow"]}" stroke-width="1.5" marker-end="url(#fwd)"/>')


def ret_arc(p, x_from, x_to, y_from, depth, label, label_x, label_y):
    return (
        f'<path d="M {x_from} {y_from} C {x_from} {depth}, {x_to} {depth}, {x_to} {y_from + 8}" '
        f'fill="none" stroke="{p["ret"]}" stroke-width="1.5" stroke-dasharray="5 4" '
        f'marker-end="url(#ret)"/>'
        f'<text x="{label_x}" y="{label_y}" text-anchor="middle" font-size="11" '
        f'fill="{p["label"]}">{label}</text>'
    )


def link(p, path, label, label_x, label_y, anchor="middle"):
    """Non-workflow connector: a thin line with a small label."""
    return (
        f'<path d="{path}" fill="none" stroke="{p["arrow"]}" stroke-width="1.5" '
        f'marker-end="url(#fwd)"/>'
        f'<text x="{label_x}" y="{label_y}" text-anchor="{anchor}" font-size="11" '
        f'fill="{p["label"]}">{label}</text>'
    )


def zone(p, x, y, w, h, label, alt=False):
    k = "zone2" if alt else "zone"
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" '
        f'fill="{p[f"{k}_fill"]}" stroke="{p[f"{k}_stroke"]}" stroke-width="1.5"/>'
        f'<text x="{x + 20}" y="{y + 27}" font-size="11" font-weight="600" '
        f'letter-spacing="1.5" fill="{p[f"{k}_label"]}">{label}</text>'
    )


def svg(p, body, aria, h, legend=True):
    head = f'<text x="24" y="32" font-size="12" fill="{p["legend"]}">{LEGEND}</text>' if legend else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" '
        f'font-family="{FONT}" role="img" aria-label="{aria}">'
        f'<defs>'
        f'<marker id="fwd" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p["arrow"]}"/></marker>'
        f'<marker id="ret" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p["ret"]}"/></marker>'
        f'</defs>{head}{body}</svg>\n'
    )


def experiment(p):
    parts = [
        node(p, XS[0], "Plan", "write the experiment plan"),
        node(p, XS[1], "Design review", "the plan must pass", "purple"),
        node(p, XS[2], "Execute", "run locally or in a sandbox"),
        node(p, XS[3], "Results review", "the report must pass", "purple"),
        node(p, XS[4], "Complete", "findings recorded", "green"),
    ]
    for i in range(4):
        parts.append(fwd_arrow(p, XS[i] + NODE_W, XS[i + 1], 96))
    parts.append(ret_arc(p, 300, 104, 128, 190, "revise the plan", 202, 194))
    parts.append(ret_arc(p, 680, 496, 128, 190, "fix run or report", 588, 194))
    parts.append(ret_arc(p, 704, 80, 128, 250, "experiment proved faulty", 392, 236))
    return svg(p, "".join(parts),
               "Experiment workflow: plan, design review, execute, results review, "
               "complete; rejected reviews send work back to execution or planning", 256)


LENSES = [
    ("Amplify · what worked", True),
    ("Avoid · what failed", True),
    ("Entropy · weird bets", True),
    ("Agent-chosen lens", False),
    ("Agent-chosen lens", False),
]


def project(p):
    row_y, mid = 108, 140
    parts = [
        node(p, XS[0], "Completed work", "a wave of experiments", "entry", row_y),
        node(p, XS[2], "Synthesis", "report · graph · spec", y=row_y),
        node(p, XS[3], "Reflection review", "adversarial check", "purple", row_y),
        node(p, XS[4], "Publish", "sets up the next wave", "green", row_y),
    ]
    # Lens cluster in column 2: five pills, three core + two designed per project.
    parts.append(f'<text x="300" y="52" text-anchor="middle" font-size="11" '
                 f'fill="{p["label"]}">3 core lenses + 2 designed for this project</text>')
    pill_ys = [59, 93, 127, 161, 195]
    for (text, core), y in zip(LENSES, pill_ys):
        dash = "" if core else ' stroke-dasharray="5 4"'
        tcol, weight = (p["title"], 600) if core else (p["sub"], 400)
        parts.append(
            f'<rect x="220" y="{y}" width="160" height="26" rx="13" '
            f'fill="{p["node_fill"]}" stroke="{p["node_stroke"]}" stroke-width="1.5"{dash}/>'
            f'<text x="300" y="{y + 17}" text-anchor="middle" font-size="11" '
            f'font-weight="{weight}" fill="{tcol}">{text}</text>')
        cy = y + 13
        parts.append(f'<line x1="186" y1="{mid}" x2="214" y2="{cy}" '
                     f'stroke="{p["arrow"]}" stroke-width="1.2" marker-end="url(#fwd)"/>')
        parts.append(f'<line x1="382" y1="{cy}" x2="412" y2="{mid}" '
                     f'stroke="{p["arrow"]}" stroke-width="1.2" marker-end="url(#fwd)"/>')
    for i in (2, 3):
        parts.append(fwd_arrow(p, XS[i] + NODE_W, XS[i + 1], mid))
    parts.append(ret_arc(p, 680, 496, 172, 226, "revise synthesis", 588, 240))
    parts.append(ret_arc(p, 704, 300, 172, 268, "lenses fall short", 502, 262))
    return svg(p, "".join(parts),
               "Project workflow: a completed wave of experiments fans out to five "
               "reflection lenses (Amplify what works, Avoid what failed, Entropy and "
               "weird bets, plus two agent-chosen), then synthesis, adversarial review, "
               "and publish; rejected reviews send work back to synthesis or the lenses", 280)


def system(p):
    bf = p["box_fill"]
    parts = [
        zone(p, 24, 40, 400, 260, "YOUR MACHINE"),
        zone(p, 568, 40, 400, 260, "HOSTED CONTROL PLANE", alt=True),
        box(p, 48, 84, 352, 56, "Agent platform",
            "Claude Code · Codex · Cursor · Gemini CLI · OpenCode", fill=bf),
        box(p, 48, 196, 160, 64, "Research repo", "files stay local", fill=bf),
        box(p, 240, 196, 160, 64, "MCP proxy", "repo files · SSH keys", fill=bf),
        box(p, 592, 196, 352, 64, "Brain — owns all research state",
            "projects · experiments · reviews · reflections", fill=bf),
        box(p, 592, 84, 352, 56, "Frontend UI",
            "humans watch strategy down to execution", fill=bf),
        box(p, 396, 340, 200, 64, "GPU sandboxes", "Lambda · Thunder · Modal", "entry"),
        link(p, "M 320 140 L 320 192", "MCP", 330, 170, "start"),
        link(p, "M 240 228 L 212 228", "reads", 224, 220),
        link(p, "M 400 228 L 588 228", "project ids, never file paths", 496, 220),
        link(p, "M 768 140 L 768 192", "reads", 778, 170, "start"),
        link(p, "M 320 260 C 320 372, 336 372, 390 372", "SSH", 334, 330, "start"),
        link(p, "M 768 260 C 768 372, 706 372, 602 372", "provisions", 726, 330, "start"),
        f'<text x="768" y="286" text-anchor="middle" font-size="11" fill="{p["label"]}">'
        f'hosted by default — or run the brain locally</text>',
    ]
    return svg(p, "".join(parts),
               "System architecture: on your machine, agent platforms talk to the MCP "
               "proxy, which reads the research repo and sends the hosted brain project "
               "ids, never file paths; the brain owns all research state, the frontend "
               "UI reads it, and GPU sandboxes are provisioned by the brain and reached "
               "over SSH", 432, legend=False)


for name, palette in (("light", LIGHT), ("dark", DARK)):
    (OUT / f"experiment-workflow-{name}.svg").write_text(experiment(palette))
    (OUT / f"project-workflow-{name}.svg").write_text(project(palette))
    (OUT / f"system-architecture-{name}.svg").write_text(system(palette))
print("wrote", *sorted(f.name for f in OUT.glob("*.svg")))
