/**
 * Unit tests for the Management KPIs API client.
 *
 * Mocks `fetch` so the calls don't escape the test runner. Mirrors
 * stakeholdersApi.test.ts: query-param building (kpi filters, datapoint
 * overwrite / from-to bounds), method + body assertions, URL encoding, and
 * typed-error propagation through the shared client (404 / 409 / 422 / 400).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	createDatapoint,
	createKpi,
	createTeamMember,
	deleteKpi,
	deleteTeamMember,
	getDashboard,
	getKpi,
	getSeries,
	getTeamMember,
	listDatapoints,
	listKpis,
	listTeamMembers,
	patchKpi,
	patchTeamMember
} from '../api/managementKpis';
import type { KpiDashboard, KpiRead, TeamMemberRead } from '../types';
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

const MEMBER: TeamMemberRead = {
	id: 'a7c9d8e2-4b31-4f6a-9d2e-8b1c3f5a7e90',
	name: 'Priya Raman',
	role_title: 'Senior Counsel, Commercial',
	department: 'legal',
	seniority: 'Senior',
	strengths_md: 'Negotiation under pressure',
	development_areas_md: 'Delegation',
	notes_md: null,
	kpi_count: 3,
	created_at: '2026-07-01T12:00:00Z',
	updated_at: '2026-07-20T15:00:00Z'
};

const KPI: KpiRead = {
	id: '5b2e6f1a-8c4d-4e7b-a1f3-2d9c0b8e6a47',
	name: 'Contract turnaround time',
	department: 'legal',
	scope: 'department',
	team_member_id: null,
	unit: 'days',
	cadence: 'monthly',
	direction: 'lower_is_better',
	baseline: '14',
	target: '7',
	rationale_md: 'Speed is the number the business feels.',
	latest_period: '2026-06',
	latest_value: '8.5',
	previous_value: '9.2',
	datapoint_count: 6,
	attainment_pct: '82.4',
	created_at: '2026-01-05T09:00:00Z',
	updated_at: '2026-07-01T09:00:00Z'
};

interface FetchSpyLike {
	mock: { calls: unknown[] };
}

function calledUrl(fetchSpy: FetchSpyLike): string {
	return (fetchSpy.mock.calls[0] as [string, RequestInit])[0];
}

function calledInit(fetchSpy: FetchSpyLike): RequestInit {
	return (fetchSpy.mock.calls[0] as [string, RequestInit])[1];
}

describe('management KPIs API', () => {
	beforeEach(() => {
		clearSession();
		setSession({ access_token: 'tok', expires_in: 900 });
		vi.restoreAllMocks();
	});

	afterEach(() => {
		global.fetch = realFetch;
	});

	// ----- team members -----

	it('listTeamMembers hits the bare path with the auth header', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, [MEMBER]));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await listTeamMembers();
		expect(out).toHaveLength(1);
		expect(out[0].kpi_count).toBe(3);
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/team-members$/);
		const headers = calledInit(fetchSpy).headers as Record<string, string>;
		expect(headers.Authorization).toBe('Bearer tok');
	});

	it('createTeamMember POSTs the JSON body', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(201, MEMBER));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await createTeamMember({
			name: 'Priya Raman',
			role_title: 'Senior Counsel, Commercial',
			department: 'legal'
		});
		expect(out.id).toBe(MEMBER.id);
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('POST');
		const parsed = JSON.parse(init.body as string);
		expect(parsed.name).toBe('Priya Raman');
		expect(parsed.department).toBe('legal');
	});

	it('getTeamMember encodes the id and surfaces 404 as LQAIApiError', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, MEMBER));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await getTeamMember('id with space');
		expect(calledUrl(fetchSpy)).toContain('id%20with%20space');

		global.fetch = vi.fn(async () =>
			jsonResponse(404, { detail: { code: 'not_found', message: 'gone' } })
		) as unknown as typeof fetch;
		await expect(getTeamMember('ghost')).rejects.toMatchObject({ status: 404 });
	});

	it('patchTeamMember uses PATCH with a partial body', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, MEMBER));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await patchTeamMember(MEMBER.id, { seniority: 'Director' });
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('PATCH');
		expect(JSON.parse(init.body as string)).toEqual({ seniority: 'Director' });
	});

	it('deleteTeamMember issues a DELETE and tolerates 204', async () => {
		const fetchSpy = vi.fn(async () => emptyResponse(204));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await deleteTeamMember(MEMBER.id);
		expect(calledInit(fetchSpy).method).toBe('DELETE');
		expect(calledUrl(fetchSpy)).toContain(`/management/team-members/${MEMBER.id}`);
	});

	// ----- kpis -----

	it('listKpis with no filters hits the bare path', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, [KPI]));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await listKpis();
		expect(out[0].name).toBe('Contract turnaround time');
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/kpis$/);
	});

	it('listKpis builds department + scope + team_member_id query params', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listKpis({ department: 'compliance', scope: 'individual', teamMemberId: MEMBER.id });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('department=compliance');
		expect(url).toContain('scope=individual');
		expect(url).toContain(`team_member_id=${MEMBER.id}`);
	});

	it('listKpis omits absent filters', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listKpis({ scope: 'department' });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('scope=department');
		expect(url).not.toContain('department=');
		expect(url).not.toContain('team_member_id');
	});

	it('createKpi POSTs the JSON body and surfaces 422 as LQAIApiError', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(201, KPI));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await createKpi({
			name: 'Contract turnaround time',
			department: 'legal',
			scope: 'department',
			unit: 'days',
			cadence: 'monthly',
			direction: 'lower_is_better',
			target: '7'
		});
		expect(out.id).toBe(KPI.id);
		const parsed = JSON.parse(calledInit(fetchSpy).body as string);
		expect(parsed.direction).toBe('lower_is_better');
		expect(parsed.target).toBe('7');

		global.fetch = vi.fn(async () =>
			jsonResponse(422, { detail: { code: 'unprocessable', message: 'bad enum' } })
		) as unknown as typeof fetch;
		await expect(
			createKpi({
				name: '',
				department: 'legal',
				scope: 'department',
				unit: 'days',
				cadence: 'monthly',
				direction: 'lower_is_better'
			})
		).rejects.toBeInstanceOf(LQAIApiError);
	});

	it('getKpi GETs the id path; patchKpi PATCHes a partial body', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, KPI));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await getKpi(KPI.id);
		expect(calledUrl(fetchSpy)).toMatch(new RegExp(`/management/kpis/${KPI.id}$`));

		const patchSpy = vi.fn(async () => jsonResponse(200, { ...KPI, target: '6' }));
		global.fetch = patchSpy as unknown as typeof fetch;
		const out = await patchKpi(KPI.id, { target: '6' });
		expect(out.target).toBe('6');
		expect(calledInit(patchSpy).method).toBe('PATCH');
		expect(JSON.parse(calledInit(patchSpy).body as string)).toEqual({ target: '6' });
	});

	it('deleteKpi issues a DELETE; malformed id surfaces 400', async () => {
		const fetchSpy = vi.fn(async () => emptyResponse(204));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await deleteKpi(KPI.id);
		expect(calledInit(fetchSpy).method).toBe('DELETE');

		global.fetch = vi.fn(async () =>
			jsonResponse(400, { detail: { code: 'bad_request', message: 'malformed id' } })
		) as unknown as typeof fetch;
		await expect(deleteKpi('not-a-uuid')).rejects.toMatchObject({
			status: 400,
			code: 'bad_request'
		});
	});

	// ----- datapoints -----

	it('createDatapoint POSTs to the nested path without overwrite by default', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(201, { period: '2026-07', value: '8.1' }));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await createDatapoint(KPI.id, { period: '2026-07', value: '8.1', note_md: 'July push' });
		const url = calledUrl(fetchSpy);
		expect(url).toMatch(new RegExp(`/management/kpis/${KPI.id}/datapoints$`));
		expect(url).not.toContain('overwrite');
		const parsed = JSON.parse(calledInit(fetchSpy).body as string);
		expect(parsed.period).toBe('2026-07');
		expect(parsed.value).toBe('8.1');
		expect(parsed.note_md).toBe('July push');
	});

	it('createDatapoint appends overwrite=true when requested', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, { period: '2026-07', value: '8.1' }));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await createDatapoint(KPI.id, { period: '2026-07', value: '8.1' }, { overwrite: true });
		expect(calledUrl(fetchSpy)).toContain('/datapoints?overwrite=true');
	});

	it('createDatapoint surfaces a duplicate period as a 409 LQAIApiError', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(409, { detail: { code: 'conflict', message: 'period already recorded' } })
		) as unknown as typeof fetch;
		await expect(createDatapoint(KPI.id, { period: '2026-06', value: '9' })).rejects.toMatchObject({
			status: 409,
			code: 'conflict'
		});
	});

	it('createDatapoint surfaces a cadence-mismatch 422', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(422, {
				detail: [{ msg: 'period must be YYYY-MM for a monthly KPI', type: 'value_error' }]
			})
		) as unknown as typeof fetch;
		await expect(createDatapoint(KPI.id, { period: '2026-Q2', value: '9' })).rejects.toMatchObject({
			status: 422
		});
	});

	it('listDatapoints builds from/to bounds only when provided', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listDatapoints(KPI.id, { from: '2025-01', to: '2026-06' });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('from=2025-01');
		expect(url).toContain('to=2026-06');

		const bareSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = bareSpy as unknown as typeof fetch;
		await listDatapoints(KPI.id);
		expect(calledUrl(bareSpy)).toMatch(new RegExp(`/management/kpis/${KPI.id}/datapoints$`));
	});

	it('getSeries returns the kpi + ascending datapoints', async () => {
		const fetchSpy = vi.fn(async () =>
			jsonResponse(200, {
				kpi: KPI,
				datapoints: [
					{ period: '2026-05', value: '9.2' },
					{ period: '2026-06', value: '8.5' }
				]
			})
		);
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await getSeries(KPI.id);
		expect(out.kpi.id).toBe(KPI.id);
		expect(out.datapoints).toHaveLength(2);
		expect(calledUrl(fetchSpy)).toMatch(new RegExp(`/management/kpis/${KPI.id}/series$`));
	});

	// ----- dashboard -----

	it('getDashboard GETs the rollup and returns both sections', async () => {
		const dashboard: KpiDashboard = {
			departments: { legal: [KPI], compliance: [] },
			team: [{ member: MEMBER, kpis: [] }]
		};
		const fetchSpy = vi.fn(async () => jsonResponse(200, dashboard));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await getDashboard();
		expect(out.departments.legal).toHaveLength(1);
		expect(out.team[0].member.name).toBe('Priya Raman');
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/dashboard$/);
	});

	it('getDashboard propagates a plain-string error detail', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(500, { detail: 'boom' })
		) as unknown as typeof fetch;
		await expect(getDashboard()).rejects.toMatchObject({ status: 500, message: 'boom' });
	});
});
