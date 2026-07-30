/**
 * Unit tests for the Management documents API client.
 *
 * Mocks `fetch` so the calls don't escape the test runner. Mirrors
 * managementKpisApi.test.ts: filter query-param building (doc_type / q /
 * date range), method + body assertions, URL encoding, and typed-error
 * propagation through the shared client (404 / 422).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	createDocument,
	deleteDocument,
	getDocument,
	listDocuments,
	patchDocument
} from '../api/managementDocuments';
import type { MgmtDocument, MgmtDocumentListRow } from '../types';
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

const ROW: MgmtDocumentListRow = {
	id: '9c4b7a2e-1d5f-4e8a-b3c6-7f0a9d8e2b41',
	owner_id: 'a7c9d8e2-4b31-4f6a-9d2e-8b1c3f5a7e90',
	title: 'Q2 2026 board pack — legal section',
	doc_type: 'board_pack',
	doc_date: '2026-06-12',
	author: 'GC office',
	related_tags: 'board, q2-2026',
	created_at: '2026-06-12T10:00:00Z',
	updated_at: '2026-06-12T10:00:00Z',
	deleted_at: null,
	content_chars: 2143
};

const DOC: MgmtDocument = {
	...ROW,
	content_md: '# Legal section\n\nMatters, spend, risk.'
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

describe('management documents API', () => {
	beforeEach(() => {
		clearSession();
		setSession({ access_token: 'tok', expires_in: 900 });
		vi.restoreAllMocks();
	});

	afterEach(() => {
		global.fetch = realFetch;
	});

	// ----- list + filters -----

	it('listDocuments with no filters hits the bare path with the auth header', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, [ROW]));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await listDocuments();
		expect(out).toHaveLength(1);
		expect(out[0].content_chars).toBe(2143);
		expect(calledUrl(fetchSpy)).toMatch(/\/management\/documents$/);
		const headers = calledInit(fetchSpy).headers as Record<string, string>;
		expect(headers.Authorization).toBe('Bearer tok');
	});

	it('listDocuments builds doc_type + q + date range query params', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listDocuments({
			docType: 'board_pack',
			q: 'series c',
			dateFrom: '2026-01-01',
			dateTo: '2026-06-30'
		});
		const url = calledUrl(fetchSpy);
		expect(url).toContain('doc_type=board_pack');
		expect(url).toContain('q=series+c');
		expect(url).toContain('date_from=2026-01-01');
		expect(url).toContain('date_to=2026-06-30');
	});

	it('listDocuments omits absent filters', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, []));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await listDocuments({ q: 'minutes' });
		const url = calledUrl(fetchSpy);
		expect(url).toContain('q=minutes');
		expect(url).not.toContain('doc_type');
		expect(url).not.toContain('date_from');
		expect(url).not.toContain('date_to');
	});

	// ----- create -----

	it('createDocument POSTs the JSON body', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(201, DOC));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await createDocument({
			title: 'Q2 2026 board pack — legal section',
			doc_type: 'board_pack',
			content_md: '# Legal section\n\nMatters, spend, risk.',
			doc_date: '2026-06-12',
			author: 'GC office',
			related_tags: 'board, q2-2026'
		});
		expect(out.id).toBe(DOC.id);
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('POST');
		const parsed = JSON.parse(init.body as string);
		expect(parsed.doc_type).toBe('board_pack');
		expect(parsed.content_md).toContain('# Legal section');
		expect(parsed.doc_date).toBe('2026-06-12');
	});

	it('createDocument surfaces a validation failure as a 422 LQAIApiError', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(422, { detail: { code: 'unprocessable', message: 'title too long' } })
		) as unknown as typeof fetch;
		await expect(
			createDocument({ title: 'x'.repeat(400), doc_type: 'minutes', content_md: 'body' })
		).rejects.toBeInstanceOf(LQAIApiError);
	});

	// ----- read -----

	it('getDocument encodes the id and returns the full body', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, DOC));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await getDocument('id with space');
		expect(out.content_md).toContain('Matters, spend, risk.');
		expect(calledUrl(fetchSpy)).toContain('/management/documents/id%20with%20space');
	});

	it('getDocument surfaces 404 (cross-user or deleted) as LQAIApiError', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(404, { detail: { code: 'not_found', message: 'gone' } })
		) as unknown as typeof fetch;
		await expect(getDocument('ghost')).rejects.toMatchObject({
			status: 404,
			code: 'not_found'
		});
	});

	// ----- update + delete -----

	it('patchDocument uses PATCH with a partial body (null clears)', async () => {
		const fetchSpy = vi.fn(async () => jsonResponse(200, { ...DOC, author: null }));
		global.fetch = fetchSpy as unknown as typeof fetch;
		const out = await patchDocument(DOC.id, { author: null });
		expect(out.author).toBeNull();
		const init = calledInit(fetchSpy);
		expect(init.method).toBe('PATCH');
		expect(JSON.parse(init.body as string)).toEqual({ author: null });
	});

	it('deleteDocument issues a DELETE and tolerates 204', async () => {
		const fetchSpy = vi.fn(async () => emptyResponse(204));
		global.fetch = fetchSpy as unknown as typeof fetch;
		await deleteDocument(DOC.id);
		expect(calledInit(fetchSpy).method).toBe('DELETE');
		expect(calledUrl(fetchSpy)).toContain(`/management/documents/${DOC.id}`);
	});

	it('deleteDocument propagates 404 for an already-removed row', async () => {
		global.fetch = vi.fn(async () =>
			jsonResponse(404, { detail: { code: 'not_found', message: 'gone' } })
		) as unknown as typeof fetch;
		await expect(deleteDocument('ghost')).rejects.toMatchObject({ status: 404 });
	});
});
