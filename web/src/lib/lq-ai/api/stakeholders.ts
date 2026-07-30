/**
 * /api/v1/stakeholders — the Management tab's Stakeholders module.
 *
 * Registry CRUD, per-stakeholder interactions / commitments / positions,
 * and the cross-stakeholder commitments rollup
 * (GET+PATCH /stakeholder-commitments).
 */
import { apiRequest } from './client';
import type {
	Stakeholder,
	StakeholderCreate,
	StakeholderUpdate,
	StakeholderInteraction,
	StakeholderInteractionCreate,
	StakeholderCommitment,
	StakeholderCommitmentCreate,
	StakeholderCommitmentRollupRow,
	StakeholderCommitmentUpdate,
	StakeholderPosition,
	StakeholderPositionCreate,
	StakeholderType,
	CommitmentDirection,
	CommitmentStatus
} from '../types';

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/stakeholders — list the caller's stakeholders.
 *
 * Query parameters:
 *
 *   * ``stakeholder_type`` — filter to one type.
 *   * ``space`` — relationship-space filter; ids match RELATIONSHIP_SPACES
 *     in `$lib/lq-ai/management/modules` (board | c-suite | investors |
 *     customers | regulators | outside-firms | media).
 *   * ``needs_attention`` — server-side cadence/health attention filter.
 */
export async function listStakeholders(
	opts: {
		stakeholderType?: StakeholderType;
		space?: string;
		needsAttention?: boolean;
	} = {}
): Promise<Stakeholder[]> {
	const params = new URLSearchParams();
	if (opts.stakeholderType) params.set('stakeholder_type', opts.stakeholderType);
	if (opts.space) params.set('space', opts.space);
	if (opts.needsAttention) params.set('needs_attention', 'true');
	const qs = params.toString();
	return apiRequest<Stakeholder[]>(`/stakeholders${qs ? `?${qs}` : ''}`);
}

/** POST /api/v1/stakeholders (201). */
export async function createStakeholder(body: StakeholderCreate): Promise<Stakeholder> {
	return apiRequest<Stakeholder>('/stakeholders', { method: 'POST', body });
}

/** GET /api/v1/stakeholders/{id}. */
export async function getStakeholder(id: string): Promise<Stakeholder> {
	return apiRequest<Stakeholder>(`/stakeholders/${encodeURIComponent(id)}`);
}

/** PATCH /api/v1/stakeholders/{id} (partial update). */
export async function patchStakeholder(id: string, body: StakeholderUpdate): Promise<Stakeholder> {
	return apiRequest<Stakeholder>(`/stakeholders/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body
	});
}

/** DELETE /api/v1/stakeholders/{id} (204 soft-delete). */
export async function deleteStakeholder(id: string): Promise<void> {
	await apiRequest<void>(`/stakeholders/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Interactions
// ---------------------------------------------------------------------------

/** GET /api/v1/stakeholders/{id}/interactions — newest first. */
export async function listInteractions(stakeholderId: string): Promise<StakeholderInteraction[]> {
	return apiRequest<StakeholderInteraction[]>(
		`/stakeholders/${encodeURIComponent(stakeholderId)}/interactions`
	);
}

/** POST /api/v1/stakeholders/{id}/interactions. */
export async function createInteraction(
	stakeholderId: string,
	body: StakeholderInteractionCreate
): Promise<StakeholderInteraction> {
	return apiRequest<StakeholderInteraction>(
		`/stakeholders/${encodeURIComponent(stakeholderId)}/interactions`,
		{ method: 'POST', body }
	);
}

// ---------------------------------------------------------------------------
// Commitments (per-stakeholder)
// ---------------------------------------------------------------------------

/** GET /api/v1/stakeholders/{id}/commitments. */
export async function listCommitments(stakeholderId: string): Promise<StakeholderCommitment[]> {
	return apiRequest<StakeholderCommitment[]>(
		`/stakeholders/${encodeURIComponent(stakeholderId)}/commitments`
	);
}

/** POST /api/v1/stakeholders/{id}/commitments. */
export async function createCommitment(
	stakeholderId: string,
	body: StakeholderCommitmentCreate
): Promise<StakeholderCommitment> {
	return apiRequest<StakeholderCommitment>(
		`/stakeholders/${encodeURIComponent(stakeholderId)}/commitments`,
		{ method: 'POST', body }
	);
}

// ---------------------------------------------------------------------------
// Positions ("stances by situation")
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/stakeholders/{id}/positions.
 *
 * ``latest=true`` collapses to one row per topic (the most recent ``as_of``).
 */
export async function listPositions(
	stakeholderId: string,
	opts: { latest?: boolean } = {}
): Promise<StakeholderPosition[]> {
	const params = new URLSearchParams();
	if (opts.latest) params.set('latest', 'true');
	const qs = params.toString();
	return apiRequest<StakeholderPosition[]>(
		`/stakeholders/${encodeURIComponent(stakeholderId)}/positions${qs ? `?${qs}` : ''}`
	);
}

/** POST /api/v1/stakeholders/{id}/positions. */
export async function createPosition(
	stakeholderId: string,
	body: StakeholderPositionCreate
): Promise<StakeholderPosition> {
	return apiRequest<StakeholderPosition>(
		`/stakeholders/${encodeURIComponent(stakeholderId)}/positions`,
		{ method: 'POST', body }
	);
}

// ---------------------------------------------------------------------------
// Cross-stakeholder commitments rollup
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/stakeholder-commitments — commitments across all the caller's
 * stakeholders ("what do I owe the board this week").
 */
export async function listCommitmentRollup(
	opts: {
		status?: CommitmentStatus;
		direction?: CommitmentDirection;
	} = {}
): Promise<StakeholderCommitmentRollupRow[]> {
	const params = new URLSearchParams();
	if (opts.status) params.set('status', opts.status);
	if (opts.direction) params.set('direction', opts.direction);
	const qs = params.toString();
	return apiRequest<StakeholderCommitmentRollupRow[]>(
		`/stakeholder-commitments${qs ? `?${qs}` : ''}`
	);
}

/** PATCH /api/v1/stakeholder-commitments/{id} (flat partial update). */
export async function patchCommitment(
	id: string,
	body: StakeholderCommitmentUpdate
): Promise<StakeholderCommitmentRollupRow> {
	return apiRequest<StakeholderCommitmentRollupRow>(
		`/stakeholder-commitments/${encodeURIComponent(id)}`,
		{ method: 'PATCH', body }
	);
}
