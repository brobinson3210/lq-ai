<!--
  /lq-ai/management/documents — the Management library.

  Board packs, minutes, memos: a private, per-user metadata list (no bodies
  on the wire — content_chars only) with server-side filters (doc_type
  exact, debounced substring search over title/author/tags, doc_date
  range), row click → viewer, and the add-document modal.
-->
<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { managementDocumentsApi } from '$lib/lq-ai/api';
	import type { MgmtDocumentListRow } from '$lib/lq-ai/types';
	import { formatDate } from '$lib/lq-ai/management/stakeholders';
	import NewDocumentModal from '$lib/lq-ai/components/NewDocumentModal.svelte';

	let rows: MgmtDocumentListRow[] = [];
	let loading = true;
	let error: string | null = null;
	let showNewModal = false;

	// ----- Filters (all server-side) -----

	let filterType = '';
	let search = '';
	let dateFrom = '';
	let dateTo = '';

	/** doc_type options accumulate across loads so picking one doesn't collapse the list. */
	let knownTypes: string[] = [];

	async function refresh() {
		loading = true;
		try {
			rows = await managementDocumentsApi.listDocuments({
				docType: filterType || undefined,
				q: search.trim() || undefined,
				dateFrom: dateFrom || undefined,
				dateTo: dateTo || undefined
			});
			const seen = new Set(knownTypes);
			for (const r of rows) seen.add(r.doc_type);
			knownTypes = [...seen].sort();
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load the document library';
		} finally {
			loading = false;
		}
	}

	onMount(refresh);

	// Debounced search; other filters refetch immediately.
	let searchTimer: ReturnType<typeof setTimeout> | null = null;

	function onSearchInput() {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => void refresh(), 300);
	}

	onDestroy(() => {
		if (searchTimer) clearTimeout(searchTimer);
	});

	$: hasFilters = !!(filterType || search.trim() || dateFrom || dateTo);

	function clearFilters() {
		filterType = '';
		search = '';
		dateFrom = '';
		dateTo = '';
		void refresh();
	}

	/** content_chars → "2.1k chars" (or "412 chars" under a thousand). */
	function sizeLabel(chars: number): string {
		if (chars >= 1000) return `${(chars / 1000).toFixed(1)}k chars`;
		return `${chars} chars`;
	}

	function tagsOf(row: MgmtDocumentListRow): string[] {
		return (row.related_tags ?? '')
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);
	}
</script>

