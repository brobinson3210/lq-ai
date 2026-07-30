<!--
  /lq-ai/management/documents/[id] — the document viewer.

  Title + metadata line, the body rendered as markdown (same marked +
  DOMPurify mechanism as the chat surface), an inline edit mode covering
  metadata and the content textarea, and a two-step soft delete.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import { managementBackHref } from '$lib/lq-ai/management/backNav';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { managementDocumentsApi } from '$lib/lq-ai/api';
	import type { MgmtDocument } from '$lib/lq-ai/types';
	import { formatDate } from '$lib/lq-ai/management/stakeholders';
	import { renderMarkdown } from '$lib/lq-ai/management/markdown';
	import {
		COMMON_DOC_TYPES,
		validateNewDocument
	} from '$lib/lq-ai/components/NewDocumentModal.svelte';

	$: documentId = $page.params.id;

	let doc: MgmtDocument | null = null;
	let loading = true;
	let error: string | null = null;

	async function load() {
		if (!documentId) return;
		loading = true;
		try {
			doc = await managementDocumentsApi.getDocument(documentId);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load the document';
		} finally {
			loading = false;
		}
	}

	let backHref = '/lq-ai/management/documents';
	afterNavigate((nav) => {
		backHref = managementBackHref(nav, '/lq-ai/management/documents');
	});

	onMount(load);

	// ----- Edit mode -----

	let editing = false;
	let saving = false;
	let editError: string | null = null;
	let draftTitle = '';
	let draftType = '';
	let draftDate = '';
	let draftAuthor = '';
	let draftTags = '';
	let draftContent = '';

	function startEdit() {
		if (!doc) return;
		draftTitle = doc.title;
		draftType = doc.doc_type;
		draftDate = doc.doc_date ?? '';
		draftAuthor = doc.author ?? '';
		draftTags = doc.related_tags ?? '';
		draftContent = doc.content_md;
		editError = null;
		editing = true;
	}

	async function saveEdit() {
		if (!doc) return;
		const result = validateNewDocument({
			title: draftTitle,
			doc_type: draftType,
			content_md: draftContent
		});
		if (!result.valid) {
			editError = result.titleError ?? result.docTypeError ?? result.contentError;
			return;
		}
		saving = true;
		editError = null;
		try {
			doc = await managementDocumentsApi.patchDocument(doc.id, {
				title: draftTitle.trim(),
				doc_type: draftType.trim(),
				content_md: draftContent,
				doc_date: draftDate || null,
				author: draftAuthor.trim() || null,
				related_tags: draftTags.trim() || null
			});
			editing = false;
		} catch (e) {
			editError = e instanceof Error ? e.message : 'Failed to save the document';
		} finally {
			saving = false;
		}
	}

	// ----- Two-step delete -----

	let confirmingDelete = false;
	let deleting = false;
	let deleteError: string | null = null;

	async function confirmDelete() {
		if (!doc) return;
		deleting = true;
		deleteError = null;
		try {
			await managementDocumentsApi.deleteDocument(doc.id);
			goto('/lq-ai/management/documents');
		} catch (e) {
			deleteError = e instanceof Error ? e.message : 'Failed to remove the document';
			deleting = false;
		}
	}

	function tagsOf(d: MgmtDocument): string[] {
		return (d.related_tags ?? '')
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);
	}
</script>

