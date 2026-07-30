/**
 * History-aware back targets for Management detail pages.
 *
 * A detail page can be reached from several places (a KPI from the
 * dashboard OR the team roster; a dossier from the registry OR the
 * commitments rollup). A static parent link loses that context — this
 * helper returns the actual in-tab origin when there is one, else the
 * given fallback.
 *
 * Usage (inside a page component):
 *   let backHref = FALLBACK;
 *   afterNavigate((nav) => { backHref = managementBackHref(nav, FALLBACK); });
 */

import type { AfterNavigate } from '@sveltejs/kit';

const MANAGEMENT_PREFIX = '/lq-ai/management';

export function managementBackHref(nav: AfterNavigate, fallback: string): string {
	const from = nav.from?.url;
	const to = nav.to?.url;
	if (
		from &&
		from.pathname.startsWith(MANAGEMENT_PREFIX) &&
		(!to || from.pathname !== to.pathname)
	) {
		return from.pathname + from.search;
	}
	return fallback;
}
