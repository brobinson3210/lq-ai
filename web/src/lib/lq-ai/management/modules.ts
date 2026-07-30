/**
 * Module registry for the /lq-ai/management tab — the GC's department cockpit.
 *
 * Two zones, per the tab's design:
 *  - 'relationships': the constituencies where trust is built person-by-person
 *    (each space is a filtered view over the Stakeholders module).
 *  - 'operations': the pillar modules — the instruments through which a GC
 *    demonstrates the department can be trusted.
 *
 * status 'production' → the module route exists and works.
 * status 'roadmap'    → the tile opens /lq-ai/management/roadmap/[id], a
 *                       value-prop page describing what the module will do.
 *
 * Adding module #15 later = one entry here + its route. Nothing else.
 */

export type ManagementZone = 'relationships' | 'operations';
export type ModuleStatus = 'production' | 'roadmap';

export interface ManagementModule {
	id: string;
	label: string;
	icon: string;
	zone: ManagementZone;
	status: ModuleStatus;
	route: string;
	/** One-line tile copy. */
	tagline: string;
	/** The relationship this module serves and what trust looks like there. */
	trustFrame: string;
	/** Roadmap pages: what the module will manage, in plain language. */
	whatItManages: string;
	/** Roadmap pages: how AI earns its keep here. */
	aiAngle: string;
}

/**
 * Stakeholder types grouped into each relationship space (mirrors api CHECK constraint).
 *
 * `descriptor` is the tile copy — what the space actually tracks, in the
 * GC's own words. `trustLooksLike` is the trust thesis behind the space;
 * it belongs in value-prop and roadmap copy, not on the working tile.
 */
export const RELATIONSHIP_SPACES: readonly {
	id: string;
	label: string;
	icon: string;
	stakeholderTypes: readonly string[];
	descriptor: string;
	trustLooksLike: string;
}[] = [
	{
		id: 'board',
		label: 'Board of Directors',
		icon: '🏛️',
		stakeholderTypes: ['board_chair', 'director'],
		descriptor: 'Pre-calls, meetings, agenda and minutes',
		trustLooksLike: 'The GC is our trusted advisor.'
	},
	{
		id: 'c-suite',
		label: 'CEO & C-Suite',
		icon: '👔',
		stakeholderTypes: ['ceo', 'c_suite_peer'],
		descriptor: 'Interactions and commitments',
		trustLooksLike: 'The GC gets things done, profitably.'
	},
	{
		id: 'investors',
		label: 'Investors & Lenders',
		icon: '💼',
		stakeholderTypes: ['investor_sponsor', 'lender'],
		descriptor: 'Filing due dates; disclosures',
		trustLooksLike: 'The GC is the source of truth and transparency.'
	},
	{
		id: 'regulators',
		label: 'Regulators & Auditors',
		icon: '⚖️',
		stakeholderTypes: ['regulator', 'auditor'],
		descriptor: 'Updates, meetings and required deliverables',
		trustLooksLike: 'The GC is responsive and credible.'
	},
	{
		id: 'outside-firms',
		label: 'Outside Firms',
		icon: '🏢',
		stakeholderTypes: ['outside_counsel'],
		descriptor: 'Engagements and status',
		trustLooksLike: 'The GC makes us better legal advisors.'
	},
	{
		id: 'media',
		label: 'Media',
		icon: '📰',
		stakeholderTypes: ['media'],
		descriptor: 'Daily scans and weekly summaries',
		trustLooksLike: 'The GC is willing to go on the record.'
	}
] as const;

