<script context="module" lang="ts">
	/**
	 * Form validation helpers — exported for unit tests.
	 */
	import type { KpiDepartment, KpiScope } from '../types';

	export interface NewKpiFields {
		name: string;
		department: KpiDepartment | '';
		scope: KpiScope | '';
		team_member_id: string;
		unit: string;
		baseline: string;
		target: string;
	}

	export interface KpiValidationResult {
		valid: boolean;
		nameError: string | null;
		departmentError: string | null;
		scopeError: string | null;
		memberError: string | null;
		unitError: string | null;
		baselineError: string | null;
		targetError: string | null;
	}

	function numericError(raw: string, label: string): string | null {
		if (!raw.trim()) return null;
		return Number.isFinite(Number(raw.trim())) ? null : `${label} must be a number.`;
	}

	export function validateNewKpi(fields: NewKpiFields): KpiValidationResult {
		const nameError = !fields.name.trim()
			? 'A KPI name is required.'
			: fields.name.trim().length > 200
				? 'The name must be 200 characters or fewer.'
				: null;
		const departmentError = !fields.department ? 'Pick a department.' : null;
		const scopeError = !fields.scope ? 'Pick a scope.' : null;
		const memberError =
			fields.scope === 'individual' && !fields.team_member_id
				? 'An individual KPI needs a team member.'
				: null;
		const unitError = !fields.unit.trim() ? 'A unit is required (%, USD, days, count…).' : null;
		const baselineError = numericError(fields.baseline, 'Baseline');
		const targetError = numericError(fields.target, 'Target');

		return {
			valid:
				nameError === null &&
				departmentError === null &&
				scopeError === null &&
				memberError === null &&
				unitError === null &&
				baselineError === null &&
				targetError === null,
			nameError,
			departmentError,
			scopeError,
			memberError,
			unitError,
			baselineError,
			targetError
		};
	}
</script>

<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { managementKpisApi } from '$lib/lq-ai/api';
	import type { KpiCadence, KpiDirection, KpiRead, TeamMemberRead } from '$lib/lq-ai/types';
	import {
		CADENCE_OPTIONS,
		DEPARTMENT_OPTIONS,
		DIRECTION_OPTIONS,
		SCOPE_OPTIONS
	} from '$lib/lq-ai/management/kpis';

	export let onClose: () => void;
	export let onCreated: (kpi: KpiRead) => void;
	/** Pre-select a member (roster page "add KPI for this person" path). */
	export let initialTeamMemberId: string | null = null;

	// Form state
	let name = '';
	let department: KpiDepartment | '' = '';
	let scope: KpiScope | '' = initialTeamMemberId ? 'individual' : '';
	let teamMemberId = initialTeamMemberId ?? '';
	let unit = '';
	let cadence: KpiCadence = 'monthly';
	let direction: KpiDirection = 'higher_is_better';
	let baseline = '';
	let target = '';
	let rationale = '';

	// Roster for the individual-scope member select
	let members: TeamMemberRead[] = [];
	let membersError: string | null = null;

	onMount(async () => {
		try {
			members = await managementKpisApi.listTeamMembers();
		} catch (e) {
			membersError = e instanceof Error ? e.message : 'Failed to load team members';
		}
	});

	// UI state
	let submitting = false;
	let nameError: string | null = null;
	let departmentError: string | null = null;
	let scopeError: string | null = null;
	let memberError: string | null = null;
	let unitError: string | null = null;
	let baselineError: string | null = null;
	let targetError: string | null = null;
	let submitError: string | null = null;

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
	}

	let nameInput: HTMLInputElement;
	$: if (nameInput) nameInput.focus();

	async function handleSubmit() {
		submitError = null;
		const result = validateNewKpi({
			name,
			department,
			scope,
			team_member_id: teamMemberId,
			unit,
			baseline,
			target
		});
		nameError = result.nameError;
		departmentError = result.departmentError;
		scopeError = result.scopeError;
		memberError = result.memberError;
		unitError = result.unitError;
		baselineError = result.baselineError;
		targetError = result.targetError;
		if (!result.valid || !department || !scope) return;

		submitting = true;
		try {
			const created = await managementKpisApi.createKpi({
				name: name.trim(),
				department,
				scope,
				team_member_id: scope === 'individual' ? teamMemberId : undefined,
				unit: unit.trim(),
				cadence,
				direction,
				baseline: baseline.trim() || undefined,
				target: target.trim() || undefined,
				rationale_md: rationale.trim() || undefined
			});
			onCreated(created);
			goto(`/lq-ai/management/kpis/${created.id}`);
		} catch (e: unknown) {
			submitError = e instanceof Error ? e.message : "Couldn't reach the server. Try again.";
		} finally {
			submitting = false;
		}
	}
