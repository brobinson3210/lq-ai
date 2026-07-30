<script context="module" lang="ts">
	/**
	 * Form validation helpers — exported for unit tests.
	 */
	import type { StakeholderHealth, StakeholderType } from '../types';

	export interface NewStakeholderFields {
		full_name: string;
		stakeholder_type: StakeholderType | '';
		cadence_target_days: string;
	}

	export interface StakeholderValidationResult {
		valid: boolean;
		nameError: string | null;
		typeError: string | null;
		cadenceError: string | null;
	}

	export function validateNewStakeholder(
		fields: NewStakeholderFields
	): StakeholderValidationResult {
		let nameError: string | null = null;
		let typeError: string | null = null;
		let cadenceError: string | null = null;

		if (!fields.full_name.trim()) {
			nameError = 'Full name is required.';
		} else if (fields.full_name.trim().length > 200) {
			nameError = 'Full name must be 200 characters or fewer.';
		}

		if (!fields.stakeholder_type) {
			typeError = 'Stakeholder type is required.';
		}

		if (fields.cadence_target_days.trim()) {
			const n = Number(fields.cadence_target_days);
			if (!Number.isInteger(n) || n < 1) {
				cadenceError = 'Cadence must be a whole number of days (1 or more).';
			}
		}

		return {
			valid: nameError === null && typeError === null && cadenceError === null,
			nameError,
			typeError,
			cadenceError
		};
	}
</script>

<script lang="ts">
	import { goto } from '$app/navigation';
	import { stakeholdersApi } from '$lib/lq-ai/api';
	import type { Stakeholder } from '$lib/lq-ai/types';
	import {
		BOARD_TYPES,
		HEALTH_OPTIONS,
		STAKEHOLDER_TYPE_OPTIONS
	} from '$lib/lq-ai/management/stakeholders';

	export let onClose: () => void;
	export let onCreated: (stakeholder: Stakeholder) => void;

	// Form state
	let fullName = '';
	let stakeholderType: StakeholderType | '' = '';
	let organization = '';
	let roleTitle = '';
	let committeeSeats = '';
	let cadenceTargetDays = '';
	let overallHealth: StakeholderHealth | '' = '';

	// UI state
	let submitting = false;
	let nameError: string | null = null;
	let typeError: string | null = null;
	let cadenceError: string | null = null;
	let submitError: string | null = null;

	$: showCommitteeSeats =
		stakeholderType !== '' && BOARD_TYPES.includes(stakeholderType as StakeholderType);

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
	}

	let nameInput: HTMLInputElement;

	// Focus the name input when modal opens
	$: if (nameInput) nameInput.focus();

	async function handleSubmit() {
		nameError = null;
		typeError = null;
		cadenceError = null;
		submitError = null;

		const result = validateNewStakeholder({
			full_name: fullName,
			stakeholder_type: stakeholderType,
			cadence_target_days: cadenceTargetDays
		});
		nameError = result.nameError;
		typeError = result.typeError;
		cadenceError = result.cadenceError;
		if (!result.valid || !stakeholderType) return;

		submitting = true;
		try {
			const created = await stakeholdersApi.createStakeholder({
				full_name: fullName.trim(),
				stakeholder_type: stakeholderType,
				organization: organization.trim() || undefined,
				role_title: roleTitle.trim() || undefined,
				committee_seats:
					showCommitteeSeats && committeeSeats.trim() ? committeeSeats.trim() : undefined,
				cadence_target_days: cadenceTargetDays.trim() ? Number(cadenceTargetDays) : undefined,
				overall_health: overallHealth || undefined
			});
			onCreated(created);
			goto(`/lq-ai/management/stakeholders/${created.id}`);
		} catch (e: unknown) {
			if (e instanceof Error) {
				submitError = e.message ?? "Couldn't reach the server. Try again.";
			} else {
				submitError = "Couldn't reach the server. Try again.";
			}
		} finally {
			submitting = false;
		}
	}
</script>

<div
	class="nsm-backdrop"
	role="dialog"
	aria-modal="true"
	aria-labelledby="nsm-title"
	tabindex="-1"
	data-testid="lq-ai-mgmt-stakeholders-new-modal"
	on:click={onClose}
	on:keydown={handleKeydown}
