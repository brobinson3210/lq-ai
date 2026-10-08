/**
 * Unit tests for the Management Outside Counsel API client.
 *
 * Mocks `fetch` so the calls don't escape the test runner. Mirrors
 * managementKpisApi.test.ts: paths and query strings, methods and JSON
 * bodies, id encoding, empty-204 handling, and typed-error propagation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	addPartner,
	createFirm,
	createInvoice,
	createValueEntry,
	deleteFirm,
	deleteInvoice,
	getSummary,
	listFirms,
	listInvoices,
	listValueEntries,
	patchInvoice,
	patchPartner,
	upsertBudget
} from '../api/managementOutsideCounsel';
import type { OcFirm, OcInvoice, OcPartner, OcSummary } from '../types';
import { clearSession, setSession } from '../auth/store';
import { LQAIApiError } from '../api/client';

const realFetch = global.fetch;

function jsonResponse(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

function emptyResponse(status: number): Response {
	return new Response(null, { status });
}

interface FetchSpyLike {
	mock: { calls: unknown[] };
}

function calledUrl(fetchSpy: FetchSpyLike): string {
	return (fetchSpy.mock.calls[0] as [string, RequestInit])[0];
}

function calledInit(fetchSpy: FetchSpyLike): RequestInit {
	return (fetchSpy.mock.calls[0] as [string, RequestInit])[1];
}

const PARTNER: OcPartner = {
	id: 'p-1',
	firm_id: 'f-1',
	stakeholder_id: null,
	name: 'Elliot Marchetti',
	practice_area: 'corporate',
	status: 'active',
	left_at: null,
	resolution_note: null,
	created_at: '2026-10-01T12:00:00Z',
	updated_at: '2026-10-01T12:00:00Z'
};

const FIRM: OcFirm = {
	id: 'f-1',
	owner_id: 'u-1',
	name: 'Hartwell & Crane LLP',
	discount_pct: '10',
	rate_increase_pct: '6',
	rate_year: 2026,
	notes_md: null,
	created_at: '2026-10-01T12:00:00Z',
	updated_at: '2026-10-01T12:00:00Z',
	partners: [PARTNER],
	below_discount_floor: false,
	above_increase_cap: true,
	spend_total: '5609.25',
	invoice_count: 1
};

const INVOICE: OcInvoice = {
	id: 'i/1',
	firm_id: 'f-1',
	firm_name: 'Hartwell & Crane LLP',
	invoice_number: 'HC-101',
	invoice_date: '2026-05-31',
	period: '2026-Q2',
	practice_area: 'corporate',
	matter_ref: null,
	status: 'received',
	notes_md: null,
	total: '5609.25',
	line_count: 2,
	lines: [],
	staffing_flags: [],
	created_at: '2026-10-01T12:00:00Z',
	updated_at: '2026-10-01T12:00:00Z'
};

const SUMMARY: OcSummary = {
	year: 2026,
	min_discount_pct: '10',
	max_rate_increase_pct: '5',
	max_billers_per_task: 2,
	quarters: [
		{
			period: '2026-Q1',
			budget: '1000.00',
			actual: '1150.00',
			pct_of_budget: '115.0',
			band: 'yellow'
		}
	],
	year_budget: '1000.00',
	year_actual: '1150.00',
	year_pct_of_budget: '115.0',
	by_firm: [],
	by_practice_area: [],
	staffing_flags: [],
	rate_flags: [],
	partner_alerts: [],
	value_total: '0.00',
	value_by_category: []
};

describe('management Outside Counsel API', () => {
	beforeEach(() => {
		clearSession();
		setSession({ access_token: 'tok', expires_in: 900 });
		vi.restoreAllMocks();
	});

	afterEach(() => {
		global.fetch = realFetch;
	});

	it('getSummary hits /summary with an optional year and the auth header', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, SUMMARY));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await getSummary(2026);
		expect(out.quarters[0].band).toBe('yellow');
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/outside-counsel\/summary\?year=2026$/);
		const headers = calledInit(fetchSpy).headers as Record<string, string>;
		expect(headers.Authorization).toBe('Bearer tok');

		const bare = vi.fn(async () => jsonResponse(200, SUMMARY));
		global.fetch = bare as unknown as typeof fetch;
		await getSummary();
		expect(calledUrl(bare)).toMatch(/\/outside-counsel\/summary$/);
	});

	it('listFirms and createFirm use the firms path', async () => {
		const listSpy = vi.fn(async () => jsonResponse(200, [FIRM]));
		global.fetch = listSpy as unknown as typeof fetch;
		const firms = await listFirms();
		expect(firms[0].above_increase_cap).toBe(true);
		expect(calledUrl(listSpy)).toMatch(/\/outside-counsel\/firms$/);

		const createSpy = vi.fn(async () => jsonResponse(201, FIRM));
		global.fetch = createSpy as unknown as typeof fetch;
		await createFirm({ name: 'Hartwell & Crane LLP', discount_pct: '10' });
		const init = calledInit(createSpy);
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body as string).discount_pct).toBe('10');
	});

	it('deleteFirm sends DELETE and tolerates an empty 204', async () => {
		const fetchSpy = vi.fn(async () => emptyResponse(204));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await expect(deleteFirm('f-1')).resolves.toBeUndefined();
		expect(calledInit(fetchSpy).method).toBe('DELETE');
		expect(calledUrl(fetchSpy)).toMatch(/\/outside-counsel\/firms\/f-1$/);
	});

	it('addPartner POSTs under the firm; patchPartner marks a partner as left', async () => {
		const addSpy = vi.fn(async () => jsonResponse(201, PARTNER));
		global.fetch = addSpy as unknown as typeof fetch;
		await addPartner('f-1', { name: 'Elliot Marchetti' });
		expect(calledUrl(addSpy)).toMatch(/\/outside-counsel\/firms\/f-1\/partners$/);

		const patchSpy = vi.fn(async () =>
			jsonResponse(200, { ...PARTNER, status: 'left_firm', left_at: '2026-10-08' })
		);
		global.fetch = patchSpy as unknown as typeof fetch;
		const out = await patchPartner('p-1', { status: 'left_firm' });
		expect(out.status).toBe('left_firm');
		expect(calledUrl(patchSpy)).toMatch(/\/outside-counsel\/partners\/p-1$/);
		expect(calledInit(patchSpy).method).toBe('PATCH');
	});

	it('upsertBudget PUTs the quarter and amount', async () => {
		const fetchSpy = vi.fn(async () =>
			jsonResponse(200, {
				id: 'b-1',
				period: '2026-Q1',
				practice_area: 'all',
				amount: '905000.00',
				notes_md: null,
				created_at: '',
				updated_at: ''
			})
		);
		global.fetch = fetchSpy as unknown as typeof fetch;
		await upsertBudget({ period: '2026-Q1', amount: '905000' });
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('PUT');
		expect(JSON.parse(init.body as string)).toEqual({ period: '2026-Q1', amount: '905000' });
	});

	it('listInvoices builds the filter query and skips empty values', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, [INVOICE]));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listInvoices({ firmId: 'f-1', period: '2026-Q2' });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('firm_id=f-1');
		expect(url).toContain('period=2026-Q2');
		expect(url).not.toContain('year=');
	});

	it('createInvoice POSTs lines; patchInvoice and deleteInvoice encode the id', async () => {
		const createSpy = vi.fn(async () => jsonResponse(201, INVOICE));
		global.fetch = createSpy as unknown as typeof fetch;
		await createInvoice({
			firm_id: 'f-1',
			invoice_date: '2026-05-31',
			practice_area: 'corporate',
			lines: [
				{
					work_date: '2026-05-02',
					timekeeper: 'Aaron Feldstein',
					title: 'associate',
					task: 'Draft SPA',
					hours: '3',
					rate: '801'
				}
			]
		});
		expect(JSON.parse(calledInit(createSpy).body as string).lines).toHaveLength(1);

		const patchSpy = vi.fn(async () => jsonResponse(200, INVOICE));
		global.fetch = patchSpy as unknown as typeof fetch;
		await patchInvoice('i/1', { status: 'paid' });
		expect(calledUrl(patchSpy)).toMatch(/\/outside-counsel\/invoices\/i%2F1$/);

		const deleteSpy = vi.fn(async () => emptyResponse(204));
		global.fetch = deleteSpy as unknown as typeof fetch;
		await deleteInvoice('i/1');
		expect(calledInit(deleteSpy).method).toBe('DELETE');
	});

	it('value ledger: list filters, and a missing receipt surfaces 422', async () => {
		const listSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = listSpy as unknown as typeof fetch;
		await listValueEntries({ year: 2026, category: 'billing_adjustments' });
		expect(calledUrl(listSpy)).toContain('year=2026');
		expect(calledUrl(listSpy)).toContain('category=billing_adjustments');

		global.fetch = vi.fn(async () =>
			jsonResponse(422, { detail: { code: 'unprocessable', message: 'source required' } })
		) as unknown as typeof fetch;
		await expect(
			createValueEntry({
				period: '2026-Q1',
				category: 'billing_adjustments',
				amount: '100',
				description: 'x',
				method_note: 'y',
				source: ''
			})
		).rejects.toBeInstanceOf(LQAIApiError);
	});
});
