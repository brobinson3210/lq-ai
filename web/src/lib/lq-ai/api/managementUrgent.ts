/**
 * /api/v1/management/urgent-matters — the Urgent Matters feed.
 *
 * One read-only endpoint: the server applies the GC's sorting rules
 * (reds then yellows, capped at 10 combined) — the client renders,
 * it never re-ranks.
 */
import { apiRequest } from './client';
import type { UrgentMattersResponse } from '../types';

/** GET /api/v1/management/urgent-matters */
export async function getUrgentMatters(): Promise<UrgentMattersResponse> {
	return apiRequest<UrgentMattersResponse>('/management/urgent-matters');
}
