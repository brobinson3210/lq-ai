/**
 * /api/v1/management — the Management tab's KPIs & OKRs module.
 *
 * Team-member roster CRUD, KPI catalog CRUD (department + individual scope),
 * per-KPI datapoints (with the duplicate-period overwrite path), the full
 * series for charting, and the cross-department dashboard rollup.
 *
 * All numeric values travel as JSON strings (backend Decimals) — parse with
 * Number() only for math/plotting; display from the string.
 */
import { apiRequest } from './client';
import type {
	KpiCadence,
	KpiCreate,
	KpiDashboard,
	KpiDatapoint,
	KpiDatapointCreate,
	KpiDepartment,
	KpiRead,
	KpiScope,
	KpiSeries,
	KpiUpdate,
	TeamMemberCreate,
	TeamMemberRead,
	TeamMemberUpdate
} from '../types';

// Re-export for convenient consumption alongside the client functions.
export type { KpiCadence, KpiDepartment, KpiScope };

// ---------------------------------------------------------------------------
// Team members (roster)
// ---------------------------------------------------------------------------

/** GET /api/v1/management/team-members. */
export async function listTeamMembers(): Promise<TeamMemberRead[]> {
	return apiRequest<TeamMemberRead[]>('/management/team-members');
}

/** POST /api/v1/management/team-members (201). */
export async function createTeamMember(body: TeamMemberCreate): Promise<TeamMemberRead> {
	return apiRequest<TeamMemberRead>('/management/team-members', { method: 'POST', body });
}

/** GET /api/v1/management/team-members/{id}. */
export async function getTeamMember(id: string): Promise<TeamMemberRead> {
	return apiRequest<TeamMemberRead>(`/management/team-members/${encodeURIComponent(id)}`);
}

/** PATCH /api/v1/management/team-members/{id} (partial update). */
export async function patchTeamMember(id: string, body: TeamMemberUpdate): Promise<TeamMemberRead> {
	return apiRequest<TeamMemberRead>(`/management/team-members/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body
	});
}

/** DELETE /api/v1/management/team-members/{id} (soft delete). */
export async function deleteTeamMember(id: string): Promise<void> {
	await apiRequest<void>(`/management/team-members/${encodeURIComponent(id)}`, {
		method: 'DELETE'
	});
}

// ---------------------------------------------------------------------------
// KPIs
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/management/kpis — list the caller's KPIs.
 *
 * Query parameters: ``department``, ``scope``, ``team_member_id``.
 */
export async function listKpis(
	opts: {
		department?: KpiDepartment;
		scope?: KpiScope;
		teamMemberId?: string;
	} = {}
): Promise<KpiRead[]> {
	const params = new URLSearchParams();
	if (opts.department) params.set('department', opts.department);
	if (opts.scope) params.set('scope', opts.scope);
	if (opts.teamMemberId) params.set('team_member_id', opts.teamMemberId);
	const qs = params.toString();
	return apiRequest<KpiRead[]>(`/management/kpis${qs ? `?${qs}` : ''}`);
}

/** POST /api/v1/management/kpis (201). */
export async function createKpi(body: KpiCreate): Promise<KpiRead> {
	return apiRequest<KpiRead>('/management/kpis', { method: 'POST', body });
}

/** GET /api/v1/management/kpis/{id}. */
export async function getKpi(id: string): Promise<KpiRead> {
	return apiRequest<KpiRead>(`/management/kpis/${encodeURIComponent(id)}`);
}

/** PATCH /api/v1/management/kpis/{id} (partial update). */
export async function patchKpi(id: string, body: KpiUpdate): Promise<KpiRead> {
	return apiRequest<KpiRead>(`/management/kpis/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body
	});
}

/** DELETE /api/v1/management/kpis/{id} (soft delete). */
export async function deleteKpi(id: string): Promise<void> {
	await apiRequest<void>(`/management/kpis/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Datapoints
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/management/kpis/{id}/datapoints (201).
 *
 * A duplicate period returns 409 unless ``overwrite: true`` is passed, in
 * which case the existing row is replaced (200). Period format is validated
 * against the KPI's cadence server-side (422 on mismatch).
 */
export async function createDatapoint(
	kpiId: string,
	body: KpiDatapointCreate,
	opts: { overwrite?: boolean } = {}
): Promise<KpiDatapoint> {
	const qs = opts.overwrite ? '?overwrite=true' : '';
	return apiRequest<KpiDatapoint>(`/management/kpis/${encodeURIComponent(kpiId)}/datapoints${qs}`, {
		method: 'POST',
		body
	});
}

/** GET /api/v1/management/kpis/{id}/datapoints — optional from/to period bounds. */
export async function listDatapoints(
	kpiId: string,
	opts: { from?: string; to?: string } = {}
): Promise<KpiDatapoint[]> {
	const params = new URLSearchParams();
	if (opts.from) params.set('from', opts.from);
	if (opts.to) params.set('to', opts.to);
	const qs = params.toString();
	return apiRequest<KpiDatapoint[]>(
		`/management/kpis/${encodeURIComponent(kpiId)}/datapoints${qs ? `?${qs}` : ''}`
	);
}

/** GET /api/v1/management/kpis/{id}/series — the KPI + full ascending series. */
export async function getSeries(kpiId: string): Promise<KpiSeries> {
	return apiRequest<KpiSeries>(`/management/kpis/${encodeURIComponent(kpiId)}/series`);
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

/** GET /api/v1/management/dashboard — department sections + per-member rollup. */
export async function getDashboard(): Promise<KpiDashboard> {
	return apiRequest<KpiDashboard>('/management/dashboard');
}
