/**
 * Presentation helpers for the KPIs & OKRs module (Management tab).
 *
 * Enum labels/options, attainment chip tones (reusing the stakeholders
 * ChipTone buckets), delta text vs the previous period, period arithmetic
 * for the two cadences, and unit-aware value formatting.
 *
 * Numeric wire values are JSON strings — helpers parse with Number() only
 * where math is required and otherwise display from the string.
 */
import type { KpiCadence, KpiDepartment, KpiDirection, KpiRead, KpiScope } from '../types';
import type { ChipTone } from './stakeholders';

// ---------------------------------------------------------------------------
// Enum labels / options
// ---------------------------------------------------------------------------

export const DEPARTMENT_LABELS: Record<KpiDepartment, string> = {
	legal: 'Legal',
	compliance: 'Compliance'
};

export const DEPARTMENT_OPTIONS = Object.entries(DEPARTMENT_LABELS).map(([value, label]) => ({
	value: value as KpiDepartment,
	label
}));

export const SCOPE_LABELS: Record<KpiScope, string> = {
	department: 'Department',
	individual: 'Individual'
};

export const SCOPE_OPTIONS = Object.entries(SCOPE_LABELS).map(([value, label]) => ({
	value: value as KpiScope,
	label
}));

export const CADENCE_LABELS: Record<KpiCadence, string> = {
	monthly: 'Monthly',
	quarterly: 'Quarterly'
};

export const CADENCE_OPTIONS = Object.entries(CADENCE_LABELS).map(([value, label]) => ({
	value: value as KpiCadence,
	label
}));

export const DIRECTION_LABELS: Record<KpiDirection, string> = {
	higher_is_better: 'Higher is better',
	lower_is_better: 'Lower is better'
};

export const DIRECTION_OPTIONS = Object.entries(DIRECTION_LABELS).map(([value, label]) => ({
	value: value as KpiDirection,
	label
}));

export function departmentLabel(d: KpiDepartment | string): string {
	return DEPARTMENT_LABELS[d as KpiDepartment] ?? d;
}

// ---------------------------------------------------------------------------
// Attainment
// ---------------------------------------------------------------------------

/**
 * Chip tone for the direction-aware attainment percentage (>100 always means
 * beating target). Null/unparseable → muted.
 */
export function attainmentTone(attainmentPct: string | null | undefined): ChipTone {
	if (attainmentPct === null || attainmentPct === undefined) return 'muted';
	const n = Number(attainmentPct);
	if (!Number.isFinite(n)) return 'muted';
	// Bill's traffic-light rule (7/29): > 80% green, > 50% yellow, rest red.
	if (n > 80) return 'good';
	if (n > 50) return 'warn';
	return 'error';
}

export interface PerformanceRating {
	/** 'Exceeds' | 'Meets' | 'Below' — the roster bubble's text. */
	label: string;
	tone: ChipTone;
}

/**
 * A team member's overall performance read, averaged across their individual
 * KPIs: >= 95% of target exceeds, >= 80% meets, below that under-performing.
 * The 80% line is the same one the attainment chips and the Urgent Matters
 * red band use, so the roster never contradicts the KPI pages.
 *
 * KPIs with no target or no datapoints carry a null attainment and are
 * skipped — they cannot be judged. When nothing is judgeable the member has
 * no rating at all (null), which the roster renders as no bubble rather than
 * an unearned green or a misleading red.
 */
export function performanceRating(
	kpis: readonly Pick<KpiRead, 'attainment_pct'>[]
): PerformanceRating | null {
	// Drop nulls BEFORE parsing — Number(null) is 0, which would read as a
	// catastrophic score rather than "not measured".
	const scores = kpis
		.filter((k) => k.attainment_pct !== null && k.attainment_pct !== undefined)
		.map((k) => Number(k.attainment_pct))
		.filter((n) => Number.isFinite(n));
	if (scores.length === 0) return null;
	const mean = scores.reduce((sum, n) => sum + n, 0) / scores.length;
	if (mean >= 95) return { label: 'Exceeds', tone: 'good' };
	if (mean >= 80) return { label: 'Meets', tone: 'warn' };
	return { label: 'Below', tone: 'error' };
}

/** "92% of target" copy — displayed from the wire string, trimmed to whole %. */
export function attainmentLabel(attainmentPct: string | null | undefined): string | null {
	if (attainmentPct === null || attainmentPct === undefined) return null;
	const n = Number(attainmentPct);
	if (!Number.isFinite(n)) return null;
	return `${formatNumber(n, 0)}% of target`;
}

// ---------------------------------------------------------------------------
// Delta vs previous period
// ---------------------------------------------------------------------------

export interface KpiDelta {
	/** Signed, unit-aware text, e.g. "+3.2 days" / "-$12,000" / "+1.5%". */
	text: string;
	/** Direction-aware: did the metric move the way we want it to? */
	improving: boolean;
}

/**
 * Signed delta of latest vs previous, judged against the KPI's direction.
 * A zero delta reads as holding steady (improving: true). Returns null when
 * either value is missing or unparseable.
 */