</script>

<div
	class="nkm-backdrop"
	role="dialog"
	aria-modal="true"
	aria-labelledby="nkm-title"
	tabindex="-1"
	data-testid="lq-ai-mgmt-kpis-new-modal"
	on:click={onClose}
	on:keydown={handleKeydown}
>
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div class="nkm-panel" on:click|stopPropagation on:keydown|stopPropagation>
		<h2 id="nkm-title" class="lq-text-page-h nkm-title">New KPI</h2>

		<form on:submit|preventDefault={handleSubmit} class="nkm-form" novalidate>
			<div class="nkm-field">
				<label class="nkm-label" for="nkm-name"
					>Name <span class="nkm-required" aria-hidden="true">*</span></label
				>
				<input
					id="nkm-name"
					type="text"
					bind:this={nameInput}
					bind:value={name}
					class="nkm-input"
					class:nkm-input--error={!!nameError}
					placeholder="e.g. Contract turnaround time"
					maxlength="200"
					required
					disabled={submitting}
					data-testid="lq-ai-mgmt-kpis-new-name"
					aria-describedby={nameError ? 'nkm-name-error' : undefined}
				/>
				{#if nameError}
					<p id="nkm-name-error" class="nkm-field-error" role="alert">{nameError}</p>
				{/if}
			</div>

			<div class="nkm-row">
				<div class="nkm-field">
					<label class="nkm-label" for="nkm-department"
						>Department <span class="nkm-required" aria-hidden="true">*</span></label
					>
					<select
						id="nkm-department"
						class="nkm-select"
						class:nkm-input--error={!!departmentError}
						bind:value={department}
						disabled={submitting}
						data-testid="lq-ai-mgmt-kpis-new-department"
						aria-describedby={departmentError ? 'nkm-department-error' : undefined}
					>
						<option value="">Select…</option>
						{#each DEPARTMENT_OPTIONS as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
					{#if departmentError}
						<p id="nkm-department-error" class="nkm-field-error" role="alert">{departmentError}</p>
					{/if}
				</div>

				<div class="nkm-field">
					<label class="nkm-label" for="nkm-scope"
						>Scope <span class="nkm-required" aria-hidden="true">*</span></label
					>
					<select
						id="nkm-scope"
						class="nkm-select"
						class:nkm-input--error={!!scopeError}
						bind:value={scope}
						disabled={submitting}
						data-testid="lq-ai-mgmt-kpis-new-scope"
						aria-describedby={scopeError ? 'nkm-scope-error' : undefined}
					>
						<option value="">Select…</option>
						{#each SCOPE_OPTIONS as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
					{#if scopeError}
						<p id="nkm-scope-error" class="nkm-field-error" role="alert">{scopeError}</p>
					{/if}
				</div>
			</div>

			{#if scope === 'individual'}
				<div class="nkm-field">
					<label class="nkm-label" for="nkm-member"
						>Team member <span class="nkm-required" aria-hidden="true">*</span></label
					>
					<select
						id="nkm-member"
						class="nkm-select"
						class:nkm-input--error={!!memberError}
						bind:value={teamMemberId}
						disabled={submitting}
						data-testid="lq-ai-mgmt-kpis-new-member"
						aria-describedby={memberError ? 'nkm-member-error' : undefined}
					>
						<option value="">Select a member…</option>
						{#each members as m (m.id)}
							<option value={m.id}>{m.name} — {m.role_title}</option>
						{/each}
					</select>
					{#if membersError}
						<p class="nkm-field-error" role="alert">{membersError}</p>
					{/if}
					{#if memberError}
						<p id="nkm-member-error" class="nkm-field-error" role="alert">{memberError}</p>
					{/if}
				</div>
			{/if}

			<div class="nkm-row">
				<div class="nkm-field">
					<label class="nkm-label" for="nkm-unit"
						>Unit <span class="nkm-required" aria-hidden="true">*</span></label
					>
					<input
						id="nkm-unit"
						type="text"
						bind:value={unit}
						class="nkm-input"
						class:nkm-input--error={!!unitError}
						placeholder="%, USD, days, count…"
						maxlength="32"
						disabled={submitting}
						data-testid="lq-ai-mgmt-kpis-new-unit"
						aria-describedby={unitError ? 'nkm-unit-error' : undefined}
					/>
					{#if unitError}
						<p id="nkm-unit-error" class="nkm-field-error" role="alert">{unitError}</p>
					{/if}
				</div>

				<div class="nkm-field">
					<label class="nkm-label" for="nkm-cadence">Cadence</label>
					<select id="nkm-cadence" class="nkm-select" bind:value={cadence} disabled={submitting}>
						{#each CADENCE_OPTIONS as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
				</div>

				<div class="nkm-field">
					<label class="nkm-label" for="nkm-direction">Direction</label>
					<select
						id="nkm-direction"
						class="nkm-select"
						bind:value={direction}
						disabled={submitting}
					>
						{#each DIRECTION_OPTIONS as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
				</div>
			</div>

			<div class="nkm-row">
				<div class="nkm-field">
					<label class="nkm-label" for="nkm-baseline"
						>Baseline <span class="nkm-optional">(optional)</span></label
					>
					<input
						id="nkm-baseline"
						type="text"
						inputmode="decimal"
						bind:value={baseline}
						class="nkm-input"
						class:nkm-input--error={!!baselineError}
						placeholder="e.g. 12.5"
						disabled={submitting}
						aria-describedby={baselineError ? 'nkm-baseline-error' : undefined}
					/>
					{#if baselineError}
						<p id="nkm-baseline-error" class="nkm-field-error" role="alert">{baselineError}</p>
					{/if}
				</div>

				<div class="nkm-field">
					<label class="nkm-label" for="nkm-target"
						>Target <span class="nkm-optional">(optional)</span></label
					>
					<input
						id="nkm-target"
						type="text"
						inputmode="decimal"
						bind:value={target}
						class="nkm-input"
						class:nkm-input--error={!!targetError}
						placeholder="e.g. 7"
						disabled={submitting}
						data-testid="lq-ai-mgmt-kpis-new-target"
						aria-describedby={targetError ? 'nkm-target-error' : undefined}
					/>
					{#if targetError}
						<p id="nkm-target-error" class="nkm-field-error" role="alert">{targetError}</p>
					{/if}
				</div>
			</div>

			<div class="nkm-field">
				<label class="nkm-label" for="nkm-rationale"
					>Why this number predicts success <span class="nkm-optional">(optional)</span></label
				>
				<textarea
					id="nkm-rationale"
					class="nkm-textarea"
					rows="3"
					bind:value={rationale}
					placeholder="The rationale the C-suite will actually read…"
					disabled={submitting}
				></textarea>
			</div>

			{#if submitError}
				<p class="nkm-submit-error" role="alert">{submitError}</p>
			{/if}

			<div class="nkm-actions">
				<button type="button" class="nkm-btn-secondary" on:click={onClose} disabled={submitting}>
					Cancel
				</button>
				<button
					type="submit"
					class="nkm-btn-primary"
					disabled={submitting}
					data-testid="lq-ai-mgmt-kpis-new-submit"
				>
					{submitting ? 'Creating KPI…' : 'Create KPI'}
				</button>
			</div>
		</form>
	</div>
</div>

<style>
	.nkm-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.35);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}

	.nkm-panel {
		background: var(--lq-canvas);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-6);
		max-width: 560px;
		width: calc(100% - 32px);
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
		max-height: calc(100vh - 64px);
		overflow-y: auto;
	}

	.nkm-title {
		margin: 0 0 var(--lq-space-4);
	}

	.nkm-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-4);
	}

	.nkm-row {
		display: flex;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
	}

	.nkm-row .nkm-field {
		flex: 1;
		min-width: 140px;
	}

	.nkm-field {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.nkm-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.nkm-required {
		color: var(--lq-error);
		margin-left: 2px;
	}

	.nkm-optional {
		font-weight: 400;
		color: var(--lq-text-tertiary);
		font-size: 12px;
	}

	.nkm-input,
	.nkm-select,
	.nkm-textarea {
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

	.nkm-textarea {
		resize: vertical;
	}

	.nkm-input:focus,
	.nkm-select:focus,
	.nkm-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.nkm-input--error {
		border-color: var(--lq-error);
	}

	.nkm-input--error:focus {
		box-shadow: 0 0 0 2px var(--lq-error-soft);
	}

	.nkm-field-error {
		font-size: 12px;
		color: var(--lq-error);
		margin: 0;
	}

	.nkm-submit-error {
		font-size: 13px;
		color: var(--lq-error);
		background: var(--lq-error-soft);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		margin: 0;
	}

	.nkm-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-3);
		padding-top: var(--lq-space-2);
		border-top: 1px solid var(--lq-border);
		margin-top: var(--lq-space-2);
	}

	.nkm-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.nkm-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.nkm-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.nkm-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.nkm-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.nkm-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.nkm-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.nkm-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}
</style>