export const MANAGEMENT_MODULES: readonly ManagementModule[] = [
	// ── Relationships zone (live: views over the Stakeholders module) ──
	{
		id: 'stakeholders',
		label: 'Stakeholder Management',
		icon: '🧑‍⚖️',
		zone: 'relationships',
		status: 'production',
		route: '/lq-ai/management/stakeholders',
		tagline: 'Every relationship that matters — dossiers, commitments, stances, cadence.',
		trustFrame:
			'Trust is earned one relationship at a time and lost in a single surprise. This is the system that makes "never drop a ball, never surprise a director" a discipline instead of a heroic act.',
		whatItManages:
			'A registry of the people who decide whether you win: directors, the CEO, C-suite peers, investors, lenders, customers, regulators, auditors, outside counsel, media. For each: a dossier with the interaction log, open commitments in both directions, their stance per live situation, and a touch cadence you configure.',
		aiAngle:
			'One click before any meeting: a concise, cited pre-meeting brief with a recommendation — drawn from the dossier and your document space, at the "would I have sent this?" bar.'
	},

	// ── Operations zone ──
	{
		id: 'kpis',
		label: 'KPIs & OKRs',
		icon: '🎯',
		zone: 'operations',
		status: 'production',
		route: '/lq-ai/management/kpis',
		tagline: 'The numbers and metrics which confirm Legal is an efficient profit center.',
		trustFrame:
			'The C-suite trusts what it can measure. Three to five outcome numbers — not vanity metrics — are how a legal department proves its value in language the business recognizes.',
		whatItManages:
			'A KPI catalog for the legal and compliance departments and for each team member: metric, baseline, target, owner, cadence, and why this number predicts success. A dashboard to monitor and update performance, three years of trend history, and preparation for 1-on-1 performance reviews.',
		aiAngle:
			'An interview wizard that asks the questions a seasoned GC would ask, then drafts your KPI catalog for line-by-line confirmation. Review-prep briefs assembled from each person’s actual numbers.'
	},
	{
		id: 'documents',
		label: 'Documents',
		icon: '🗄️',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/documents',
		tagline:
			'Searchable, editable, functional — board packs, minutes, memos. Private to this instance.',
		trustFrame:
			'Institutional memory is a trust instrument: the GC who can produce the board pack, the minutes, or the counsel letter on demand is the GC nobody second-guesses. This library keeps that record in your own instance, not in a vendor cloud.',
		whatItManages:
			'A private document space for the management record: board packs, meeting minutes, counsel letters, budget memos, regulator correspondence, negotiation summaries, compliance reports. Each document is inline markdown with type, date, author, and tags — filterable and searchable.',
		aiAngle:
			'These documents feed the pre-meeting brief — cited alongside the dossier. Extraction of commitments and stances from documents into dossiers is roadmap.'
	},
	{
		id: 'budget',
		label: 'Budget',
		icon: '💰',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/budget',
		tagline: 'Where the spend is now, with projections.',
		trustFrame:
			'The CFO stops auditing you and starts trusting you when every line is defensible and nothing is padded. A budget is not accounting — it is how you earn the CFO’s trust.',
		whatItManages:
			'The annual legal budget built through a guided interview — headcount, matter mix, deal pipeline, litigation exposure, panel rates, contingency — producing line items with a written assumptions narrative, then actuals, forecast, and accruals through the year.',
		aiAngle:
			'Interview → draft budget with a defensible assumptions narrative; a variance narrative for the CFO when reality moves.'
	},
	{
		id: 'spend-value',
		label: 'Outside Counsel',
		icon: '📈',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/spend-value',
		tagline: 'Trackable spend — by quarter, by year, by firm. Next up for production.',
		trustFrame:
			'"I hire lawyers, not law firms." Managing spend is not penny-pinching — it is proof to the business that legal treats company money like its own.',
		whatItManages:
			'Outside-counsel spend analytics, invoice review against billing guidelines, rate benchmarking, and a value ledger — savings, recoveries, avoided cost — the "legal pays for itself" story.',
		aiAngle:
			'Invoice upload → extraction to actuals; guideline-compliance flags on bills; the value story assembled in the CFO’s language.'
	},
	{
		id: 'matter-portfolio',
		label: 'Matter Portfolio',
		icon: '🗂️',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/matter-portfolio',
		tagline: 'Intake, triage, allocation — the whole book of work at a glance.',
		trustFrame:
			'The business trusts a department where the right work goes to the right resource on time. Reliability is systematic, not heroic.',
		whatItManages:
			'Intake and triage, allocation, a status board, and workload distribution across the team — extending LQ.AI’s existing Matters rather than duplicating them.',
		aiAngle:
			'Triage and allocation suggestions; portfolio synthesis — "the five matters that need you this week."'
	},
	{
		id: 'entities',
		label: 'Entities & Housekeeping',
		icon: '🏗️',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/entities',
		tagline: 'Subsidiaries, appointments, signing authorities, filings.',
		trustFrame:
			'Corporate housekeeping is invisible until it fails an audit or a deal. Clean entities are quiet proof the department is run well.',
		whatItManages:
			'The subsidiary registry, D&O appointments, signing authorities, and the filings calendar across jurisdictions.',
		aiAngle: 'Filing-obligation extraction; org-chart and appointment-expiry surfacing.'
	},
	{
		id: 'risk-register',
		label: 'Risk Register',
		icon: '🚨',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/risk-register',
		tagline: 'The downside: known, priced, and owned.',
		trustFrame:
			'The audit committee’s trust is earned by never being blindsided. A clean risk register is how you make sure they never are.',
		whatItManages:
			'Legal risks with owners, mitigations, and exposure; a disputes-exposure rollup; crisis and incident playbooks.',
		aiAngle:
			'Risk extraction from the matter portfolio; exposure summaries fit for the audit committee.'
	},
	{
		id: 'obligations',
		label: 'Obligations & Policies',
		icon: '📅',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/obligations',
		tagline: 'Filings, renewals, insurance, policies, training — nothing slips.',
		trustFrame:
			'Reliability means never dropping a ball. The obligations calendar is the discipline that makes "we never miss a deadline" true.',
		whatItManages:
			'The compliance and obligations calendar (filings, renewals, insurance including D&O), the policy lifecycle, and training compliance.',
		aiAngle:
			'Obligation extraction from contracts and policies already in your knowledge bases; deadline surfacing.'
	},
	{
		id: 'board-reporting',
		label: 'Board & Exec Reporting',
		icon: '📝',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/board-reporting',
		tagline: 'Fiduciary-grade reporting: cited, honest, short.',
		trustFrame:
			'"We are never surprised" is won before the meeting — in the report, the pre-calls, the socialization of hard issues.',
		whatItManages:
			'The quarterly legal-department report and the board/committee support calendar.',
		aiAngle:
			'An auto-drafted, cited report pulling KPIs, spend, portfolio, and risks — every claim traceable, nothing overclaimed.'
	},
	{
		id: 'business-feedback',
		label: 'Business-Partner Feedback',
		icon: '🗣️',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/business-feedback',
		tagline: 'What the business actually thinks of legal.',
		trustFrame:
			'The business is legal’s customer. Trust grows when they see you ask, listen, and change.',
		whatItManages:
			'Internal-client satisfaction pulses and complaint/praise themes by business unit.',
		aiAngle:
			'Survey synthesis and theme extraction — the honest digest of how legal is experienced.'
	},
	{
		id: 'team-org',
		label: 'Team & Org',
		icon: '🧩',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/team-org',
		tagline: 'Roster, coverage, capacity, succession.',
		trustFrame:
			'Your team trusts a leader with a plan: clear coverage, honest capacity, a bench being built.',
		whatItManages:
			'The roster (lawyers, paralegals, legal ops), roles, the skills/coverage matrix, workload and capacity views, and hiring/succession planning.',
		aiAngle:
			'Coverage-gap analysis ("who covers privacy in EMEA?"); workload-rebalancing suggestions from matter allocation.'
	},
	{
		id: 'team-performance',
		label: 'Team Performance',
		icon: '🌱',
		zone: 'operations',
		status: 'roadmap',
		route: '/lq-ai/management/roadmap/team-performance',
		tagline: 'Goals that move each person up their own stack.',
		trustFrame:
			'"I’ll grow here, and I’ve got air cover." Your team’s trust is earned with real goals, real development, and credit given up the chain.',
		whatItManages:
			'Individual goals cascaded from department KPIs, check-ins, review cycles, development plans, and utilization — kept strictly private (comp-adjacent).',
		aiAngle:
			'Interview-based goal setting; review briefs drafted from each lawyer’s actual contributions — including an explicit AI-leverage goal for everyone.'
	}
] as const;

/** The dashboard is the future cross-pillar home; listed for the roadmap page. */
export const DASHBOARD_MODULE: ManagementModule = {
	id: 'dashboard',
	label: 'Dashboard',
	icon: '🧭',
	zone: 'operations',
	status: 'roadmap',
	route: '/lq-ai/management/roadmap/dashboard',
	tagline: 'What needs the GC today — across every pillar.',
	trustFrame:
		'The operating rhythm that makes reliability systematic: open commitments, budget burn, KPI status, portfolio hotspots, upcoming obligations — one morning glance.',
	whatItManages:
		'A cross-module synthesis view: needs-attention cadence, open commitments, budget burn, KPI status, portfolio hotspots, upcoming obligations.',
	aiAngle: 'A cross-module synthesis brief each morning.'
};

export const ALL_MODULES: readonly ManagementModule[] = [
	...MANAGEMENT_MODULES,
	DASHBOARD_MODULE
] as const;

export function moduleById(id: string): ManagementModule | undefined {
	return ALL_MODULES.find((m) => m.id === id);
}
