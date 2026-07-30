<!--
  /lq-ai/management/commitments — the cross-stakeholder commitments rollup.

  "What do I owe the board this week." Defaults to we_owe + open; rows are
  grouped by stakeholder with overdue due-dates highlighted and an inline
  status select (flat PATCH /stakeholder-commitments/{id}).
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { stakeholdersApi } from '$lib/lq-ai/api';
	import type {
		CommitmentDirection,
		CommitmentStatus,
		StakeholderCommitmentRollupRow
	} from '$lib/lq-ai/types';
	import { formatDate, isOverdue, typeLabel } from '$lib/lq-ai/management/stakeholders';

	let rows: StakeholderCommitmentRollupRow[] = [];
	let loading = true;
	let error: string | null = null;

	let direction: CommitmentDirection = 'we_owe';
	let status: CommitmentStatus = 'open';

	const DIRECTION_CHIPS: { value: CommitmentDirection; label: string }[] = [
		{ value: 'we_owe', label: 'We owe' },
		{ value: 'they_owe', label: 'They owe' }
	];

	const STATUS_CHIPS: { value: CommitmentStatus; label: string }[] = [
		{ value: 'open', label: 'Open' },
		{ value: 'done', label: 'Done' },
		{ value: 'dropped', label: 'Dropped' }
	];

	async function refresh() {
		loading = true;
		try {
			rows = await stakeholdersApi.listCommitmentRollup({ direction, status });
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load commitments';
		} finally {
			loading = false;
		}
	}

	onMount(refresh);

	function setDirection(d: CommitmentDirection) {
		direction = d;
		void refresh();
	}

	function setStatus(s: CommitmentStatus) {
		status = s;
		void refresh();
	}

	interface StakeholderGroup {
		stakeholderId: string;
		fullName: string;
		stakeholderType: string;
		rows: StakeholderCommitmentRollupRow[];
	}

	function groupByStakeholder(all: StakeholderCommitmentRollupRow[]): StakeholderGroup[] {
		const map = new Map<string, StakeholderGroup>();
		for (const row of all) {
			const g = map.get(row.stakeholder_id);
			if (g) {
				g.rows.push(row);
			} else {
				map.set(row.stakeholder_id, {
					stakeholderId: row.stakeholder_id,
					fullName: row.full_name,
					stakeholderType: row.stakeholder_type,
					rows: [row]
				});
			}
		}
		return [...map.values()];
	}

	$: groups = groupByStakeholder(rows);

	let rowError: string | null = null;

	async function changeStatus(row: StakeholderCommitmentRollupRow, e: Event) {
		const next = (e.currentTarget as HTMLSelectElement).value as CommitmentStatus;
		rowError = null;
		try {
			await stakeholdersApi.patchCommitment(row.id, { status: next });
			await refresh();
		} catch (err) {
			rowError = err instanceof Error ? err.message : 'Failed to update commitment';
		}
	}
</script>

