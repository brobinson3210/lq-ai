/**
 * /api/v1/management/documents — the Management tab's document space.
 *
 * Inline-markdown documents (board packs, minutes, memos) with a metadata
 * list (no bodies — `content_chars` instead), a full detail read, partial
 * PATCH, and 204 soft delete. Per-user isolation: cross-user reads 404.
 */
import { apiRequest } from './client';
import type {
	MgmtDocument,
	MgmtDocumentCreate,
	MgmtDocumentListRow,
	MgmtDocumentUpdate
} from '../types';

/**
 * GET /api/v1/management/documents — metadata list, newest doc_date first
 * (nulls last).
 *
 * Filters: ``doc_type`` exact; ``q`` case-insensitive substring over
 * title / author / tags (not the body); ``date_from`` / ``date_to``
 * inclusive doc_date bounds (YYYY-MM-DD).
 */
export async function listDocuments(
	opts: {
		docType?: string;
		q?: string;
		dateFrom?: string;
		dateTo?: string;
	} = {}
): Promise<MgmtDocumentListRow[]> {
	const params = new URLSearchParams();
	if (opts.docType) params.set('doc_type', opts.docType);
	if (opts.q) params.set('q', opts.q);
	if (opts.dateFrom) params.set('date_from', opts.dateFrom);
	if (opts.dateTo) params.set('date_to', opts.dateTo);
	const qs = params.toString();
	return apiRequest<MgmtDocumentListRow[]>(`/management/documents${qs ? `?${qs}` : ''}`);
}

/** POST /api/v1/management/documents (201). */
export async function createDocument(body: MgmtDocumentCreate): Promise<MgmtDocument> {
	return apiRequest<MgmtDocument>('/management/documents', { method: 'POST', body });
}

/** GET /api/v1/management/documents/{id} — full read including content_md. */
export async function getDocument(id: string): Promise<MgmtDocument> {
	return apiRequest<MgmtDocument>(`/management/documents/${encodeURIComponent(id)}`);
}

/** PATCH /api/v1/management/documents/{id} (partial update; null clears). */
export async function patchDocument(id: string, body: MgmtDocumentUpdate): Promise<MgmtDocument> {
	return apiRequest<MgmtDocument>(`/management/documents/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body
	});
}

/** DELETE /api/v1/management/documents/{id} (204 soft delete). */
export async function deleteDocument(id: string): Promise<void> {
	await apiRequest<void>(`/management/documents/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
