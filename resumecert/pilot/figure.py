"""Small dependency-free vector plot generated from recorded aggregates."""


def coverage_svg(summary):
    grid = summary["primary"]
    budgets = tuple(map(int, grid))
    n = summary["design"]["matched_cases"]
    left, top, width, height = 77, 96, 548, 172

    def x(b):
        return left + width * b / max(budgets)

    def y(c):
        return top + height * (1 - c / n)

    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="780" height="364" viewBox="0 0 780 364" '
        'role="img" aria-labelledby="title desc">',
        '<title id="title">ResumeCert development pilot coverage</title>',
        '<desc id="desc">Fixed, greedy adaptive, and exact prior tree coverage at budgets 0–4. '
        'Dashed line shows the per-case hindsight ceiling, not a policy.</desc>',
        '<rect x="0" y="0" width="780" height="364" rx="14" fill="#fff"/>',
        '<g font-family="system-ui, sans-serif" fill="#142d40">',
        '<text x="29" y="32" font-size="20" font-weight="700">When do two reads suffice?</text>',
        '<text x="29" y="55" font-size="13">Synthetic development pilot · 24 matched pairs · 48 cases</text>',
        '<text x="29" y="80" font-size="12" font-weight="600">Certified cases</text>',
    ]
    for count in range(0, n + 1, n // 4):
        out.append(f'<path d="M{left} {y(count):.1f} H{left+width}" stroke="#d9e3eb"/>')
        out.append(f'<text x="{left-13}" y="{y(count)+4:.1f}" text-anchor="end" font-size="11">{count}</text>')
    for b in budgets:
        out.append(f'<text x="{x(b):.1f}" y="287" text-anchor="middle" font-size="11">{b}</text>')
    out.append('<text x="351" y="307" text-anchor="middle" font-size="12">Unit read budget per case</text>')
    series = (
        ("hindsight", "Per-case ceiling", "#596878", ' stroke-dasharray="6 4"'),
        ("fixed", "Optimized fixed", "#1577a3", ""),
        ("adaptive", "Greedy adaptive", "#a75815", ""),
        ("optimal_tree", "Exact prior tree", "#176449", ""),
    )
    for i, (key, label, color, dash) in enumerate(series):
        values = []
        for b in budgets:
            row = grid[str(b)]["fixed" if key == "hindsight" else key]
            values.append(row["within_budget_witness"] if key == "hindsight"
                          else row["certified_allow"] + row["certified_block"])
        points = " ".join(f"{x(b):.1f},{y(c):.1f}" for b, c in zip(budgets, values))
        out.append(f'<polyline points="{points}" stroke="{color}" stroke-width="2.7" fill="none"{dash}/>')
        if not dash:
            for b, c in zip(budgets, values):
                out.append(f'<circle cx="{x(b):.1f}" cy="{y(c):.1f}" r="{4.5-i/2}" '
                           f'fill="{color}" stroke="#fff" stroke-width="1"/>')
        lx = 36 + i * 180
        out.append(f'<path d="M{lx} 336 h21" stroke="{color}" stroke-width="3"{dash}/>')
        out.append(f'<text x="{lx+27}" y="340" font-size="12">{label}</text>')
    return "\n".join(out + ["</g>", "</svg>", ""])
