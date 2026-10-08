import { describe, expect, it } from 'vitest';

import {
	areaLabel,
	bandLabel,
	bandTone,
	barPct,
	categoryLabel,
	formatMoney,
	formatPct,
	partnerStatusLabel,
	quarterLabel
} from '../management/outsideCounsel';

describe('outside counsel display helpers', () => {
	it('formats money as whole dollars and blanks as a dash', () => {
		expect(formatMoney('905000.00')).toBe('$905,000');
		expect(formatMoney('5609.25')).toBe('$5,609');
		expect(formatMoney(null)).toBe('—');
		expect(formatMoney('')).toBe('—');
		expect(formatMoney('n/a')).toBe('n/a');
	});

	it('formats percentages from the server string', () => {
		expect(formatPct('115.0')).toBe('115.0%');
		expect(formatPct(null)).toBe('—');
	});

	it('maps bands to chip tones and labels', () => {
		expect(bandTone('green')).toBe('good');
		expect(bandTone('yellow')).toBe('warn');
		expect(bandTone('red')).toBe('error');
		expect(bandTone(null)).toBe('muted');
		expect(bandLabel('red')).toBe('Over budget');
		expect(bandLabel(undefined)).toBe('No budget');
	});

	it('labels quarters, areas, categories and partner statuses', () => {
		expect(quarterLabel('2026-Q3')).toBe('Q3 2026');
		expect(quarterLabel('odd')).toBe('odd');
		expect(areaLabel('corporate')).toBe('Corporate / M&A');
		expect(areaLabel('all')).toBe('Department total');
		expect(areaLabel(null)).toBe('—');
		expect(categoryLabel('insourcing_avoidance')).toBe('Insourcing avoidance');
		expect(partnerStatusLabel('left_firm')).toBe('Left the firm');
	});

	it('scales bars against the max and clamps', () => {
		expect(barPct('50', 200)).toBe(25);
		expect(barPct('500', 200)).toBe(100);
		expect(barPct('10', 0)).toBe(0);
		expect(barPct('x', 10)).toBe(0);
	});
});
