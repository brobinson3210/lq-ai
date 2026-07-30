"""stakeholder_health_traffic_light — 3-level overall_health

Replaces the 4-level ``stakeholders.overall_health`` scale
(strong | solid | needs_attention | at_risk) with a 3-level traffic
light (green | yellow | red), per the GC's decision. Data mapping on
upgrade:

    strong          -> green
    solid           -> green
    needs_attention -> yellow
    at_risk         -> red

The downgrade restores the old CHECK and maps back
(green -> solid, yellow -> needs_attention, red -> at_risk). NOTE:
the downgrade is LOSSY for rows that were originally ``strong`` —
the upgrade merges strong and solid into green, so the reverse
mapping cannot distinguish them and lands every green row on
``solid``.

Revision ID: 0070
Revises: 0069
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the old CHECK first so the data rewrite can land the new values.
    op.drop_constraint("chk_stakeholders_health", "stakeholders", type_="check")
    op.execute(
        """
        UPDATE stakeholders
        SET overall_health = CASE overall_health
            WHEN 'strong' THEN 'green'
            WHEN 'solid' THEN 'green'
            WHEN 'needs_attention' THEN 'yellow'
            WHEN 'at_risk' THEN 'red'
            ELSE overall_health
        END
        WHERE overall_health IS NOT NULL
        """
    )
    op.create_check_constraint(
        "chk_stakeholders_health",
        "stakeholders",
        "overall_health IS NULL OR overall_health IN ('green', 'yellow', 'red')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_stakeholders_health", "stakeholders", type_="check")
    # Lossy on purpose: strong and solid were merged into green by the
    # upgrade, so green can only map back to one of them — 'solid' is
    # the conservative choice (never over-states a relationship).
    op.execute(
        """
        UPDATE stakeholders
        SET overall_health = CASE overall_health
            WHEN 'green' THEN 'solid'
            WHEN 'yellow' THEN 'needs_attention'
            WHEN 'red' THEN 'at_risk'
            ELSE overall_health
        END
        WHERE overall_health IS NOT NULL
        """
    )
    op.create_check_constraint(
        "chk_stakeholders_health",
        "stakeholders",
        "overall_health IS NULL OR overall_health IN "
        "('strong', 'solid', 'needs_attention', 'at_risk')",
    )
