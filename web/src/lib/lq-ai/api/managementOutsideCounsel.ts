/**
 * /api/v1/management/outside-counsel — the Management tab's Outside Counsel module.
 *
 * Firms (with the partners the GC chose), quarterly budgets, line-item
 * invoices, the value ledger, and the one-call summary dashboard. The
 * GC's policy thresholds (10% minimum discount, 5% maximum increase,
 * two billers per task, 110%/125% budget bands) are applied server-side;
 * the client renders what the server computed.
 *
 * All money and percentages travel as JSON strings (backend Decimals) —
 * parse with Number() only for math/plotting; display from the string.
 */
import { apiRequest } from './client';
import type {
	OcBudget,
	OcBudgetUpsert,
	OcFirm,
	OcFirmCreate,
	OcFirmUpdate,
	OcInvoice,
	OcInvoiceCreate,
	OcInvoiceUpdate,
	OcPartner,
	OcPartnerCreate,
	OcPartnerUpdate,
	OcSummary,
	OcValueCategory,
	OcValueEntry,
	OcValueEntryCreate,
	OcValueEntryUpdate
} from '../types';

const BASE = '/management/outside-counsel';
const id = (value: string) => encodeURIComponent(value);

function query(params: Record<string, string | number | undefined>): string {
	const qs = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== '') qs.set(key, String(value));
	}
	const s = qs.toString();
	return s ? `?${s}` : '';
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

/** GET /summary — the module dashboard for one year (default: this year). */
export async function getSummary(year?: number): Promise<OcSummary> {
	return apiRequest<OcSummary>(`${BASE}/summary${query({ year })}`);
}

// ---------------------------------------------------------------------------
// Firms and partners
// ---------------------------------------------------------------------------

/** GET /firms — firms with chosen partners, rate flags and spend totals. */
export async function listFirms(): Promise<OcFirm[]> {
	return apiRequest<OcFirm[]>(`${BASE}/firms`);
}

/** POST /firms (201). */
export async function createFirm(body: OcFirmCreate): Promise<OcFirm> {
	return apiRequest<OcFirm>(`${BASE}/firms`, { method: 'POST', body });
}

/** PATCH /firms/{id}. */
export async function patchFirm(firmId: string, body: OcFirmUpdate): Promise<OcFirm> {
	return apiRequest<OcFirm>(`${BASE}/firms/${id(firmId)}`, { method: 'PATCH', body });
}

/** DELETE /firms/{id} (soft delete — its invoices drop out of every total). */
export async function deleteFirm(firmId: string): Promise<void> {
	await apiRequest<void>(`${BASE}/firms/${id(firmId)}`, { method: 'DELETE' });
}

/** POST /firms/{id}/partners (201) — record a partner the GC chose. */
export async function addPartner(firmId: string, body: OcPartnerCreate): Promise<OcPartner> {
	return apiRequest<OcPartner>(`${BASE}/firms/${id(firmId)}/partners`, {
		method: 'POST',
		body
	});
}

/**
 * PATCH /partners/{id}. `status: 'left_firm'` raises a red Urgent Matters
 * item until resolved with `'replaced'` or `'followed'`.
 */
export async function patchPartner(partnerId: string, body: OcPartnerUpdate): Promise<OcPartner> {
	return apiRequest<OcPartner>(`${BASE}/partners/${id(partnerId)}`, { method: 'PATCH', body });
}

/** DELETE /partners/{id} (soft). */
export async function deletePartner(partnerId: string): Promise<void> {
	await apiRequest<void>(`${BASE}/partners/${id(partnerId)}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Budgets
// ---------------------------------------------------------------------------

/** GET /budgets — optionally one year. */
export async function listBudgets(year?: number): Promise<OcBudget[]> {
	return apiRequest<OcBudget[]>(`${BASE}/budgets${query({ year })}`);
}

/** PUT /budgets — insert or replace the budget for one quarter and area. */
export async function upsertBudget(body: OcBudgetUpsert): Promise<OcBudget> {
	return apiRequest<OcBudget>(`${BASE}/budgets`, { method: 'PUT', body });
}

/** DELETE /budgets/{id}. */
export async function deleteBudget(budgetId: string): Promise<void> {
	await apiRequest<void>(`${BASE}/budgets/${id(budgetId)}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Invoices
// ---------------------------------------------------------------------------

/** GET /invoices — newest first, with lines and staffing flags. */
export async function listInvoices(
	opts: { firmId?: string; period?: string; year?: number } = {}
): Promise<OcInvoice[]> {
	return apiRequest<OcInvoice[]>(
		`${BASE}/invoices${query({ firm_id: opts.firmId, period: opts.period, year: opts.year })}`
	);
}

/** POST /invoices (201) — line by line; the total is computed server-side. */
export async function createInvoice(body: OcInvoiceCreate): Promise<OcInvoice> {
	return apiRequest<OcInvoice>(`${BASE}/invoices`, { method: 'POST', body });
}

/** PATCH /invoices/{id} — `lines`, when present, replaces all lines. */
export async function patchInvoice(invoiceId: string, body: OcInvoiceUpdate): Promise<OcInvoice> {
	return apiRequest<OcInvoice>(`${BASE}/invoices/${id(invoiceId)}`, { method: 'PATCH', body });
}

/** DELETE /invoices/{id} (soft). */
export async function deleteInvoice(invoiceId: string): Promise<void> {
	await apiRequest<void>(`${BASE}/invoices/${id(invoiceId)}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Value ledger
// ---------------------------------------------------------------------------

/** GET /value-entries — optionally one year and/or one category. */
export async function listValueEntries(
	opts: { year?: number; category?: OcValueCategory } = {}
): Promise<OcValueEntry[]> {
	return apiRequest<OcValueEntry[]>(
		`${BASE}/value-entries${query({ year: opts.year, category: opts.category })}`
	);
}

/** POST /value-entries (201) — `method_note` and `source` are mandatory. */
export async function createValueEntry(body: OcValueEntryCreate): Promise<OcValueEntry> {
	return apiRequest<OcValueEntry>(`${BASE}/value-entries`, { method: 'POST', body });
}

/** PATCH /value-entries/{id}. */
export async function patchValueEntry(
	entryId: string,
	body: OcValueEntryUpdate
): Promise<OcValueEntry> {
	return apiRequest<OcValueEntry>(`${BASE}/value-entries/${id(entryId)}`, {
		method: 'PATCH',
		body
	});
}

/** DELETE /value-entries/{id} (soft). */
export async function deleteValueEntry(entryId: string): Promise<void> {
	await apiRequest<void>(`${BASE}/value-entries/${id(entryId)}`, { method: 'DELETE' });
}
