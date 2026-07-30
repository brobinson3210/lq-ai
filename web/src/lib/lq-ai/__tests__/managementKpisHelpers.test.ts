/**
 * Unit tests for the KPI presentation helpers (Management tab).
 *
 * Covers period arithmetic for both cadences (incl. year rollover), the
 * direction-aware delta text, the attainment tone bands, the roster
 * performance rating, and unit-aware value formatting.
 */
import { describe, expect, it } from 'vitest';

import {
	attainmentLabel,
	attainmentTone,
	currentPeriod,
	deltaOf,
	formatKpiValue,
	nextPeriod,
	performanceRating,
	periodLabel,
	unitSuffix
} from '../management/kpis';

describe('nextPeriod', () => {
	it('advances a monthly period', () => {
		expect(nextPeriod('2026-06', 'monthly')).toBe('2026-07');
		expect(nextPeriod('2026-01', 'monthly')).toBe('2026-02');
	});

	it('rolls a monthly period over the year boundary', () => {
		expect(nextPeriod('2026-12', 'monthly')).toBe('2027-01');
	});

	it('advances a quarterly period', () => {
		expect(nextPeriod('2026-Q2', 'quarterly')).toBe('2026-Q3');
		expect(nextPeriod('2026-Q1', 'quarterly')).toBe('2026-Q2');
	});

	it('rolls a quarterly period over the year boundary', () => {
		expect(nextPeriod('2026-Q4', 'quarterly')).toBe('2027-Q1');
	});

	it('falls back to the current period when latest is null', () => {
		const now = new Date(2026, 6, 24); // 2026-07-24
		expect(nextPeriod(null, 'monthly', now)).toBe('2026-07');
		expect(nextPeriod(null, 'quarterly', now)).toBe('2026-Q3');
	});

	it('falls back to the current period on a cadence-format mismatch', () => {
		const now = new Date(2026, 0, 15); // 2026-01-15
		expect(nextPeriod('2026-Q2', 'monthly', now)).toBe('2026-01');
		expect(nextPeriod('2026-06', 'quarterly', now)).toBe('2026-Q1');
	});
});

describe('currentPeriod', () => {
	it('formats the containing month and quarter', () => {
		expect(currentPeriod('monthly', new Date(2026, 11, 31))).toBe('2026-12');
		expect(currentPeriod('quarterly', new Date(2026, 11, 31))).toBe('2026-Q4');
		expect(currentPeriod('quarterly', new Date(2026, 0, 1))).toBe('2026-Q1');
	});
});

describe('periodLabel', () => {
	it('labels monthly and quarterly periods', () => {
		expect(periodLabel('2026-06')).toBe('Jun 2026');
		expect(periodLabel('2026-12')).toBe('Dec 2026');
		expect(periodLabel('2026-Q2')).toBe('Q2 2026');
	});

	it('passes through unknown formats and handles null', () => {
		expect(periodLabel('weird')).toBe('weird');
		expect(periodLabel(null)).toBe('—');
	});
});

describe('deltaOf', () => {
	it('marks an increase as improving when higher is better', () => {
		const d = deltaOf('12.5', '10.4', 'higher_is_better', 'days');
		expect(d).not.toBeNull();
		expect(d?.text).toBe('+2.1 days');
		expect(d?.improving).toBe(true);
	});

	it('marks an increase as worsening when lower is better', () => {
		const d = deltaOf('12.5', '10.4', 'lower_is_better', 'days');
		expect(d?.text).toBe('+2.1 days');
		expect(d?.improving).toBe(false);
	});

	it('marks a decrease as improving when lower is better', () => {
		const d = deltaOf('7', '10.2', 'lower_is_better', 'days');
		expect(d?.text).toBe('-3.2 days');
		expect(d?.improving).toBe(true);
	});

	it('marks a decrease as worsening when higher is better', () => {
		const d = deltaOf('7', '10.2', 'higher_is_better', 'days');
		expect(d?.text).toBe('-3.2 days');
		expect(d?.improving).toBe(false);
	});

	it('treats a zero delta as holding steady', () => {
		const d = deltaOf('10', '10', 'lower_is_better', 'count');
		expect(d?.text).toBe('±0');
		expect(d?.improving).toBe(true);
	});

	it('formats % and USD deltas with their units', () => {
		expect(deltaOf('14', '12.5', 'higher_is_better', '%')?.text).toBe('+1.5%');
		expect(deltaOf('38000', '50000', 'lower_is_better', 'USD')?.text).toBe('-$12,000');
	});

	it('returns null when either value is missing or unparseable', () => {
		expect(deltaOf(null, '10', 'higher_is_better', 'days')).toBeNull();
		expect(deltaOf('10', null, 'higher_is_better', 'days')).toBeNull();
		expect(deltaOf('nope', '10', 'higher_is_better', 'days')).toBeNull();
	});
});