<main class="mdl-page" data-testid="lq-ai-mgmt-docs-page">
	<a class="mdl-back" href="/lq-ai/management">← Management</a>

	<header class="mdl-header">
		<div>
			<h1 class="lq-text-page-h">Documents</h1>
			<p class="mdl-sub lq-text-body">
				The Management library — board packs, minutes, memos. Private to this instance; feeds the
				pre-meeting brief.
			</p>
		</div>
		<button
			type="button"
			class="mdl-btn-primary"
			data-testid="lq-ai-mgmt-docs-new-btn"
			on:click={() => (showNewModal = true)}
		>
			+ Add document
		</button>
	</header>

	<div class="mdl-filters" data-testid="lq-ai-mgmt-docs-filters">
		<select
			class="mdl-input mdl-input--compact"
			bind:value={filterType}
			aria-label="Filter by document type"
			data-testid="lq-ai-mgmt-docs-filter-type"
			on:change={() => void refresh()}
		>
			<option value="">All types</option>
			{#each knownTypes as t (t)}
				<option value={t}>{t}</option>
			{/each}
		</select>
		<input
			type="search"
			class="mdl-input mdl-filters__search"
			bind:value={search}
			placeholder="Search title, author, tags…"
			aria-label="Search documents"
			data-testid="lq-ai-mgmt-docs-search"
			on:input={onSearchInput}
		/>
		<label class="mdl-date-label">
			<span class="mdl-date-caption">From</span>
			<input
				type="date"
				class="mdl-input mdl-input--compact"
				bind:value={dateFrom}
				aria-label="Document date from"
				data-testid="lq-ai-mgmt-docs-date-from"
				on:change={() => void refresh()}
			/>
		</label>
		<label class="mdl-date-label">
			<span class="mdl-date-caption">To</span>
			<input
				type="date"
				class="mdl-input mdl-input--compact"
				bind:value={dateTo}
				aria-label="Document date to"
				data-testid="lq-ai-mgmt-docs-date-to"
				on:change={() => void refresh()}
			/>
		</label>
		{#if hasFilters}
			<button type="button" class="mdl-btn-mini" on:click={clearFilters}>Clear</button>
		{/if}
	</div>

	{#if loading}
		<p class="lq-text-body mdl-state-msg">Loading the library…</p>
	{:else if error}
		<p class="lq-text-body mdl-state-msg mdl-state-msg--error" role="alert">
			Couldn't load the document library: {error}
		</p>
	{:else if rows.length === 0}
		<section class="mdl-empty" data-testid="lq-ai-mgmt-docs-empty">
			{#if hasFilters}
				<p class="lq-text-body mdl-empty__copy">No documents match these filters.</p>
				<button type="button" class="mdl-btn-secondary" on:click={clearFilters}>
					Clear filters
				</button>
			{:else}
				<p class="lq-text-body mdl-empty__copy">
					No documents yet. The library is where board packs, minutes, and memos become
					institutional memory — and raw material for the pre-meeting brief.
				</p>
				<button type="button" class="mdl-btn-primary" on:click={() => (showNewModal = true)}>
					+ Add document
				</button>
			{/if}
		</section>
	{:else}
		<ul class="mdl-list" data-testid="lq-ai-mgmt-docs-list">
			{#each rows as row (row.id)}
				<li>
					<a
						class="mdl-row"
						href={`/lq-ai/management/documents/${row.id}`}
						aria-label={`Open document: ${row.title}`}
						data-testid={`lq-ai-mgmt-docs-row-${row.id}`}
					>
						<div class="mdl-row__main">
							<span class="mdl-row__title">{row.title}</span>
							<span class="mdl-chip">{row.doc_type}</span>
						</div>
						<div class="mdl-row__meta">
							<span class="mdl-row__date">{formatDate(row.doc_date)}</span>
							{#if row.author}
								<span class="mdl-row__author">{row.author}</span>
							{/if}
							<span class="mdl-row__size">{sizeLabel(row.content_chars)}</span>
							{#each tagsOf(row) as tag (tag)}
								<span class="mdl-tag">#{tag}</span>
							{/each}
						</div>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</main>

{#if showNewModal}
	<NewDocumentModal
		onClose={() => (showNewModal = false)}
		onCreated={(doc) => {
			showNewModal = false;
			goto(`/lq-ai/management/documents/${doc.id}`);
		}}
	/>
{/if}

<style>
	.mdl-page {
		padding: var(--lq-space-6);
		max-width: 1000px;
		margin: 0 auto;
	}

	.mdl-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.mdl-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-4);
	}

	.mdl-sub {
		margin-top: var(--lq-space-2);
		color: var(--lq-text-secondary);
		max-width: 62ch;
	}

	.mdl-filters {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-4);
	}

	.mdl-filters__search {
		flex: 1;
		min-width: 200px;
	}

	.mdl-date-label {
		display: inline-flex;
		align-items: center;
		gap: var(--lq-space-1);
	}

	.mdl-date-caption {
		font-size: 12px;
		color: var(--lq-text-tertiary);
	}

	.mdl-input {
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		font-size: 14px;
		color: var(--lq-text-primary);
		box-sizing: border-box;
		transition: border-color 0.15s ease;
	}

	.mdl-input:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.mdl-input--compact {
		font-size: 13px;
		padding: var(--lq-space-1) var(--lq-space-2);
	}

	.mdl-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.mdl-state-msg--error {
		color: var(--lq-error);
	}

	.mdl-empty {
		text-align: center;
		padding: var(--lq-space-8) var(--lq-space-4);
	}

	.mdl-empty__copy {
		color: var(--lq-text-secondary);
		margin-bottom: var(--lq-space-4);
	}

	.mdl-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
	}

	.mdl-row {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-3) var(--lq-space-4);
		text-decoration: none;
		color: inherit;
		transition:
			border-color 0.15s ease,
			box-shadow 0.15s ease;
	}

	.mdl-row:hover {
		border-color: var(--lq-accent-border);
		box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
	}

	.mdl-row:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.mdl-row__main {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		flex-wrap: wrap;
	}

	.mdl-row__title {
		font-weight: 600;
		font-size: 0.95rem;
		color: var(--lq-text-primary);
		flex: 1;
		min-width: 0;
	}

	.mdl-chip {
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: var(--lq-radius-pill);
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
		background: var(--lq-tier-soft);
		color: var(--lq-tier);
		border: 1px solid var(--lq-tier-border);
	}

	.mdl-row__meta {
		display: flex;
		align-items: baseline;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		font-size: 0.8rem;
		color: var(--lq-text-tertiary);
	}

	.mdl-row__author {
		color: var(--lq-text-secondary);
	}

	.mdl-tag {
		font-size: 0.75rem;
		color: var(--lq-text-tertiary);
	}

	.mdl-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.mdl-btn-primary:hover {
		filter: brightness(0.95);
	}

	.mdl-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.mdl-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.mdl-btn-secondary:hover {
		background: var(--lq-inset);
	}

	.mdl-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.mdl-btn-mini {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-sm);
		padding: 2px var(--lq-space-2);
		font-size: 12px;
		font-weight: 500;
		cursor: pointer;
	}

	.mdl-btn-mini:hover {
		border-color: var(--lq-accent);
		color: var(--lq-accent);
	}

	.mdl-btn-mini:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}
</style>
