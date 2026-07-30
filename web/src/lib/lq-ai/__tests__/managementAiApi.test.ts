/**
 * Unit tests for the Management AI API client.
 *
 * Mocks `fetch` so the calls don't escape the test runner. Mirrors
 * managementKpisApi.test.ts: query-param building (job list filters),
 * method + body assertions per job type, URL encoding, typed-error
 * propagation (404 / 422), and the pollAiJob helper under fake timers
 * (resolves on done, resolves-and-stops on error, rejects on timeout).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	AiJobTimeoutError,
	createAiJob,
	getAiJob,
	getWizardQuestions,
	listAiJobs,
	pollAiJob
} from '../api/managementAi';
import type { MgmtAiJob, MgmtAiJobStatus } from '../types';
import { clearSession, setSession } from '../auth/store';
import { LQAIApiError } from '../api/client';

const realFetch = global.fetch;

function jsonResponse(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

const JOB_ID = '3f8a1c2b-9d4e-4a6f-b7c8-0e1d2a3b4c5d';
const STAKEHOLDER_ID = 'a7c9d8e2-4b31-4f6a-9d2e-8b1c3f5a7e90';
const MEMBER_ID = '5b2e6f1a-8c4d-4e7b-a1f3-2d9c0b8e6a47';

function jobWith(status: MgmtAiJobStatus, extra: Partial<MgmtAiJob> = {}): MgmtAiJob {
	return {
		id: JOB_ID,
		job_type: 'pre_meeting_brief',
		status,
		stakeholder_id: STAKEHOLDER_ID,
		team_member_id: null,
		created_at: '2026-07-25T09:00:00Z',
		completed_at: status === 'done' || status === 'error' ? '2026-07-25T09:01:00Z' : null,
		result_md: status === 'done' ? '# Brief\n\nReady. [kpi: Contract turnaround time]' : null,
		result_json: null,
		error: status === 'error' ? 'The model was unavailable.' : null,
		params: null,
		...extra
	};
}

interface FetchSpyLike {
	mock: { calls: unknown[] };
}

function calledUrl(fetchSpy: FetchSpyLike, call = 0): string {
	return (fetchSpy.mock.calls[call] as [string, RequestInit])[0];
}

function calledInit(fetchSpy: FetchSpyLike, call = 0): RequestInit {
	return (fetchSpy.mock.calls[call] as [string, RequestInit])[1];
}

describe('management AI API', () => {
	beforeEach(() => {
		clearSession();
		setSession({ access_token: 'tok', expires_in: 900 });
		vi.restoreAllMocks();
	});

	afterEach(() => {
		global.fetch = realFetch;
		vi.useRealTimers();
	});

	// ----- wizard questions -----

	it('getWizardQuestions GETs the static script with the auth header', async () => {
		const fetchSpy = vi.fn(async () =>
			jsonResponse(200, {
				questions: [
					{ id: 'a1', section: 'A', prompt: 'What must legal deliver?', hint: '', optional: false }
				]
			})
		);
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await getWizardQuestions();
		expect(out.questions).toHaveLength(1);
		expect(out.questions[0].section).toBe('A');
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/kpi-wizard\/questions$/);
		const headers = calledInit(fetchSpy).headers as Record<string, string>;
		expect(headers.Authorization).toBe('Bearer tok');
	});

	// ----- job creation -----

	it('createAiJob POSTs a pre_meeting_brief body with the stakeholder id', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(202, jobWith('pending')));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await createAiJob({
			job_type: 'pre_meeting_brief',
			stakeholder_id: STAKEHOLDER_ID
		});
		expect(out.id).toBe(JOB_ID);
		expect(out.status).toBe('pending');
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('POST');
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/ai-jobs$/);
		const parsed = JSON.parse(init.body as string);
		expect(parsed).toEqual({ job_type: 'pre_meeting_brief', stakeholder_id: STAKEHOLDER_ID });
	});

	it('createAiJob POSTs a review_prep body with the team member id', async () => {
		const fetchSpy = vi.fn(async () =>
			jsonResponse(
				202,
				jobWith('pending', {
					job_type: 'review_prep',
					stakeholder_id: null,
					team_member_id: MEMBER_ID
				})
			)
		);
		global.fetch = fetchSpy as unknown as typeof fetch;
		await createAiJob({ job_type: 'review_prep', team_member_id: MEMBER_ID });
		expect(JSON.parse(calledInit(fetchSpy).body as string)).toEqual({
			job_type: 'review_prep',
			team_member_id: MEMBER_ID
		});
	});

	it('createAiJob POSTs a kpi_draft body carrying the answers', async () => {
		const fetchSpy = vi.fn(async () =>
			jsonResponse(202, jobWith('pending', { job_type: 'kpi_draft', stakeholder_id: null }))
		);
		global.fetch = fetchSpy as unknown as typeof fetch;
		await createAiJob({
			job_type: 'kpi_draft',
			answers: [{ question_id: 'a1', answer: 'Close deals faster without waiving protections.' }]
		});
		const parsed = JSON.parse(calledInit(fetchSpy).body as string);
		expect(parsed.job_type).toBe('kpi_draft');
		expect(parsed.answers).toEqual([
			{ question_id: 'a1', answer: 'Close deals faster without waiving protections.' }
		]);
	});

	it('createAiJob surfaces a pairing violation as a 422 LQAIApiError', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(422, {
				detail: { code: 'unprocessable', message: "job_type='kpi_draft' requires answers" }
			})
		) as unknown as typeof fetch;
		await expect(createAiJob({ job_type: 'kpi_draft' })).rejects.toBeInstanceOf(LQAIApiError);
		await expect(createAiJob({ job_type: 'kpi_draft' })).rejects.toMatchObject({ status: 422 });
	});

	it('createAiJob surfaces an unknown subject as a 404', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(404, { detail: { code: 'not_found', message: 'Stakeholder ghost not found.' } })
		) as unknown as typeof fetch;
		await expect(
			createAiJob({ job_type: 'pre_meeting_brief', stakeholder_id: 'ghost' })
		).rejects.toMatchObject({ status: 404, code: 'not_found' });
	});

	// ----- job read + list -----

	it('getAiJob encodes the id and returns the row', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, jobWith('running')));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await getAiJob('id with space');
		expect(out.status).toBe('running');
		expect(calledUrl(fetchSpy)).toContain('/management/ai-jobs/id%20with%20space');
	});

	it('listAiJobs with no filters hits the bare path', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, [jobWith('done')]));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await listAiJobs();
		expect(out).toHaveLength(1);
		expect(out[0].result_md).toContain('Brief');
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/ai-jobs$/);
	});

	it('listAiJobs builds job_type + stakeholder_id + team_member_id query params', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listAiJobs({
			jobType: 'review_prep',
			stakeholderId: STAKEHOLDER_ID,
			teamMemberId: MEMBER_ID
		});
		const url = calledUrl(fetchSpy);
		expect(url).toContain('job_type=review_prep');
		expect(url).toContain(`stakeholder_id=${STAKEHOLDER_ID}`);
		expect(url).toContain(`team_member_id=${MEMBER_ID}`);
	});

	it('listAiJobs omits absent filters', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listAiJobs({ jobType: 'pre_meeting_brief' });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('job_type=pre_meeting_brief');
		expect(url).not.toContain('stakeholder_id');
		expect(url).not.toContain('team_member_id');
	});

	it('listAiJobs propagates a plain-string error detail', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(500, { detail: 'boom' })
		) as unknown as typeof fetch;
		await expect(listAiJobs()).rejects.toMatchObject({ status: 500, message: 'boom' });
	});

	// ----- pollAiJob (fake timers) -----

	it('pollAiJob polls until done, reporting every row via onUpdate', async () => {
		vi.useFakeTimers();
		const statuses: MgmtAiJobStatus[] = ['pending', 'running', 'done'];
		let call = 0;
		const fetchSpy = vi.fn(async () =>
			jsonResponse(200, jobWith(statuses[Math.min(call++, statuses.length - 1)]))
		);
		global.fetch = fetchSpy as unknown as typeof fetch;

		const onUpdate = vi.fn();
		const promise = pollAiJob(JOB_ID, { intervalMs: 2000, timeoutMs: 60000, onUpdate });
		await vi.advanceTimersByTimeAsync(0); // first poll (pending)
		await vi.advanceTimersByTimeAsync(2000); // second poll (running)
		await vi.advanceTimersByTimeAsync(2000); // third poll (done)
		const job = await promise;

		expect(job.status).toBe('done');
		expect(job.result_md).toContain('[kpi: Contract turnaround time]');
		expect(fetchSpy).toHaveBeenCalledTimes(3);
		expect(onUpdate).toHaveBeenCalledTimes(3);
		expect(onUpdate.mock.calls[0][0].status).toBe('pending');
		expect(onUpdate.mock.calls[2][0].status).toBe('done');
	});

	it('pollAiJob resolves on error status and stops polling', async () => {
		vi.useFakeTimers();
		let call = 0;
		const fetchSpy = vi.fn(async () =>
			jsonResponse(200, call++ === 0 ? jobWith('running') : jobWith('error'))
		);
		global.fetch = fetchSpy as unknown as typeof fetch;

		const promise = pollAiJob(JOB_ID, { intervalMs: 2000, timeoutMs: 60000 });
		await vi.advanceTimersByTimeAsync(0);
		await vi.advanceTimersByTimeAsync(2000);
		const job = await promise;

		expect(job.status).toBe('error');
		expect(job.error).toBe('The model was unavailable.');
		// Terminal — no further polls even as time keeps passing.
		await vi.advanceTimersByTimeAsync(20000);
		expect(fetchSpy).toHaveBeenCalledTimes(2);
	});

	it('pollAiJob rejects with AiJobTimeoutError once the budget is exhausted', async () => {
		vi.useFakeTimers();
		const fetchSpy = vi.fn(async () => jsonResponse(200, jobWith('running')));
		global.fetch = fetchSpy as unknown as typeof fetch;

		const promise = pollAiJob(JOB_ID, { intervalMs: 2000, timeoutMs: 5000 });
		const expectation = expect(promise).rejects.toBeInstanceOf(AiJobTimeoutError);
		await vi.advanceTimersByTimeAsync(0); // poll 1 (waited 0)
		await vi.advanceTimersByTimeAsync(2000); // poll 2 (waited 2000)
		await vi.advanceTimersByTimeAsync(2000); // poll 3 (waited 4000 → next wait would exceed)
		await expectation;
		expect(fetchSpy).toHaveBeenCalledTimes(3);
	});

	it('pollAiJob propagates a fetch-level error (404) instead of retrying forever', async () => {
		vi.useFakeTimers();
		global.fetch = vi.fn(async () =>
			jsonResponse(404, { detail: { code: 'not_found', message: 'gone' } })
		) as unknown as typeof fetch;

		const promise = pollAiJob(JOB_ID, { intervalMs: 2000, timeoutMs: 60000 });
		const expectation = expect(promise).rejects.toMatchObject({ status: 404 });
		await vi.advanceTimersByTimeAsync(0);
		await expectation;
	});
});
