"""Management-tab AI features — background draft jobs for the Management tab.

Three job types (PRD Management-tab plan, "draft-then-confirm" posture):

* ``pre_meeting_brief`` — stakeholder briefing from the dossier + docs.
* ``review_prep``       — 1-on-1 review prep from KPI series + roster.
* ``kpi_draft``         — KPI catalog draft from the A2 wizard answers.

Submodules:

* :mod:`app.management_ai.kpi_questions` — the static, data-free wizard
  question script (single source of truth; served to the frontend).
* :mod:`app.management_ai.runner` — context composer + gateway runner,
  launched via FastAPI ``BackgroundTasks`` exactly like the playbook
  executor.
* ``prompts/`` — data-free, attorney-reviewable prompt templates
  (transparency: the prompt is visible work product, the composer only
  fills ``{placeholders}``).
"""
