/**
 * Unit tests for the Urgent Matters API client.
 *
 * The client is a single read; these pin the path, the auth header, that
 * the server's ordering is passed through untouched (the client never
 * re-ranks), and typed-error propagation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getUrgentMatters } from '../api/managementUrgent';
import type { UrgentItem, UrgentMattersResponse } from '../types';
import { clearSession, setSession } from '../auth/store';
import { LQAIApiError } from '../api/client';

const realFetch = global.fetch;

function jsonResponse(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

function item(overrides: Partial<UrgentItem>): UrgentItem {
	return {
		kind: 'commitment',
		title: 'Deliver: board memo',
		why: 'Due 2026-10-07 — 1 day overdue — owed to Margo (Board Chair)',
		next_step: 'Deliver or renegotiate the date',
		due_date: '2026-10-07',
		days_until_due: -1,
		stakeholder_id: 's-1',
		stakeholder_name: 'Margo',
		kpi_id: null,
		firm_id: null,
		link: '/lq-ai/management/stakeholders/s-1',
		...overrides
	};
}

describe('management Urgent Matters API', () => {
	beforeEach(() => {
		clearSession();
		setSession({ access_token: 'tok', expires_in: 900 });
		vi.restoreAllMocks();
	});

	afterEach(() => {
		global.fetch = realFetch;
	});

	it('GETs the feed with the auth header and keeps the server order', async () => {
		const body: UrgentMattersResponse = {
			generated_at: '2026-10-08T12:00:00Z',
			red: [
				item({}),
				item({
					kind: 'outside_counsel',
					title: 'Discount below 10%: Cheap Firm',
					due_date: null,
					days_until_due: null,
					stakeholder_id: null,
					stakeholder_name: null,
					firm_id: 'f-1',
					link: '/lq-ai/management/outside-counsel'
				})
			],
			yellow: []
		};
		const fetchSpy = vi.fn(async () => jsonResponse(200, body));
		global.fetch = fetchSpy as unknown as typeof fetch;

		const out = await getUrgentMatters();
		expect(out.red.map((i) => i.kind)).toEqual(['commitment', 'outside_counsel']);
		expect(out.red[1].firm_id).toBe('f-1');
		const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toMatch(/\/management\/urgent-matters$/);
		expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
	});

	it('surfaces an error status as LQAIApiError', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(500, { detail: { code: 'internal', message: 'boom' } })
		) as unknown as typeof fetch;
		await expect(getUrgentMatters()).rejects.toBeInstanceOf(LQAIApiError);
	});
});
