/**
 * Unit tests for the stakeholders API client (Management tab).
 *
 * Mocks `fetch` so the calls don't escape the test runner. Mirrors the
 * shape of saved-prompts-api.test.ts: query-param building (space /
 * needs_attention / rollup filters), method + body assertions, URL
 * encoding, and typed-error propagation through the shared client.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	createCommitment,
	createInteraction,
	createPosition,
	createStakeholder,
	deleteStakeholder,
	getStakeholder,
	listCommitmentRollup,
	listCommitments,
	listInteractions,
	listPositions,
	listStakeholders,
	patchCommitment,
	patchStakeholder
} from '../api/stakeholders';
import type { Stakeholder, StakeholderCommitmentRollupRow } from '../types';
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

const SAMPLE: Stakeholder = {
	id: '9f0c7d2e-51a4-4b7b-a6a4-0f4f7f2f9d11',
	owner_id: '11111111-1111-1111-1111-111111111111',
	full_name: 'Margaret Chen',
	stakeholder_type: 'board_chair',
	organization: 'Meridian Capital',
	role_title: 'Board Chair',
	committee_seats: 'Audit, Compensation',
	overall_health: 'solid',
	cadence_target_days: 30,
	last_interaction_at: '2026-07-20T15:00:00Z',
	days_since_last_interaction: 4,
	open_commitments_count: 2,
	created_at: '2026-07-01T12:00:00Z',
	updated_at: '2026-07-20T15:00:00Z'
};

const ROLLUP_ROW: StakeholderCommitmentRollupRow = {
	id: '4c1de1cc-9a2f-45e1-8a01-3a3f6a1a2b3c',
	stakeholder_id: SAMPLE.id,
	full_name: SAMPLE.full_name,
	stakeholder_type: 'board_chair',
	direction: 'we_owe',
	description: 'Send the D&O renewal comparison',
	due_date: '2026-07-28',
	status: 'open'
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

describe('stakeholders API', () => {
	beforeEach(() => {
		clearSession();
		setSession({ access_token: 'tok', expires_in: 900 });
		vi.restoreAllMocks();
	});

	afterEach(() => {
		global.fetch = realFetch;
	});

	// ----- registry -----

	it('listStakeholders with no filters hits the bare path', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, [SAMPLE]));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await listStakeholders();
		expect(out).toHaveLength(1);
		expect(out[0].full_name).toBe('Margaret Chen');
		expect(calledUrl(fetchSpy)).toMatch(/\/stakeholders$/);
	});

	it('listStakeholders builds space + needs_attention query params', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listStakeholders({ space: 'c-suite', needsAttention: true });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('space=c-suite');
		expect(url).toContain('needs_attention=true');
	});

	it('listStakeholders omits needs_attention when false and passes stakeholder_type', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listStakeholders({ stakeholderType: 'regulator', needsAttention: false });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('stakeholder_type=regulator');
		expect(url).not.toContain('needs_attention');
		expect(url).not.toContain('space=');
	});

	it('listStakeholders attaches Authorization header', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listStakeholders();
		const headers = calledInit(fetchSpy).headers as Record<string, string>;
		expect(headers.Authorization).toBe('Bearer tok');
	});

	it('createStakeholder POSTs JSON body and returns the created row', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(201, SAMPLE));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await createStakeholder({
			full_name: 'Margaret Chen',
			stakeholder_type: 'board_chair',
			committee_seats: 'Audit, Compensation'
		});
		expect(out.id).toBe(SAMPLE.id);
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('POST');
		const parsed = JSON.parse(init.body as string);
		expect(parsed.full_name).toBe('Margaret Chen');
		expect(parsed.stakeholder_type).toBe('board_chair');
	});

	it('createStakeholder surfaces 422 as LQAIApiError', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(422, { detail: { code: 'unprocessable', message: 'bad type' } })
		) as unknown as typeof fetch;
		await expect(
			createStakeholder({ full_name: '', stakeholder_type: 'other' })
		).rejects.toBeInstanceOf(LQAIApiError);
	});

	it('getStakeholder encodes the id and surfaces 404 as LQAIApiError', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, SAMPLE));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await getStakeholder('id with space');
		expect(calledUrl(fetchSpy)).toContain('id%20with%20space');

		global.fetch = vi.fn(async () =>
			jsonResponse(404, { detail: { code: 'not_found', message: 'gone' } })
		) as unknown as typeof fetch;
		await expect(getStakeholder('ghost')).rejects.toMatchObject({ status: 404 });
	});

	it('patchStakeholder uses PATCH with a partial body', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, SAMPLE));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await patchStakeholder(SAMPLE.id, { overall_health: 'red' });
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('PATCH');
		expect(JSON.parse(init.body as string)).toEqual({ overall_health: 'red' });
	});

	it('deleteStakeholder issues a DELETE and tolerates 204', async () => {
		const fetchSpy = vi.fn(async () => emptyResponse(204));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await deleteStakeholder(SAMPLE.id);
		expect(calledInit(fetchSpy).method).toBe('DELETE');
	});

	// ----- interactions / commitments / positions -----

	it('createInteraction POSTs to the nested path', async () => {
		const fetchSpy = vi.fn(async () =>
			jsonResponse(201, {
				id: 'i1',
				occurred_at: '2026-07-20T15:00:00Z',
				channel: 'board_meeting',
				summary_md: 'Q2 audit pre-read walkthrough'
			})
		);
		global.fetch = fetchSpy as unknown as typeof fetch;
		await createInteraction(SAMPLE.id, {
			occurred_at: '2026-07-20T15:00:00Z',
			channel: 'board_meeting',
			summary_md: 'Q2 audit pre-read walkthrough'
		});
		expect(calledUrl(fetchSpy)).toContain(`/stakeholders/${SAMPLE.id}/interactions`);
		expect(calledInit(fetchSpy).method).toBe('POST');
	});

	it('listInteractions GETs the nested path', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listInteractions(SAMPLE.id);
		expect(calledUrl(fetchSpy)).toMatch(new RegExp(`/stakeholders/${SAMPLE.id}/interactions$`));
	});

	it('createCommitment POSTs direction + description', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(201, ROLLUP_ROW));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await createCommitment(SAMPLE.id, {
			direction: 'we_owe',
			description: 'Send the D&O renewal comparison',
			due_date: '2026-07-28'
		});
		const parsed = JSON.parse(calledInit(fetchSpy).body as string);
		expect(parsed.direction).toBe('we_owe');
		expect(parsed.due_date).toBe('2026-07-28');
	});

	it('listCommitments GETs the nested path', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listCommitments(SAMPLE.id);
		expect(calledUrl(fetchSpy)).toMatch(new RegExp(`/stakeholders/${SAMPLE.id}/commitments$`));
	});

	it('listPositions passes latest=true only when requested', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listPositions(SAMPLE.id, { latest: true });
		expect(calledUrl(fetchSpy)).toContain('latest=true');

		const fetchSpy2 = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy2 as unknown as typeof fetch;
		await listPositions(SAMPLE.id);
		expect(calledUrl(fetchSpy2)).not.toContain('latest');
	});

	it('createPosition POSTs topic + stance + as_of', async () => {
		const fetchSpy = vi.fn(async () =>
			jsonResponse(201, {
				id: 'p1',
				topic: 'Series C terms',
				stance: 'skeptical',
				as_of: '2026-07-24'
			})
		);
		global.fetch = fetchSpy as unknown as typeof fetch;
		await createPosition(SAMPLE.id, {
			topic: 'Series C terms',
			stance: 'skeptical',
			as_of: '2026-07-24'
		});
		const parsed = JSON.parse(calledInit(fetchSpy).body as string);
		expect(parsed.topic).toBe('Series C terms');
		expect(parsed.stance).toBe('skeptical');
		expect(parsed.as_of).toBe('2026-07-24');
	});

	// ----- rollup -----

	it('listCommitmentRollup builds status + direction query params', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, [ROLLUP_ROW]));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await listCommitmentRollup({ status: 'open', direction: 'we_owe' });
		expect(out[0].full_name).toBe('Margaret Chen');
		const url = calledUrl(fetchSpy);
		expect(url).toContain('/stakeholder-commitments?');
		expect(url).toContain('status=open');
		expect(url).toContain('direction=we_owe');
	});

	it('listCommitmentRollup with no filters hits the bare path', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listCommitmentRollup();
		expect(calledUrl(fetchSpy)).toMatch(/\/stakeholder-commitments$/);
	});

	it('patchCommitment PATCHes the flat rollup path', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, { ...ROLLUP_ROW, status: 'done' }));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await patchCommitment(ROLLUP_ROW.id, { status: 'done' });
		expect(out.status).toBe('done');
		expect(calledUrl(fetchSpy)).toContain(`/stakeholder-commitments/${ROLLUP_ROW.id}`);
		expect(calledInit(fetchSpy).method).toBe('PATCH');
		expect(JSON.parse(calledInit(fetchSpy).body as string)).toEqual({ status: 'done' });
	});

	it('patchCommitment surfaces 400 on malformed id as LQAIApiError', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(400, { detail: { code: 'bad_request', message: 'malformed id' } })
		) as unknown as typeof fetch;
		await expect(patchCommitment('not-a-uuid', { status: 'done' })).rejects.toMatchObject({
			status: 400,
			code: 'bad_request'
		});
	});
});
