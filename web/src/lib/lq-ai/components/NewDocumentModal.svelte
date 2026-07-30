<script context="module" lang="ts">
	/**
	 * Form validation helpers — exported for unit tests (house pattern,
	 * mirrors NewKpiModal).
	 */
	export interface NewDocumentFields {
		title: string;
		doc_type: string;
		content_md: string;
	}

	export interface DocumentValidationResult {
		valid: boolean;
		titleError: string | null;
		docTypeError: string | null;
		contentError: string | null;
	}

	export function validateNewDocument(fields: NewDocumentFields): DocumentValidationResult {
		const titleError = !fields.title.trim()
			? 'A title is required.'
			: fields.title.trim().length > 300
				? 'The title must be 300 characters or fewer.'
				: null;
		const docTypeError = !fields.doc_type.trim()
			? 'A document type is required.'
			: fields.doc_type.trim().length > 60
				? 'The type must be 60 characters or fewer.'
				: null;
		const contentError = !fields.content_md.trim() ? 'Paste or write the document content.' : null;
		return {
			valid: titleError === null && docTypeError === null && contentError === null,
			titleError,
			docTypeError,
			contentError
		};
	}

	/** Common doc_type suggestions — free text; the datalist is only a nudge. */
	export const COMMON_DOC_TYPES: readonly string[] = [
		'board_pack',
		'minutes',
		'counsel_letter',
		'budget_memo',
		'matter_memo',
		'regulator_correspondence',
		'negotiation_summary',
		'insurance',
		'compliance_report',
		'press',
		'other'
	] as const;
</script>

<script lang="ts">
	import { managementDocumentsApi } from '$lib/lq-ai/api';
	import type { MgmtDocument } from '$lib/lq-ai/types';

	export let onClose: () => void;
	export let onCreated: (doc: MgmtDocument) => void;

	// Form state
	let title = '';
	let docType = '';
	let docDate = '';
	let author = '';
	let relatedTags = '';
	let contentMd = '';

	// UI state
	let submitting = false;
	let titleError: string | null = null;
	let docTypeError: string | null = null;
	let contentError: string | null = null;
	let submitError: string | null = null;

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
	}

	let titleInput: HTMLInputElement;
	$: if (titleInput) titleInput.focus();

	async function handleSubmit() {
		submitError = null;
		const result = validateNewDocument({ title, doc_type: docType, content_md: contentMd });
		titleError = result.titleError;
		docTypeError = result.docTypeError;
		contentError = result.contentError;
		if (!result.valid) return;

		submitting = true;
		try {
			const created = await managementDocumentsApi.createDocument({
				title: title.trim(),
				doc_type: docType.trim(),
				content_md: contentMd,
				doc_date: docDate || undefined,
				author: author.trim() || undefined,
				related_tags: relatedTags.trim() || undefined
			});
			onCreated(created);
		} catch (e: unknown) {
			submitError = e instanceof Error ? e.message : "Couldn't reach the server. Try again.";
		} finally {
			submitting = false;
		}
	}
</script>

<div
	class="ndm-backdrop"
	role="dialog"
	aria-modal="true"
	aria-labelledby="ndm-title"
	tabindex="-1"
	data-testid="lq-ai-mgmt-docs-new-modal"
	on:click={onClose}
	on:keydown={handleKeydown}
