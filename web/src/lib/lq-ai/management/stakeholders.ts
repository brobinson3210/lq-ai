/**
 * Presentation helpers for the Stakeholders module (Management tab).
 *
 * Labels, chip tones, and small date helpers shared by the registry page,
 * the dossier page, the commitments rollup, and the new-stakeholder modal.
 * Space definitions stay in ./modules (RELATIONSHIP_SPACES) — this file
 * only maps enum values to display copy and lq-* token tones.
 */
import type {
	CommitmentStatus,
	InteractionChannel,
	StakeholderHealth,
	StakeholderStance,
	StakeholderType
} from '../types';

// ---------------------------------------------------------------------------
// Stakeholder types
// ---------------------------------------------------------------------------

export const STAKEHOLDER_TYPE_LABELS: Record<StakeholderType, string> = {
	board_chair: 'Board chair',
	director: 'Director',
	ceo: 'CEO',
	c_suite_peer: 'C-suite peer',
	investor_sponsor: 'Investor / sponsor',
	lender: 'Lender',
	customer: 'Customer',
	regulator: 'Regulator',
	auditor: 'Auditor',
	outside_counsel: 'Outside counsel',
	media: 'Media',
	other: 'Other'
};

export const STAKEHOLDER_TYPE_OPTIONS = Object.entries(STAKEHOLDER_TYPE_LABELS).map(
	([value, label]) => ({ value: value as StakeholderType, label })
);

/** Types for which committee_seats is meaningful (board seats). */
export const BOARD_TYPES: readonly StakeholderType[] = ['board_chair', 'director'];

export function typeLabel(t: StakeholderType | string): string {
	return STAKEHOLDER_TYPE_LABELS[t as StakeholderType] ?? t;
}

// ---------------------------------------------------------------------------
// Chip tones — map to the lq-* palette in the pages' scoped styles
// ---------------------------------------------------------------------------

/** Visual tone bucket; pages style `.chip--{tone}` off the lq-* tokens. */
export type ChipTone = 'good' | 'info' | 'warn' | 'error' | 'muted';

export const HEALTH_LABELS: Record<StakeholderHealth, string> = {
	green: 'Green — good',
	yellow: 'Yellow — watch',
	red: 'Red — act now'
};

export const HEALTH_OPTIONS = Object.entries(HEALTH_LABELS).map(([value, label]) => ({
	value: value as StakeholderHealth,
	label
}));

export function healthLabel(h: StakeholderHealth | null | undefined): string {
	return h ? HEALTH_LABELS[h] : 'Unset';
}

export function healthTone(h: StakeholderHealth | null | undefined): ChipTone {
	// Bill's traffic-light scale (7/29): green / yellow / red, nothing else.
	switch (h) {
		case 'green':
			return 'good';
		case 'yellow':
			return 'warn';
		case 'red':
			return 'error';
		default:
			return 'muted';
	}
}

export const STANCE_LABELS: Record<StakeholderStance, string> = {
	champion: 'Champion',
	supportive: 'Supportive',
	neutral: 'Neutral',
	skeptical: 'Skeptical',
	opposed: 'Opposed',
	unknown: 'Unknown'
};

export const STANCE_OPTIONS = Object.entries(STANCE_LABELS).map(([value, label]) => ({
	value: value as StakeholderStance,
	label
}));

export function stanceTone(s: StakeholderStance): ChipTone {
	switch (s) {
		case 'champion':
		case 'supportive':
			return 'good';
		case 'skeptical':
			return 'warn';
		case 'opposed':
			return 'error';
		default:
			return 'muted';
	}
}

export const CHANNEL_LABELS: Record<InteractionChannel, string> = {
	meeting: 'Meeting',
	call: 'Call',
	email: 'Email',
	message: 'Message',
	board_meeting: 'Board meeting',
	social: 'Social',
	other: 'Other'
};

export const CHANNEL_OPTIONS = Object.entries(CHANNEL_LABELS).map(([value, label]) => ({
	value: value as InteractionChannel,
	label
}));

// ---------------------------------------------------------------------------
// Recency / cadence / due dates
// ---------------------------------------------------------------------------

/** "last touch: N days ago" copy from the computed days_since field. */
export function lastTouchLabel(days: number | null | undefined): string {
	if (days === null || days === undefined) return 'Last touch: never';
	if (days === 0) return 'Last touch: today';
	if (days === 1) return 'Last touch: 1 day ago';
	return `Last touch: ${days} days ago`;
}

/** True when a cadence target is set and the last touch exceeds it (or never happened). */
export function cadenceExceeded(
	days: number | null | undefined,
	cadenceTargetDays: number | null | undefined
): boolean {
	if (cadenceTargetDays === null || cadenceTargetDays === undefined) return false;
	if (days === null || days === undefined) return true;
	return days > cadenceTargetDays;
}

/** Overdue = still open and due strictly before today (YYYY-MM-DD compare). */
export function isOverdue(
	dueDate: string | null | undefined,
	status: CommitmentStatus,
	today: string = todayISODate()
): boolean {
	if (!dueDate || status !== 'open') return false;
	return dueDate < today;
}

/** Today as YYYY-MM-DD in the user's local timezone. */
export function todayISODate(d: Date = new Date()): string {
	const y = d.getFullYear();
	const m = String(d.getMonth() + 1).padStart(2, '0');
	const day = String(d.getDate()).padStart(2, '0');
	return `${y}-${m}-${day}`;
}

/** Now as a `datetime-local` input value (YYYY-MM-DDTHH:MM, local time). */
export function nowLocalDatetime(d: Date = new Date()): string {
	const h = String(d.getHours()).padStart(2, '0');
	const min = String(d.getMinutes()).padStart(2, '0');
	return `${todayISODate(d)}T${h}:${min}`;
}

/** Convert a `datetime-local` input value to an ISO-8601 UTC datetime. */
export function localDatetimeToISO(value: string): string {
	return new Date(value).toISOString();
}

/** Render an ISO datetime as a short local date-time for the timeline. */
export function formatDateTime(iso: string): string {
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return iso;
	return d.toLocaleString(undefined, {
		year: 'numeric',
		month: 'short',
		day: 'numeric',
		hour: 'numeric',
		minute: '2-digit'
	});
}

/** Render a YYYY-MM-DD date without timezone drift. */
export function formatDate(ymd: string | null | undefined): string {
	if (!ymd) return '—';
	const [y, m, d] = ymd.split('-').map(Number);
	if (!y || !m || !d) return ymd;
	return new Date(y, m - 1, d).toLocaleDateString(undefined, {
		year: 'numeric',
		month: 'short',
		day: 'numeric'
	});
}