export function deltaOf(
	latest: string | null | undefined,
	previous: string | null | undefined,
	direction: KpiDirection,
	unit: string
): KpiDelta | null {
	if (latest === null || latest === undefined) return null;
	if (previous === null || previous === undefined) return null;
	const a = Number(latest);
	const b = Number(previous);
	if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
	const delta = a - b;
	const sign = delta > 0 ? '+' : delta < 0 ? '-' : '±';
	const magnitude = formatUnitMagnitude(Math.abs(delta), unit);
	const improving = delta === 0 || (direction === 'higher_is_better' ? delta > 0 : delta < 0);
	return { text: `${sign}${magnitude}`, improving };
}

// ---------------------------------------------------------------------------
// Periods ('YYYY-MM' monthly / 'YYYY-Qn' quarterly)
// ---------------------------------------------------------------------------

const MONTHLY_RE = /^(\d{4})-(0[1-9]|1[0-2])$/;
const QUARTERLY_RE = /^(\d{4})-Q([1-4])$/;

/** The period containing `now`, in the cadence's format. */
export function currentPeriod(cadence: KpiCadence, now: Date = new Date()): string {
	const y = now.getFullYear();
	const m = now.getMonth(); // 0-based
	if (cadence === 'monthly') return `${y}-${String(m + 1).padStart(2, '0')}`;
	return `${y}-Q${Math.floor(m / 3) + 1}`;
}

/**
 * The period after `latestPeriod`, with year rollover ("2026-12" → "2027-01",
 * "2026-Q4" → "2027-Q1"). When `latestPeriod` is null or does not match the
 * cadence's format, falls back to the current period.
 */
export function nextPeriod(
	latestPeriod: string | null | undefined,
	cadence: KpiCadence,
	now: Date = new Date()
): string {
	if (latestPeriod) {
		if (cadence === 'monthly') {
			const match = MONTHLY_RE.exec(latestPeriod);
			if (match) {
				const y = Number(match[1]);
				const m = Number(match[2]);
				return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`;
			}
		} else {
			const match = QUARTERLY_RE.exec(latestPeriod);
			if (match) {
				const y = Number(match[1]);
				const q = Number(match[2]);
				return q === 4 ? `${y + 1}-Q1` : `${y}-Q${q + 1}`;
			}
		}
	}
	return currentPeriod(cadence, now);
}

const MONTH_SHORT = [
	'Jan',
	'Feb',
	'Mar',
	'Apr',
	'May',
	'Jun',
	'Jul',
	'Aug',
	'Sep',
	'Oct',
	'Nov',
	'Dec'
];

/** "2026-06" → "Jun 2026"; "2026-Q2" → "Q2 2026"; anything else verbatim. */
export function periodLabel(period: string | null | undefined): string {
	if (!period) return '—';
	const monthly = MONTHLY_RE.exec(period);
	if (monthly) return `${MONTH_SHORT[Number(monthly[2]) - 1]} ${monthly[1]}`;
	const quarterly = QUARTERLY_RE.exec(period);
	if (quarterly) return `Q${quarterly[2]} ${quarterly[1]}`;
	return period;
}

// ---------------------------------------------------------------------------
// Value formatting
// ---------------------------------------------------------------------------

/** Trim a number to at most `maxDecimals` places, dropping trailing zeros. */
function formatNumber(n: number, maxDecimals = 2): string {
	return n.toLocaleString('en-US', {
		minimumFractionDigits: 0,
		maximumFractionDigits: maxDecimals
	});
}

/** USD formatting: compact ("$1.2M") at/above 100k, plain currency below. */
function formatUsd(n: number): string {
	if (Math.abs(n) >= 100_000) {
		return n.toLocaleString('en-US', {
			style: 'currency',
			currency: 'USD',
			notation: 'compact',
			minimumFractionDigits: 0,
			maximumFractionDigits: 1
		});
	}
	return n.toLocaleString('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 0,
		maximumFractionDigits: 2
	});
}

/** Unit-aware magnitude used by delta text (always parsed — deltas are math). */
function formatUnitMagnitude(n: number, unit: string): string {
	if (unit === '%') return `${formatNumber(n)}%`;
	if (unit === 'USD') return formatUsd(n);
	if (unit === 'days') return `${formatNumber(n)} days`;
	if (unit === 'count') return formatNumber(n);
	return `${formatNumber(n)} ${unit}`;
}

/**
 * Display a wire value respecting its unit:
 *   '%'   → "12.5%"  (from the string)
 *   'USD' → "$1.2M" compact above 100k, "$45,000" below
 *   'days' / 'count' / other → the plain wire string
 */
export function formatKpiValue(value: string | null | undefined, unit: string): string {
	if (value === null || value === undefined || value === '') return '—';
	if (unit === '%') return `${value}%`;
	if (unit === 'USD') {
		const n = Number(value);
		if (!Number.isFinite(n)) return value;
		return formatUsd(n);
	}
	return value;
}

/**
 * Small suffix rendered beside a plain hero value ('days' etc.). Empty for
 * units already embedded by formatKpiValue ('%', 'USD') and for bare counts.
 */
export function unitSuffix(unit: string): string {
	if (unit === '%' || unit === 'USD' || unit === 'count') return '';
	return unit;
}
