"""Static KPI-wizard interview script — the A2 KPI interview, as data.

This is the single source of truth for the wizard the frontend renders:
``GET /api/v1/management/kpi-wizard/questions`` serves this constant
verbatim, and the ``kpi_draft`` job composer joins the user's answers
back against these ids. The questions are deliberately **data-free**
(no user or deployment data) so the script itself is shareable,
reviewable work product — same transparency posture as the prompt
templates in ``prompts/``.

Sections mirror the A2 interview structure:

* **A — Objective.** What "legal succeeds" means this year, in the
  business's words; last year's complaints/praise; bottleneck vs
  enabler.
* **B — Candidate metrics.** One question per metric family
  (efficiency / cost / value / enablement / risk), each pushing
  outcome-over-activity.
* **C — Vanity check.** The three numbers you'd show the CEO — the
  kill-vanity-metrics forcing function.
* **D — Baselines & targets.** What's measured today, realistic vs
  stretch targets, ownership, cadence.

Shape per question: ``{id, section, prompt, hint, optional}``. Ids are
stable — the frontend submits ``[{question_id, answer}]`` keyed on
them, and renames here would orphan in-flight wizard sessions.
"""

from __future__ import annotations

from typing import TypedDict


class KpiQuestion(TypedDict):
    """One wizard question — static, data-free."""

    id: str
    section: str
    prompt: str
    hint: str
    optional: bool


QUESTIONS: tuple[KpiQuestion, ...] = (
    # --- Section A: objective -------------------------------------------------
    {
        "id": "a1_success_sentence",
        "section": "A",
        "prompt": (
            'Finish the sentence: "This year, legal succeeds if the business can say we ___."'
        ),
        "hint": (
            "Answer in the business's words, not legal's. If the CEO repeated "
            "your sentence at an all-hands, would it land?"
        ),
        "optional": False,
    },
    {
        "id": "a2_complaints_praise",
        "section": "A",
        "prompt": (
            "What did internal clients complain about most regarding legal "
            "last year? What did they praise?"
        ),
        "hint": (
            "Be specific: which teams, which kinds of requests, which moments. "
            "Complaints point at metrics worth moving; praise points at value "
            "worth proving with a number."
        ),
        "optional": False,
    },
    {
        "id": "a3_bottleneck_enabler",
        "section": "A",
        "prompt": (
            "Where is legal seen as a bottleneck today, and where is it already seen as an enabler?"
        ),
        "hint": (
            "Name the workflows (contract review, approvals, hiring, product "
            "launches...). A KPI that turns a named bottleneck into an enabler "
            "is the strongest kind."
        ),
        "optional": False,
    },
    # --- Section B: candidate metrics per family -----------------------------
    {
        "id": "b1_efficiency",
        "section": "B",
        "prompt": (
            "Efficiency: where does speed matter most to the business — and "
            "what outcome (not activity) would prove legal is fast enough?"
        ),
        "hint": (
            "Outcome over activity: 'median NDA turnaround in days' beats "
            "'number of NDAs reviewed'. What would the business notice "
            "improving?"
        ),
        "optional": False,
    },
    {
        "id": "b2_cost",
        "section": "B",
        "prompt": (
            "Cost: what does the department spend (outside counsel, tools, "
            "settlements), and which cost outcome is worth managing to a "
            "number?"
        ),
        "hint": (
            "Outcome over activity: 'outside counsel spend vs budget' or "
            "'spend per matter type' beats 'invoices processed'. Include "
            "what you'd trade off — cheap can be slow."
        ),
        "optional": False,
    },
    {
        "id": "b3_value",
        "section": "B",
        "prompt": (
            "Value: what value did legal create or protect last year that "
            "nobody measured — and what number would have captured it?"
        ),
        "hint": (
            "Think recovered amounts, avoided liabilities with a defensible "
            "estimate, favorable terms won. If the number needs a paragraph "
            "of caveats, it may belong in the narrative, not the KPI list."
        ),
        "optional": False,
    },
    {
        "id": "b4_enablement",
        "section": "B",
        "prompt": (
            "Enablement: which business motions (sales, partnerships, "
            "product, hiring) depend on legal — and what outcome would show "
            "legal is accelerating them rather than gating them?"
        ),
        "hint": (
            "Outcome over activity: 'deals closed without legal escalation' "
            "or 'sales-cycle days attributable to contracting' beats "
            "'contracts touched'."
        ),
        "optional": False,
    },
    {
        "id": "b5_risk",
        "section": "B",
        "prompt": (
            "Risk: which risk outcomes actually worry you (incidents, "
            "findings, disputes, missed obligations) — and which of them is "
            "measurable without gaming?"
        ),
        "hint": (
            "Outcome over activity: 'open audit findings past due' beats "
            "'trainings delivered'. Beware metrics a team can satisfy "
            "without reducing risk."
        ),
        "optional": False,
    },
    # --- Section C: vanity check ----------------------------------------------
    {
        "id": "c1_three_numbers",
        "section": "C",
        "prompt": (
            "If you could show the CEO only three numbers about legal this "
            "year, which three would you pick — and why those?"
        ),
        "hint": (
            "This is the vanity-metric filter. Anything that wouldn't make "
            "this cut probably shouldn't be a KPI at all."
        ),
        "optional": False,
    },
    {
        "id": "c2_stop_reporting",
        "section": "C",
        "prompt": (
            "Which numbers does the department currently track or report "
            "that you would happily stop reporting tomorrow?"
        ),
        "hint": (
            "Naming the vanity metrics explicitly helps the draft record "
            "what was deliberately NOT chosen, and why."
        ),
        "optional": True,
    },
    # --- Section D: baselines, targets, ownership, cadence --------------------
    {
        "id": "d1_baselines",
        "section": "D",
        "prompt": (
            "For the metrics you're leaning toward: what do you actually "
            "know about today's numbers? Give any baselines you have, even "
            "rough ones."
        ),
        "hint": (
            "Say 'unknown' where you don't have one — the draft will leave "
            "the baseline empty rather than invent it."
        ),
        "optional": False,
    },
    {
        "id": "d2_targets",
        "section": "D",
        "prompt": (
            "Where would you set targets — a realistic year-one number and, "
            "separately, a stretch number?"
        ),
        "hint": (
            "A realistic target you hit builds credibility; a stretch target "
            "you miss with a good narrative can still be the right call. "
            "Distinguish them."
        ),
        "optional": False,
    },
    {
        "id": "d3_ownership",
        "section": "D",
        "prompt": (
            "Who would own each number day-to-day — you, a named deputy, or "
            "a function (e.g., compliance)?"
        ),
        "hint": (
            "A KPI without an owner drifts. Naming the owner also decides "
            "whether a metric is a department number or an individual one."
        ),
        "optional": True,
    },
    {
        "id": "d4_cadence",
        "section": "D",
        "prompt": (
            "How often should each number be measured and reviewed — "
            "monthly or quarterly — and in what forum?"
        ),
        "hint": (
            "Match cadence to how fast the number can actually move. "
            "Quarterly for slow-moving outcomes; monthly only where the "
            "data is cheap and the number responds within a month."
        ),
        "optional": False,
    },
)
"""The full interview script, in render order."""

QUESTIONS_BY_ID: dict[str, KpiQuestion] = {q["id"]: q for q in QUESTIONS}
"""Lookup used by the ``kpi_draft`` composer to join answers to prompts."""
