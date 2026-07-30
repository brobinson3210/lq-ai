<script context="module" lang="ts">
	/**
	 * Form validation helpers — exported for unit tests.
	 */
	import type { KpiDepartment } from '../types';

	export interface NewTeamMemberFields {
		name: string;
		role_title: string;
		department: KpiDepartment | '';
	}

	export interface TeamMemberValidationResult {
		valid: boolean;
		nameError: string | null;
		roleError: string | null;
		departmentError: string | null;
	}

	export function validateNewTeamMember(fields: NewTeamMemberFields): TeamMemberValidationResult {
		const nameError = !fields.name.trim()
			? 'A name is required.'
			: fields.name.trim().length > 200
				? 'The name must be 200 characters or fewer.'
				: null;
		const roleError = !fields.role_title.trim() ? 'A role / title is required.' : null;
		const departmentError = !fields.department ? 'Pick a department.' : null;
		return {
			valid: nameError === null && roleError === null && departmentError === null,
			nameError,
			roleError,
			departmentError
		};
	}
</script>

<script lang="ts">
	import { managementKpisApi } from '$lib/lq-ai/api';
	import type { TeamMemberRead } from '$lib/lq-ai/types';
	import { DEPARTMENT_OPTIONS } from '$lib/lq-ai/management/kpis';

	export let onClose: () => void;
	export let onCreated: (member: TeamMemberRead) => void;

	// Form state
	let name = '';
	let roleTitle = '';
	let department: KpiDepartment | '' = '';
	let seniority = '';
	let strengths = '';
	let developmentAreas = '';
	let notes = '';

	// UI state
	let submitting = false;
	let nameError: string | null = null;
	let roleError: string | null = null;
	let departmentError: string | null = null;
	let submitError: string | null = null;

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
	}

	let nameInput: HTMLInputElement;
	$: if (nameInput) nameInput.focus();

	async function handleSubmit() {
		submitError = null;
		const result = validateNewTeamMember({ name, role_title: roleTitle, department });
		nameError = result.nameError;
		roleError = result.roleError;
		departmentError = result.departmentError;
		if (!result.valid || !department) return;

		submitting = true;
		try {
			const created = await managementKpisApi.createTeamMember({
				name: name.trim(),
				role_title: roleTitle.trim(),
				department,
				seniority: seniority.trim() || undefined,
				strengths_md: strengths.trim() || undefined,
				development_areas_md: developmentAreas.trim() || undefined,
				notes_md: notes.trim() || undefined
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
	class="ntm-backdrop"
	role="dialog"
	aria-modal="true"
	aria-labelledby="ntm-title"
	tabindex="-1"
	data-testid="lq-ai-mgmt-team-new-modal"
	on:click={onClose}
	on:keydown={handleKeydown}
>
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div class="ntm-panel" on:click|stopPropagation on:keydown|stopPropagation>
		<h2 id="ntm-title" class="lq-text-page-h ntm-title">New team member</h2>

		<form on:submit|preventDefault={handleSubmit} class="ntm-form" novalidate>
			<div class="ntm-field">
				<label class="ntm-label" for="ntm-name"
					>Name <span class="ntm-required" aria-hidden="true">*</span></label
				>
				<input
					id="ntm-name"
					type="text"
					bind:this={nameInput}
					bind:value={name}
					class="ntm-input"
					class:ntm-input--error={!!nameError}
					placeholder="e.g. Priya Raman"
					maxlength="200"
					required
					disabled={submitting}
					data-testid="lq-ai-mgmt-team-new-name"
					aria-describedby={nameError ? 'ntm-name-error' : undefined}
				/>
				{#if nameError}
					<p id="ntm-name-error" class="ntm-field-error" role="alert">{nameError}</p>
				{/if}
			</div>

			<div class="ntm-row">
				<div class="ntm-field">
					<label class="ntm-label" for="ntm-role"
						>Role / title <span class="ntm-required" aria-hidden="true">*</span></label
					>
					<input
						id="ntm-role"
						type="text"
						bind:value={roleTitle}
						class="ntm-input"
						class:ntm-input--error={!!roleError}
						placeholder="e.g. Senior Counsel, Commercial"
						maxlength="200"
						disabled={submitting}
						data-testid="lq-ai-mgmt-team-new-role"
						aria-describedby={roleError ? 'ntm-role-error' : undefined}
					/>
					{#if roleError}
						<p id="ntm-role-error" class="ntm-field-error" role="alert">{roleError}</p>
					{/if}
				</div>

				<div class="ntm-field">
					<label class="ntm-label" for="ntm-department"
						>Department <span class="ntm-required" aria-hidden="true">*</span></label
					>
					<select
						id="ntm-department"
						class="ntm-select"
						class:ntm-input--error={!!departmentError}
						bind:value={department}
						disabled={submitting}
						data-testid="lq-ai-mgmt-team-new-department"
						aria-describedby={departmentError ? 'ntm-department-error' : undefined}
					>
						<option value="">Select…</option>
						{#each DEPARTMENT_OPTIONS as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
					{#if departmentError}
						<p id="ntm-department-error" class="ntm-field-error" role="alert">
							{departmentError}
						</p>
					{/if}
				</div>
			</div>

			<div class="ntm-field">
				<label class="ntm-label" for="ntm-seniority"
					>Seniority <span class="ntm-optional">(optional)</span></label
				>
				<input
					id="ntm-seniority"
					type="text"
					bind:value={seniority}
					class="ntm-input"
					maxlength="100"
					placeholder="e.g. Senior, 8 yrs PQE"
					disabled={submitting}
				/>
			</div>

			<div class="ntm-field">
				<label class="ntm-label" for="ntm-strengths"
					>Strengths <span class="ntm-optional">(optional)</span></label
				>
				<textarea
					id="ntm-strengths"
					class="ntm-textarea"
					rows="2"
					bind:value={strengths}
					disabled={submitting}
				></textarea>
			</div>

			<div class="ntm-field">
				<label class="ntm-label" for="ntm-development"
					>Development areas <span class="ntm-optional">(optional)</span></label
				>
				<textarea
					id="ntm-development"
					class="ntm-textarea"
					rows="2"
					bind:value={developmentAreas}
					disabled={submitting}
				></textarea>
			</div>

			<div class="ntm-field">
				<label class="ntm-label" for="ntm-notes"
					>Notes <span class="ntm-optional">(optional)</span></label
				>
				<textarea
					id="ntm-notes"
					class="ntm-textarea"
					rows="2"
					bind:value={notes}
					disabled={submitting}
				></textarea>
			</div>

			{#if submitError}
				<p class="ntm-submit-error" role="alert">{submitError}</p>
			{/if}

			<div class="ntm-actions">
				<button type="button" class="ntm-btn-secondary" on:click={onClose} disabled={submitting}>
					Cancel
				</button>
				<button
					type="submit"
					class="ntm-btn-primary"
					disabled={submitting}
					data-testid="lq-ai-mgmt-team-new-submit"
				>
					{submitting ? 'Adding member…' : 'Add member'}
				</button>
			</div>
		</form>
	</div>
</div>

<style>
	.ntm-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.35);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}

	.ntm-panel {
		background: var(--lq-canvas);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-6);
		max-width: 560px;
		width: calc(100% - 32px);
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
		max-height: calc(100vh - 64px);
		overflow-y: auto;
	}

	.ntm-title {
		margin: 0 0 var(--lq-space-4);
	}

	.ntm-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-4);
	}

	.ntm-row {
		display: flex;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
	}

	.ntm-row .ntm-field {
		flex: 1;
		min-width: 160px;
	}

	.ntm-field {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.ntm-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.ntm-required {
		color: var(--lq-error);
		margin-left: 2px;
	}

	.ntm-optional {
		font-weight: 400;
		color: var(--lq-text-tertiary);
		font-size: 12px;
	}

	.ntm-input,
	.ntm-select,
	.ntm-textarea {
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

	.ntm-textarea {
		resize: vertical;
	}

	.ntm-input:focus,
	.ntm-select:focus,
	.ntm-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.ntm-input--error {
		border-color: var(--lq-error);
	}

	.ntm-input--error:focus {
		box-shadow: 0 0 0 2px var(--lq-error-soft);
	}

	.ntm-field-error {
		font-size: 12px;
		color: var(--lq-error);
		margin: 0;
	}

	.ntm-submit-error {
		font-size: 13px;
		color: var(--lq-error);
		background: var(--lq-error-soft);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		margin: 0;
	}

	.ntm-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-3);
		padding-top: var(--lq-space-2);
		border-top: 1px solid var(--lq-border);
		margin-top: var(--lq-space-2);
	}

	.ntm-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.ntm-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.ntm-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.ntm-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.ntm-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.ntm-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.ntm-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.ntm-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}
</style>
