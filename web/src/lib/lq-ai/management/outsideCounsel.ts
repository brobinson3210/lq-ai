/**
 * Display helpers for the Outside Counsel module (pure, unit-tested).
 *
 * The server computes every number and band; these only format them.
 */
import type {
	OcBand,
	OcBudgetArea,
	OcPartnerStatus,
	OcTimekeeperTitle,
	OcValueCategory
} from '$lib/lq-ai/types';

export const PRACTICE_AREAS: { value: Exclude<OcBudgetArea, 'all'>; label: string }[] = [
	{ value: 'commercial', label: 'Commercial' },
	{ value: 'corporate', label: 'Corporate / M&A' },
	{ value: 'employment', label: 'Employment' },
	{ value: 'ip', label: 'IP' },
	{ value: 'litigation', label: 'Litigation' },
	{ value: 'privacy', label: 'Privacy' },
	{ value: 'regulatory', label: 'Regulatory' },
	{ value: 'other', label: 'Other' }
];

export const TIMEKEEPER_TITLES: { value: OcTimekeeperTitle; label: string }[] = [
	{ value: 'partner', label: 'Partner' },
	{ value: 'counsel', label: 'Counsel' },
	{ value: 'associate', label: 'Associate' },
	{ value: 'paralegal', label: 'Paralegal' },
	{ value: 'other', label: 'Other' }
];

export const VALUE_CATEGORIES: { value: OcValueCategory; label: string }[] = [
	{ value: 'self_service_savings', label: 'Self-service savings' },
	{ value: 'billing_adjustments', label: 'Billing adjustments & rate discipline' },
	{ value: 'insourcing_avoidance', label: 'Insourcing avoidance' },
	{ value: 'settlement_avoidance', label: 'Settlement / demand avoidance' }
];

const PARTNER_STATUS_LABELS: Record<OcPartnerStatus, string> = {
	active: 'Active',
	left_firm: 'Left the firm',
	replaced: 'Replaced',
	followed: 'Followed to new firm'
};

export function areaLabel(value: string | null | undefined): string {
	if (!value) return '—';
	if (value === 'all') return 'Department total';
	return PRACTICE_AREAS.find((a) => a.value === value)?.label ?? value;
}

export function categoryLabel(value: string): string {
	return VALUE_CATEGORIES.find((c) => c.value === value)?.label ?? value;
}

export function partnerStatusLabel(value: OcPartnerStatus): string {
	return PARTNER_STATUS_LABELS[value] ?? value;
}

/** "$1,234,567" — whole dollars for display; null/blank → "—". */
export function formatMoney(value: string | null | undefined): string {
	if (value === null || value === undefined || value === '') return '—';
	const n = Number(value);
	if (!Number.isFinite(n)) return value;
	return n.toLocaleString('en-US', {
		style: 'currency',
		currency: 'USD',
		maximumFractionDigits: 0
	});
}

/** "115.0%" — null → "—". */
export function formatPct(value: string | null | undefined): string {
	if (value === null || value === undefined || value === '') return '—';
	return `${value}%`;
}

/** Band → the chip tone names shared with the KPI pages. */
export function bandTone(band: OcBand | null | undefined): 'good' | 'warn' | 'error' | 'muted' {
	if (band === 'green') return 'good';
	if (band === 'yellow') return 'warn';
	if (band === 'red') return 'error';
	return 'muted';
}

export function bandLabel(band: OcBand | null | undefined): string {
	if (band === 'green') return 'On budget';
	if (band === 'yellow') return 'Watch';
	if (band === 'red') return 'Over budget';
	return 'No budget';
}

/** "2026-Q3" → "Q3 2026". */
export function quarterLabel(period: string): string {
	const m = /^(\d{4})-Q([1-4])$/.exec(period);
	return m ? `Q${m[2]} ${m[1]}` : period;
}

/** Bar width (0–100) of `amount` against the largest value in a list. */
export function barPct(amount: string, max: number): number {
	const n = Number(amount);
	if (!Number.isFinite(n) || max <= 0) return 0;
	return Math.max(0, Math.min(100, (n / max) * 100));
}