<main class="mdv-page" data-testid="lq-ai-mgmt-docs-viewer-page">
	<a class="mdv-back" href={backHref}>← Back</a>

	{#if loading}
		<p class="lq-text-body mdv-state-msg">Loading the document…</p>
	{:else if error || !doc}
		<p class="lq-text-body mdv-state-msg mdv-state-msg--error" role="alert">
			{error ?? 'Document not found'}
		</p>
	{:else if editing}
		<form
			class="mdv-edit-form"
			data-testid="lq-ai-mgmt-docs-edit-form"
			on:submit|preventDefault={saveEdit}
		>
			<div class="mdv-field">
				<label class="mdv-label" for="mdv-title">Title</label>
				<input
					id="mdv-title"
					class="mdv-input"
					type="text"
					maxlength="300"
					bind:value={draftTitle}
					disabled={saving}
				/>
			</div>
			<div class="mdv-form-row">
				<div class="mdv-field">
					<label class="mdv-label" for="mdv-type">Type</label>
					<input
						id="mdv-type"
						class="mdv-input"
						type="text"
						maxlength="60"
						list="mdv-doc-types"
						bind:value={draftType}
						disabled={saving}
					/>
					<datalist id="mdv-doc-types">
						{#each COMMON_DOC_TYPES as t (t)}
							<option value={t}></option>
						{/each}
					</datalist>
				</div>
				<div class="mdv-field">
					<label class="mdv-label" for="mdv-date">Document date</label>
					<input
						id="mdv-date"
						class="mdv-input"
						type="date"
						bind:value={draftDate}
						disabled={saving}
					/>
				</div>
				<div class="mdv-field">
					<label class="mdv-label" for="mdv-author">Author</label>
					<input
						id="mdv-author"
						class="mdv-input"
						type="text"
						maxlength="200"
						bind:value={draftAuthor}
						disabled={saving}
					/>
				</div>
				<div class="mdv-field">
					<label class="mdv-label" for="mdv-tags">Tags (comma-separated)</label>
					<input
						id="mdv-tags"
						class="mdv-input"
						type="text"
						bind:value={draftTags}
						disabled={saving}
					/>
				</div>
			</div>
			<div class="mdv-field">
				<label class="mdv-label" for="mdv-content">Content (markdown)</label>
				<textarea
					id="mdv-content"
					class="mdv-textarea"
					rows="18"
					bind:value={draftContent}
					disabled={saving}
					data-testid="lq-ai-mgmt-docs-edit-content"
				></textarea>
			</div>
			{#if editError}
				<p class="mdv-inline-error" role="alert">{editError}</p>
			{/if}
			<div class="mdv-form-actions">
				<button
					type="button"
					class="mdv-btn-secondary"
					disabled={saving}
					on:click={() => (editing = false)}
				>
					Cancel
				</button>
				<button
					type="submit"
					class="mdv-btn-primary"
					disabled={saving}
					data-testid="lq-ai-mgmt-docs-edit-save"
				>
					{saving ? 'Saving…' : 'Save document'}
				</button>
			</div>
		</form>
	{:else}
		<header class="mdv-header">
			<div class="mdv-header__main">
				<h1 class="lq-text-page-h">{doc.title}</h1>
				<p class="mdv-meta">
					<span class="mdv-chip">{doc.doc_type}</span>
					<span>{formatDate(doc.doc_date)}</span>
					{#if doc.author}<span>· {doc.author}</span>{/if}
					{#each tagsOf(doc) as tag (tag)}
						<span class="mdv-tag">#{tag}</span>
					{/each}
				</p>
			</div>
			<div class="mdv-header__actions">
				<button
					type="button"
					class="mdv-btn-secondary"
					data-testid="lq-ai-mgmt-docs-edit-btn"
					on:click={startEdit}
				>
					Edit
				</button>
				{#if confirmingDelete}
					<span class="mdv-confirm" role="alertdialog" aria-label="Confirm document removal">
						Remove this document?
						<button
							type="button"
							class="mdv-btn-danger"
							disabled={deleting}
							data-testid="lq-ai-mgmt-docs-delete-confirm"
							on:click={confirmDelete}
						>
							{deleting ? 'Removing…' : 'Yes, remove'}
						</button>
						<button
							type="button"
							class="mdv-btn-secondary"
							disabled={deleting}
							on:click={() => (confirmingDelete = false)}
						>
							Cancel
						</button>
					</span>
				{:else}
					<button
						type="button"
						class="mdv-btn-ghost-danger"
						data-testid="lq-ai-mgmt-docs-delete-btn"
						on:click={() => (confirmingDelete = true)}
					>
						Remove
					</button>
				{/if}
			</div>
		</header>
		{#if deleteError}
			<p class="mdv-inline-error" role="alert">{deleteError}</p>
		{/if}

		<article class="mdv-content" data-testid="lq-ai-mgmt-docs-content">
			<!-- eslint-disable-next-line svelte/no-at-html-tags — sanitised via DOMPurify in renderMarkdown -->
			<div class="mdv-md">{@html renderMarkdown(doc.content_md)}</div>
		</article>
	{/if}
</main>

<style>
	.mdv-page {
		padding: var(--lq-space-6);
		max-width: 860px;
		margin: 0 auto;
	}

	.mdv-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.mdv-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.mdv-state-msg--error {
		color: var(--lq-error);
	}

	.mdv-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-4);
	}

	.mdv-meta {
		display: flex;
		align-items: baseline;
		gap: var(--lq-space-2);
		flex-wrap: wrap;
		margin-top: var(--lq-space-2);
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
	}

	.mdv-chip {
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

	.mdv-tag {
		font-size: 0.8rem;
		color: var(--lq-text-tertiary);
	}

	.mdv-header__actions {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		flex-wrap: wrap;
	}

	.mdv-confirm {
		display: inline-flex;
		align-items: center;
		gap: var(--lq-space-2);
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
	}

	.mdv-content {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-5, 1.25rem) var(--lq-space-6);
	}

	.mdv-md {
		color: var(--lq-text-primary);
		font-size: 0.95rem;
		line-height: 1.7;
	}

	.mdv-md :global(h1),
	.mdv-md :global(h2),
	.mdv-md :global(h3),
	.mdv-md :global(h4) {
		color: var(--lq-text-primary);
		font-weight: 600;
		margin: var(--lq-space-4) 0 var(--lq-space-2);
	}

	.mdv-md :global(h1) {
		font-size: 1.25rem;
	}

	.mdv-md :global(h2) {
		font-size: 1.1rem;
	}

	.mdv-md :global(h3),
	.mdv-md :global(h4) {
		font-size: 1rem;
	}

	.mdv-md :global(p),
	.mdv-md :global(ul),
	.mdv-md :global(ol) {
		margin: 0 0 var(--lq-space-3);
	}

	.mdv-md :global(ul),
	.mdv-md :global(ol) {
		padding-left: 1.5rem;
	}

	.mdv-md :global(li) {
		margin-bottom: var(--lq-space-1);
	}

	.mdv-md :global(blockquote) {
		border-left: 3px solid var(--lq-border);
		margin: 0 0 var(--lq-space-3);
		padding-left: var(--lq-space-3);
		color: var(--lq-text-secondary);
	}

	.mdv-md :global(code) {
		font-family: var(--lq-font-mono, ui-monospace, monospace);
		font-size: 0.85em;
		background: var(--lq-inset);
		border-radius: var(--lq-radius-sm);
		padding: 0.05em 0.35em;
	}

	.mdv-md :global(pre) {
		background: var(--lq-inset);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-3);
		overflow-x: auto;
	}

	.mdv-md :global(table) {
		border-collapse: collapse;
		margin: 0 0 var(--lq-space-3);
	}

	.mdv-md :global(th),
	.mdv-md :global(td) {
		border: 1px solid var(--lq-border);
		padding: var(--lq-space-1) var(--lq-space-2);
		font-size: 0.85rem;
	}

	.mdv-edit-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-3);
	}

	.mdv-form-row {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: var(--lq-space-3);
	}

	.mdv-field {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.mdv-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.mdv-input,
	.mdv-textarea {
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		font-size: 14px;
		color: var(--lq-text-primary);
		box-sizing: border-box;
		width: 100%;
		transition: border-color 0.15s ease;
	}

	.mdv-input:focus,
	.mdv-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.mdv-textarea {
		resize: vertical;
		font-family: var(--lq-font-mono, ui-monospace, monospace);
		font-size: 13px;
		line-height: 1.55;
	}

	.mdv-form-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-3);
	}

	.mdv-inline-error {
		color: var(--lq-error);
		font-size: 13px;
		margin: 0;
	}

	.mdv-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.mdv-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.mdv-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.mdv-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.mdv-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.mdv-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.mdv-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.mdv-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.mdv-btn-danger {
		background: var(--lq-error);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.mdv-btn-danger:focus-visible {
		outline: 2px solid var(--lq-error);
		outline-offset: 2px;
	}

	.mdv-btn-ghost-danger {
		background: transparent;
		color: var(--lq-error);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.mdv-btn-ghost-danger:hover {
		background: var(--lq-error-soft);
	}

	.mdv-btn-ghost-danger:focus-visible {
		outline: 2px solid var(--lq-error);
		outline-offset: 2px;
	}
</style>
