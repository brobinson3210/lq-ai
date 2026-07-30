/**
 * Markdown rendering for the Management tab's AI features and document space.
 *
 * Reuses the exact mechanism of the lq-ai chat surface (MessageBubble):
 * client-side `marked.parse` sanitised with `DOMPurify` — both existing
 * dependencies of the OpenWebUI fork; no new packages.
 *
 * `renderBriefMarkdown` additionally decorates the bracketed source markers
 * AI briefs carry — [interaction 2026-06-12], [doc: <title>], [kpi: <name>]
 * — with a subtle chip (`.mgmt-src-marker`) styled by the consuming
 * component via :global. Markers are decorated, never stripped: they are
 * the brief's provenance trail.
 */
import DOMPurify from 'dompurify';
import { marked } from 'marked';

/** Markdown → sanitised HTML for {@html}. Same call shape as MessageBubble. */
export function renderMarkdown(md: string | null | undefined): string {
	return DOMPurify.sanitize(marked.parse(md ?? '', { async: false }) as string);
}

/**
 * Source markers: `[interaction …]`, `[doc: …]`, `[kpi: …]`. Matched only
 * within already-sanitised HTML text (never across tags — `<`/`>` excluded),
 * so wrapping them in a span introduces no new unsanitised content.
 */
const SOURCE_MARKER_RE = /\[(?:interaction|doc:|kpi:)[^\]<>\n]*\]/g;

/** Brief markdown → sanitised HTML with source markers wrapped as chips. */
export function renderBriefMarkdown(md: string | null | undefined): string {
	return renderMarkdown(md).replace(
		SOURCE_MARKER_RE,
		(marker) => `<span class="mgmt-src-marker">${marker}</span>`
	);
}