<main class="cmt-page" data-testid="lq-ai-mgmt-commitments-page">
	<a class="cmt-back" href="/lq-ai/management/stakeholders">← Stakeholders</a>

	<header class="cmt-header">
		<h1 class="lq-text-page-h">Commitments</h1>
		<p class="cmt-sub lq-text-body">
			Everything promised, in both directions — never drop a ball, never surprise a director.
		</p>
	</header>

	<div class="cmt-filters">
		<div class="cmt-chip-group" role="group" aria-label="Filter by direction">
			{#each DIRECTION_CHIPS as chip (chip.value)}
				<button
					type="button"
					class="cmt-chip"
					class:cmt-chip--active={direction === chip.value}
					aria-pressed={direction === chip.value}
					data-testid={`lq-ai-mgmt-commitments-direction-${chip.value}`}
					on:click={() => setDirection(chip.value)}
				>
					{chip.label}
				</button>
			{/each}
		</div>
		<div class="cmt-chip-group" role="group" aria-label="Filter by status">
			{#each STATUS_CHIPS as chip (chip.value)}
				<button
					type="button"
					class="cmt-chip"
					class:cmt-chip--active={status === chip.value}
					aria-pressed={status === chip.value}
					data-testid={`lq-ai-mgmt-commitments-status-${chip.value}`}
					on:click={() => setStatus(chip.value)}
				>
					{chip.label}
				</button>
			{/each}
		</div>
	</div>

	{#if rowError}
		<p class="cmt-inline-error" role="alert">{rowError}</p>
	{/if}

	{#if loading}
		<p class="lq-text-body cmt-state-msg">Loading commitments…</p>
	{:else if error}
		<p class="lq-text-body cmt-state-msg cmt-state-msg--error" role="alert">
			Couldn't load commitments: {error}
		</p>
	{:else if groups.length === 0}
		<section class="cmt-empty" data-testid="lq-ai-mgmt-commitments-empty">
			<p class="lq-text-body cmt-empty__copy">
				{status === 'open'
					? direction === 'we_owe'
						? 'Nothing owed — every promise is either kept or logged elsewhere.'
						: 'Nothing outstanding from your stakeholders.'
					: 'No commitments match these filters.'}
			</p>
		</section>
	{:else}
		<div class="cmt-groups" data-testid="lq-ai-mgmt-commitments-list">
			{#each groups as g (g.stakeholderId)}
				<section class="cmt-group" aria-label={`Commitments with ${g.fullName}`}>
					<div class="cmt-group__head">
						<a class="cmt-group__name" href={`/lq-ai/management/stakeholders/${g.stakeholderId}`}>
							{g.fullName}
						</a>
						<span class="cmt-group__type">{typeLabel(g.stakeholderType)}</span>
					</div>
					<ul class="cmt-rows">
						{#each g.rows as row (row.id)}
							<li class="cmt-row" class:cmt-row--closed={row.status !== 'open'}>
								<span class="cmt-row__desc">{row.description}</span>
								<span
									class="cmt-row__due"
									class:cmt-row__due--overdue={isOverdue(row.due_date, row.status)}
								>
									{#if row.due_date}
										{isOverdue(row.due_date, row.status) ? 'overdue — ' : 'due '}{formatDate(
											row.due_date
										)}
									{:else}
										no due date
									{/if}
								</span>
								<select
									class="cmt-status"
									value={row.status}
									aria-label={`Status of commitment: ${row.description}`}
									on:change={(e) => changeStatus(row, e)}
								>
									<option value="open">Open</option>
									<option value="done">Done</option>
									<option value="dropped">Dropped</option>
								</select>
							</li>
						{/each}
					</ul>
				</section>
			{/each}
		</div>
	{/if}
</main>

<style>
	.cmt-page {
		padding: var(--lq-space-6);
		max-width: 900px;
		margin: 0 auto;
	}

	.cmt-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.cmt-header {
		margin-bottom: var(--lq-space-4);
	}

	.cmt-sub {
		margin-top: var(--lq-space-2);
		color: var(--lq-text-secondary);
		max-width: 60ch;
	}

	.cmt-filters {
		display: flex;
		gap: var(--lq-space-4);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.cmt-chip-group {
		display: inline-flex;
		gap: var(--lq-space-2);
	}

	.cmt-chip {
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-pill);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-size: 13px;
		color: var(--lq-text-secondary);
		cursor: pointer;
		transition:
			border-color 0.15s ease,
			background 0.15s ease;
	}

	.cmt-chip:hover {
		border-color: var(--lq-accent);
	}

	.cmt-chip:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.cmt-chip--active {
		background: var(--lq-accent-soft);
		border-color: var(--lq-accent-border);
		color: var(--lq-accent);
		font-weight: 600;
	}

	.cmt-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.cmt-state-msg--error {
		color: var(--lq-error);
	}

	.cmt-inline-error {
		color: var(--lq-error);
		font-size: 13px;
		margin: 0 0 var(--lq-space-3);
	}

	.cmt-empty {
		text-align: center;
		padding: var(--lq-space-8) var(--lq-space-4);
	}

	.cmt-empty__copy {
		color: var(--lq-text-secondary);
	}

	.cmt-groups {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-4);
	}

	.cmt-group {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-4);
	}

	.cmt-group__head {
		display: flex;
		align-items: baseline;
		gap: var(--lq-space-3);
		margin-bottom: var(--lq-space-3);
	}

	.cmt-group__name {
		font-weight: 600;
		color: var(--lq-text-primary);
		text-decoration: none;
	}

	.cmt-group__name:hover {
		color: var(--lq-accent);
		text-decoration: underline;
	}

	.cmt-group__type {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--lq-text-tertiary);
	}

	.cmt-rows {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
	}

	.cmt-row {
		display: flex;
		align-items: center;
		gap: var(--lq-space-3);
		padding: var(--lq-space-2) var(--lq-space-3);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		background: var(--lq-inset);
	}

	.cmt-row--closed {
		opacity: 0.6;
	}

	.cmt-row__desc {
		flex: 1;
		font-size: 0.9rem;
		color: var(--lq-text-primary);
	}

	.cmt-row__due {
		font-size: 0.8rem;
		color: var(--lq-text-tertiary);
		white-space: nowrap;
	}

	.cmt-row__due--overdue {
		color: var(--lq-error);
		font-weight: 600;
	}

	.cmt-status {
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-sm);
		padding: 2px var(--lq-space-2);
		font-size: 12px;
		color: var(--lq-text-primary);
		cursor: pointer;
	}

	.cmt-status:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}
</style>