>
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div class="nsm-panel" on:click|stopPropagation on:keydown|stopPropagation>
		<h2 id="nsm-title" class="lq-text-page-h nsm-title">New stakeholder</h2>

		<form on:submit|preventDefault={handleSubmit} class="nsm-form" novalidate>
			<!-- Full name -->
			<div class="nsm-field">
				<label class="nsm-label" for="nsm-name"
					>Full name <span class="nsm-required" aria-hidden="true">*</span></label
				>
				<input
					id="nsm-name"
					type="text"
					bind:this={nameInput}
					bind:value={fullName}
					class="nsm-input"
					class:nsm-input--error={!!nameError}
					placeholder="e.g. Margaret Chen"
					maxlength="200"
					required
					disabled={submitting}
					data-testid="lq-ai-mgmt-stakeholders-new-name"
					aria-describedby={nameError ? 'nsm-name-error' : undefined}
				/>
				{#if nameError}
					<p id="nsm-name-error" class="nsm-field-error" role="alert">{nameError}</p>
				{/if}
			</div>

			<!-- Type -->
			<div class="nsm-field">
				<label class="nsm-label" for="nsm-type"
					>Stakeholder type <span class="nsm-required" aria-hidden="true">*</span></label
				>
				<select
					id="nsm-type"
					class="nsm-select"
					class:nsm-input--error={!!typeError}
					bind:value={stakeholderType}
					disabled={submitting}
					data-testid="lq-ai-mgmt-stakeholders-new-type"
					aria-describedby={typeError ? 'nsm-type-error' : undefined}
				>
					<option value="">Select a type…</option>
					{#each STAKEHOLDER_TYPE_OPTIONS as opt (opt.value)}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>
				{#if typeError}
					<p id="nsm-type-error" class="nsm-field-error" role="alert">{typeError}</p>
				{/if}
			</div>

			<!-- Organization -->
			<div class="nsm-field">
				<label class="nsm-label" for="nsm-org"
					>Organization <span class="nsm-optional">(optional)</span></label
				>
				<input
					id="nsm-org"
					type="text"
					bind:value={organization}
					class="nsm-input"
					maxlength="200"
					placeholder="e.g. Meridian Capital"
					disabled={submitting}
				/>
			</div>

			<!-- Role / title -->
			<div class="nsm-field">
				<label class="nsm-label" for="nsm-role"
					>Role / title <span class="nsm-optional">(optional)</span></label
				>
				<input
					id="nsm-role"
					type="text"
					bind:value={roleTitle}
					class="nsm-input"
					maxlength="200"
					placeholder="e.g. Audit Committee Chair"
					disabled={submitting}
				/>
			</div>

			<!-- Committee seats — board types only -->
			{#if showCommitteeSeats}
				<div class="nsm-field">
					<label class="nsm-label" for="nsm-seats"
						>Committee seats <span class="nsm-optional">(optional)</span></label
					>
					<input
						id="nsm-seats"
						type="text"
						bind:value={committeeSeats}
						class="nsm-input"
						maxlength="200"
						placeholder="e.g. Audit, Compensation"
						disabled={submitting}
					/>
				</div>
			{/if}

			<!-- Cadence target -->
			<div class="nsm-field">
				<label class="nsm-label" for="nsm-cadence"
					>Touch cadence target, days <span class="nsm-optional">(optional)</span></label
				>
				<input
					id="nsm-cadence"
					type="number"
					min="1"
					step="1"
					bind:value={cadenceTargetDays}
					class="nsm-input"
					class:nsm-input--error={!!cadenceError}
					placeholder="e.g. 30"
					disabled={submitting}
					aria-describedby={cadenceError ? 'nsm-cadence-error' : undefined}
				/>
				{#if cadenceError}
					<p id="nsm-cadence-error" class="nsm-field-error" role="alert">{cadenceError}</p>
				{/if}
			</div>

			<!-- Overall health -->
			<div class="nsm-field">
				<label class="nsm-label" for="nsm-health"
					>Overall health <span class="nsm-optional">(optional)</span></label
				>
				<select id="nsm-health" class="nsm-select" bind:value={overallHealth} disabled={submitting}>
					<option value="">Unset</option>
					{#each HEALTH_OPTIONS as opt (opt.value)}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>
			</div>

			<!-- Submit error -->
			{#if submitError}
				<p class="nsm-submit-error" role="alert">{submitError}</p>
			{/if}

			<!-- Actions -->
			<div class="nsm-actions">
				<button type="button" class="nsm-btn-secondary" on:click={onClose} disabled={submitting}>
					Cancel
				</button>
				<button
					type="submit"
					class="nsm-btn-primary"
					disabled={submitting}
					data-testid="lq-ai-mgmt-stakeholders-new-submit"
				>
					{submitting ? 'Creating stakeholder…' : 'Create stakeholder'}
				</button>
			</div>
		</form>
	</div>
</div>

<style>
	.nsm-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.35);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}

	.nsm-panel {
		background: var(--lq-canvas);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-6);
		max-width: 520px;
		width: calc(100% - 32px);
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
		max-height: calc(100vh - 64px);
		overflow-y: auto;
	}

	.nsm-title {
		margin: 0 0 var(--lq-space-4);
	}

	.nsm-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-4);
	}

	.nsm-field {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.nsm-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.nsm-required {
		color: var(--lq-error);
		margin-left: 2px;
	}

	.nsm-optional {
		font-weight: 400;
		color: var(--lq-text-tertiary);
		font-size: 12px;
	}

	.nsm-input,
	.nsm-select {
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

	.nsm-input:focus,
	.nsm-select:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.nsm-input--error {
		border-color: var(--lq-error);
	}

	.nsm-input--error:focus {
		box-shadow: 0 0 0 2px var(--lq-error-soft);
	}

	.nsm-field-error {
		font-size: 12px;
		color: var(--lq-error);
		margin: 0;
	}

	.nsm-submit-error {
		font-size: 13px;
		color: var(--lq-error);
		background: var(--lq-error-soft);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		margin: 0;
	}

	.nsm-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-3);
		padding-top: var(--lq-space-2);
		border-top: 1px solid var(--lq-border);
		margin-top: var(--lq-space-2);
	}

	.nsm-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.nsm-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.nsm-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.nsm-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.nsm-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.nsm-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.nsm-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.nsm-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}
</style>