describe('attainmentTone', () => {
	it("maps Bill's traffic-light bands: green > 80, yellow > 50, red otherwise", () => {
		expect(attainmentTone('130')).toBe('good');
		expect(attainmentTone('100')).toBe('good');
		expect(attainmentTone('80.1')).toBe('good');
		expect(attainmentTone('80')).toBe('warn');
		expect(attainmentTone('50.1')).toBe('warn');
		expect(attainmentTone('50')).toBe('error');
		expect(attainmentTone('0')).toBe('error');
	});

	it('is muted for null / unparseable', () => {
		expect(attainmentTone(null)).toBe('muted');
		expect(attainmentTone(undefined)).toBe('muted');
		expect(attainmentTone('n/a')).toBe('muted');
	});
});

describe('performanceRating', () => {
	const kpis = (...pcts: (string | null)[]) => pcts.map((attainment_pct) => ({ attainment_pct }));

	it('bands the average: >= 95 exceeds, >= 80 meets, below that under', () => {
		expect(performanceRating(kpis('96'))).toEqual({ label: 'Exceeds', tone: 'good' });
		expect(performanceRating(kpis('95'))).toEqual({ label: 'Exceeds', tone: 'good' });
		expect(performanceRating(kpis('94.9'))).toEqual({ label: 'Meets', tone: 'warn' });
		expect(performanceRating(kpis('80'))).toEqual({ label: 'Meets', tone: 'warn' });
		expect(performanceRating(kpis('79.9'))).toEqual({ label: 'Below', tone: 'error' });
		expect(performanceRating(kpis('0'))).toEqual({ label: 'Below', tone: 'error' });
	});

	it('averages across a member’s KPIs', () => {
		// (120 + 70) / 2 = 95 -> exceeds, on the boundary.
		expect(performanceRating(kpis('120', '70'))).toEqual({ label: 'Exceeds', tone: 'good' });
		// (100 + 60) / 2 = 80 -> meets.
		expect(performanceRating(kpis('100', '60'))).toEqual({ label: 'Meets', tone: 'warn' });
	});

	it('skips unmeasurable KPIs rather than scoring them zero', () => {
		// A null attainment must not drag the average down; Number(null) is 0.
		expect(performanceRating(kpis('96', null))).toEqual({ label: 'Exceeds', tone: 'good' });
		expect(performanceRating(kpis('96', 'n/a'))).toEqual({ label: 'Exceeds', tone: 'good' });
	});

	it('has no rating when nothing is judgeable', () => {
		expect(performanceRating([])).toBeNull();
		expect(performanceRating(kpis(null))).toBeNull();
		expect(performanceRating(kpis(null, null))).toBeNull();
	});
});

describe('attainmentLabel', () => {
	it('renders whole-percent copy and null passthrough', () => {
		expect(attainmentLabel('92.4')).toBe('92% of target');
		expect(attainmentLabel('130')).toBe('130% of target');
		expect(attainmentLabel(null)).toBeNull();
	});
});

describe('formatKpiValue', () => {
	it('renders % from the wire string', () => {
		expect(formatKpiValue('12.5', '%')).toBe('12.5%');
	});

	it('renders USD compact at/above 100k and plain below', () => {
		expect(formatKpiValue('1200000', 'USD')).toBe('$1.2M');
		expect(formatKpiValue('250000', 'USD')).toBe('$250K');
		expect(formatKpiValue('45000', 'USD')).toBe('$45,000');
		expect(formatKpiValue('12.5', 'USD')).toBe('$12.5');
	});

	it('renders days and count as the plain wire string', () => {
		expect(formatKpiValue('12.5', 'days')).toBe('12.5');
		expect(formatKpiValue('42', 'count')).toBe('42');
	});

	it('em-dashes missing values', () => {
		expect(formatKpiValue(null, 'days')).toBe('—');
		expect(formatKpiValue(undefined, '%')).toBe('—');
		expect(formatKpiValue('', 'USD')).toBe('—');
	});
});

describe('unitSuffix', () => {
	it('suffixes only units not already embedded in the value', () => {
		expect(unitSuffix('days')).toBe('days');
		expect(unitSuffix('matters closed')).toBe('matters closed');
		expect(unitSuffix('%')).toBe('');
		expect(unitSuffix('USD')).toBe('');
		expect(unitSuffix('count')).toBe('');
	});
});