>
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div class="ndm-panel" on:click|stopPropagation on:keydown|stopPropagation>
		<h2 id="ndm-title" class="lq-text-page-h ndm-title">Add document</h2>

		<form on:submit|preventDefault={handleSubmit} class="ndm-form" novalidate>
			<div class="ndm-field">
				<label class="ndm-label" for="ndm-doc-title"
					>Title <span class="ndm-required" aria-hidden="true">*</span></label
				>
				<input
					id="ndm-doc-title"
					type="text"
					bind:this={titleInput}
					bind:value={title}
					class="ndm-input"
					class:ndm-input--error={!!titleError}
					placeholder="e.g. Q2 2026 board pack — legal section"
					maxlength="300"
					required
					disabled={submitting}
					data-testid="lq-ai-mgmt-docs-new-title"
					aria-describedby={titleError ? 'ndm-title-error' : undefined}
				/>
				{#if titleError}
					<p id="ndm-title-error" class="ndm-field-error" role="alert">{titleError}</p>
				{/if}
			</div>

			<div class="ndm-row">
				<div class="ndm-field">
					<label class="ndm-label" for="ndm-doc-type"
						>Type <span class="ndm-required" aria-hidden="true">*</span></label
					>
					<input
						id="ndm-doc-type"
						type="text"
						bind:value={docType}
						class="ndm-input"
						class:ndm-input--error={!!docTypeError}
						placeholder="board_pack, minutes…"
						maxlength="60"
						list="ndm-doc-types"
						disabled={submitting}
						data-testid="lq-ai-mgmt-docs-new-type"
						aria-describedby={docTypeError ? 'ndm-type-error' : undefined}
					/>
					<datalist id="ndm-doc-types">
						{#each COMMON_DOC_TYPES as t (t)}
							<option value={t}></option>
						{/each}
					</datalist>
					{#if docTypeError}
						<p id="ndm-type-error" class="ndm-field-error" role="alert">{docTypeError}</p>
					{/if}
				</div>

				<div class="ndm-field">
					<label class="ndm-label" for="ndm-doc-date"
						>Document date <span class="ndm-optional">(optional)</span></label
					>
					<input
						id="ndm-doc-date"
						type="date"
						bind:value={docDate}
						class="ndm-input"
						disabled={submitting}
						data-testid="lq-ai-mgmt-docs-new-date"
					/>
				</div>
			</div>

			<div class="ndm-row">
				<div class="ndm-field">
					<label class="ndm-label" for="ndm-doc-author"
						>Author <span class="ndm-optional">(optional)</span></label
					>
					<input
						id="ndm-doc-author"
						type="text"
						bind:value={author}
						class="ndm-input"
						placeholder="e.g. Outside counsel — Meridian LLP"
						maxlength="200"
						disabled={submitting}
					/>
				</div>

				<div class="ndm-field">
					<label class="ndm-label" for="ndm-doc-tags"
						>Tags <span class="ndm-optional">(optional, comma-separated)</span></label
					>
					<input
						id="ndm-doc-tags"
						type="text"
						bind:value={relatedTags}
						class="ndm-input"
						placeholder="series-c, governance"
						disabled={submitting}
					/>
				</div>
			</div>

			<div class="ndm-field">
				<label class="ndm-label" for="ndm-doc-content"
					>Content <span class="ndm-required" aria-hidden="true">*</span></label
				>
				<textarea
					id="ndm-doc-content"
					class="ndm-textarea"
					class:ndm-input--error={!!contentError}
					rows="10"
					bind:value={contentMd}
					placeholder="Paste markdown or plain text…"
					disabled={submitting}
					data-testid="lq-ai-mgmt-docs-new-content"
					aria-describedby={contentError ? 'ndm-content-error' : undefined}
				></textarea>
				{#if contentError}
					<p id="ndm-content-error" class="ndm-field-error" role="alert">{contentError}</p>
				{/if}
			</div>

			{#if submitError}
				<p class="ndm-submit-error" role="alert">{submitError}</p>
			{/if}

			<div class="ndm-actions">
				<button type="button" class="ndm-btn-secondary" on:click={onClose} disabled={submitting}>
					Cancel
				</button>
				<button
					type="submit"
					class="ndm-btn-primary"
					disabled={submitting}
					data-testid="lq-ai-mgmt-docs-new-submit"
				>
					{submitting ? 'Adding document…' : 'Add document'}
				</button>
			</div>
		</form>
	</div>
</div>

<style>
	.ndm-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.35);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}

	.ndm-panel {
		background: var(--lq-canvas);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-6);
		max-width: 640px;
		width: calc(100% - 32px);
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
		max-height: calc(100vh - 64px);
		overflow-y: auto;
	}

	.ndm-title {
		margin: 0 0 var(--lq-space-4);
	}

	.ndm-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-4);
	}

	.ndm-row {
		display: flex;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
	}

	.ndm-row .ndm-field {
		flex: 1;
		min-width: 160px;
	}

	.ndm-field {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.ndm-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.ndm-required {
		color: var(--lq-error);
		margin-left: 2px;
	}

	.ndm-optional {
		font-weight: 400;
		color: var(--lq-text-tertiary);
		font-size: 12px;
	}

	.ndm-input,
	.ndm-textarea {
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		font-size: 14px;
		color: var(--lq-text-primary);
		width: 100%;
		box-sizing: border-box;
		transition: border-color 0.15s ease;
	}

	.ndm-textarea {
		resize: vertical;
		font-family: var(--lq-font-mono, ui-monospace, monospace);
		font-size: 13px;
		line-height: 1.55;
	}

	.ndm-input:focus,
	.ndm-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.ndm-input--error {
		border-color: var(--lq-error);
	}

	.ndm-input--error:focus {
		box-shadow: 0 0 0 2px var(--lq-error-soft);
	}

	.ndm-field-error {
		font-size: 12px;
		color: var(--lq-error);
		margin: 0;
	}

	.ndm-submit-error {
		font-size: 13px;
		color: var(--lq-error);
		background: var(--lq-error-soft);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		margin: 0;
	}

	.ndm-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-3);
		padding-top: var(--lq-space-2);
		border-top: 1px solid var(--lq-border);
		margin-top: var(--lq-space-2);
	}

	.ndm-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.ndm-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.ndm-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.ndm-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.ndm-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.ndm-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.ndm-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.ndm-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}
</style>
