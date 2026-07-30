/**
 * /api/v1/management — the Management tab's AI features.
 *
 * The static KPI-interview questions, AI-job creation (pre-meeting brief /
 * review prep / KPI draft), the per-job detail read the UI polls, the
 * newest-first job list behind the "past briefs" panels, and `pollAiJob` —
 * the poll-until-terminal helper every AI feature shares.
 */
import { apiRequest } from './client';
import type {
	MgmtAiJob,
	MgmtAiJobCreate,
	MgmtAiJobListRow,
	MgmtAiJobType,
	MgmtKpiWizardQuestionsRead
} from '../types';

// ---------------------------------------------------------------------------
// Wizard questions
// ---------------------------------------------------------------------------

/** GET /api/v1/management/kpi-wizard/questions — the static interview script. */
export async function getWizardQuestions(): Promise<MgmtKpiWizardQuestionsRead> {
	return apiRequest<MgmtKpiWizardQuestionsRead>('/management/kpi-wizard/questions');
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/management/ai-jobs (202) — create a job row.
 *
 * Pairing enforced server-side (422 on violation, 404 unknown subject):
 * pre_meeting_brief → stakeholder_id; review_prep → team_member_id;
 * kpi_draft → non-empty answers, no subject id.
 */
export async function createAiJob(body: MgmtAiJobCreate): Promise<MgmtAiJob> {
	return apiRequest<MgmtAiJob>('/management/ai-jobs', { method: 'POST', body });
}

/** GET /api/v1/management/ai-jobs/{id} — the row the UI polls. */
export async function getAiJob(id: string): Promise<MgmtAiJob> {
	return apiRequest<MgmtAiJob>(`/management/ai-jobs/${encodeURIComponent(id)}`);
}

/**
 * GET /api/v1/management/ai-jobs — newest first. Rows already include
 * result_md / result_json (no `params`), so past-brief panels render
 * straight from the list.
 */
export async function listAiJobs(
	opts: {
		jobType?: MgmtAiJobType;
		stakeholderId?: string;
		teamMemberId?: string;
	} = {}
): Promise<MgmtAiJobListRow[]> {
	const params = new URLSearchParams();
	if (opts.jobType) params.set('job_type', opts.jobType);
	if (opts.stakeholderId) params.set('stakeholder_id', opts.stakeholderId);
	if (opts.teamMemberId) params.set('team_member_id', opts.teamMemberId);
	const qs = params.toString();
	return apiRequest<MgmtAiJobListRow[]>(`/management/ai-jobs${qs ? `?${qs}` : ''}`);
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

export interface PollAiJobOptions {
	/** Delay between polls; the backend suggests ~2s. */
	intervalMs?: number;
	/** Total waiting budget before rejecting. */
	timeoutMs?: number;
	/** Fires after every fetch with the latest row (including the terminal one). */
	onUpdate?: (job: MgmtAiJob) => void;
}

/** Thrown when a job stays non-terminal past the polling budget. */
export class AiJobTimeoutError extends Error {
	constructor(id: string, timeoutMs: number) {
		super(
			`The AI job did not finish within ${Math.round(timeoutMs / 1000)}s. It may still complete — check past results in a moment.`
		);
		this.name = 'AiJobTimeoutError';
		void id;
	}
}

/**
 * Poll GET /management/ai-jobs/{id} every `intervalMs` until the job reaches
 * a terminal status ('done' OR 'error' — both resolve; the caller branches
 * on `job.status`). Rejects with `AiJobTimeoutError` once the accumulated
 * waiting time would exceed `timeoutMs`. Deterministic under fake timers:
 * elapsed time is counted from the sleeps themselves, not the wall clock.
 */
export async function pollAiJob(id: string, opts: PollAiJobOptions = {}): Promise<MgmtAiJob> {
	const intervalMs = opts.intervalMs ?? 2000;
	const timeoutMs = opts.timeoutMs ?? 180000;
	let waited = 0;
	for (;;) {
		const job = await getAiJob(id);
		opts.onUpdate?.(job);
		if (job.status === 'done' || job.status === 'error') return job;
		if (waited + intervalMs > timeoutMs) throw new AiJobTimeoutError(id, timeoutMs);
		await new Promise<void>((resolve) => setTimeout(resolve, intervalMs));
		waited += intervalMs;
	}
}
