<!--
  /lq-ai/management/stakeholders — the Stakeholders registry.

  Space filter chips are built from RELATIONSHIP_SPACES (module registry);
  the active space is kept in the ?space= query param so the Management
  landing's Relationship cards deep-link straight into a filtered view.
  "Needs attention" is the server-side cadence/health filter.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { stakeholdersApi } from '$lib/lq-ai/api';
	import type { Stakeholder } from '$lib/lq-ai/types';
	import { RELATIONSHIP_SPACES } from '$lib/lq-ai/management/modules';
	import {
		cadenceExceeded,
		healthLabel,
		healthTone,
		lastTouchLabel,
		typeLabel
	} from '$lib/lq-ai/management/stakeholders';
	import NewStakeholderModal from '$lib/lq-ai/components/NewStakeholderModal.svelte';

	let stakeholders: Stakeholder[] = [];
	let loading = true;
	let error: string | null = null;
	let showNewModal = false;
	let mounted = false;

	// URL is the source of truth for the filters.
	$: space = $page.url.searchParams.get('space') ?? '';
	$: needsAttention = $page.url.searchParams.get('needs_attention') === 'true';
	$: activeSpace = RELATIONSHIP_SPACES.find((s) => s.id === space);

	async function refresh(spaceId: string, attention: boolean) {
		loading = true;
		try {
			stakeholders = await stakeholdersApi.listStakeholders({
				space: spaceId || undefined,
				needsAttention: attention
			});
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load stakeholders';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		mounted = true;
	});

	// Re-fetch whenever the URL filters change (chip clicks, back/forward,
	// deep links from the Management landing).
	$: if (mounted) void refresh(space, needsAttention);

	function filterUrl(spaceId: string, attention: boolean): string {
		const params = new URLSearchParams();
		if (spaceId) params.set('space', spaceId);
		if (attention) params.set('needs_attention', 'true');
		const qs = params.toString();
		return `/lq-ai/management/stakeholders${qs ? `?${qs}` : ''}`;
	}

	function setSpace(spaceId: string) {
		goto(filterUrl(spaceId, needsAttention), {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
	}

	function toggleNeedsAttention() {
		goto(filterUrl(space, !needsAttention), {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
	}

	function dossierUrl(id: string): string {
		return `/lq-ai/management/stakeholders/${id}${space ? `?space=${space}` : ''}`;
	}
</script>

<main class="shp-page" data-testid="lq-ai-mgmt-stakeholders-page">
	<a class="shp-back" href="/lq-ai/management">← Management</a>

	<header class="shp-header">
		<div>
			<h1 class="lq-text-page-h">Stakeholders</h1>
			<a
				class="shp-rollup-link"
				href="/lq-ai/management/commitments"
				data-testid="lq-ai-mgmt-stakeholders-rollup-link"
			>
				Commitments — what do I owe →
			</a>
		</div>
		<button
			type="button"
			class="shp-btn-primary"
			data-testid="lq-ai-mgmt-stakeholders-new-btn"
			on:click={() => (showNewModal = true)}
		>
			+ New stakeholder
		</button>
	</header>

	<div class="shp-filters" role="group" aria-label="Filter stakeholders by relationship space">
		<button
			type="button"
			class="shp-chip"
			class:shp-chip--active={space === ''}
			aria-pressed={space === ''}
			data-testid="lq-ai-mgmt-stakeholders-space-all"
			on:click={() => setSpace('')}
		>
			All
		</button>
		{#each RELATIONSHIP_SPACES as s (s.id)}
			<button
				type="button"
				class="shp-chip"
				class:shp-chip--active={space === s.id}
				aria-pressed={space === s.id}
				data-testid={`lq-ai-mgmt-stakeholders-space-${s.id}`}
				on:click={() => setSpace(s.id)}
			>
				{s.label}
			</button>
		{/each}
		<button
			type="button"
			class="shp-chip shp-chip--attention"
			class:shp-chip--active={needsAttention}
			aria-pressed={needsAttention}
			aria-label="Show only stakeholders needing attention"
			data-testid="lq-ai-mgmt-stakeholders-needs-attention"
			on:click={toggleNeedsAttention}
		>
			⚠ Needs attention
		</button>
	</div>

	{#if loading}
		<p class="lq-text-body shp-state-msg">Loading stakeholders…</p>
	{:else if error}
		<p class="lq-text-body shp-state-msg shp-state-msg--error" role="alert">
			Couldn't load stakeholders: {error}
		</p>
	{:else if stakeholders.length === 0}
		<section class="shp-empty" data-testid="lq-ai-mgmt-stakeholders-empty">
			<p class="lq-text-body shp-empty__copy">
				{#if needsAttention}
					Nothing needs attention here — every relationship is inside its cadence.
				{:else if activeSpace}
					No stakeholders in {activeSpace.label} yet. Trust is built person by person — add the first
					one.
				{:else}
					No stakeholders yet. Trust is built person by person — start your registry.
				{/if}
			</p>
			{#if !needsAttention}
				<button type="button" class="shp-btn-primary" on:click={() => (showNewModal = true)}>
					+ New stakeholder
				</button>
			{/if}
		</section>
	{:else}
		<div class="shp-grid" data-testid="lq-ai-mgmt-stakeholders-list">
			{#each stakeholders as s (s.id)}
				<a
					class="shp-card"
					href={dossierUrl(s.id)}
					aria-label={`Open dossier: ${s.full_name}`}
					data-testid={`lq-ai-mgmt-stakeholders-card-${s.id}`}
				>
					<div class="shp-card__top">
						<h3 class="shp-card__name">{s.full_name}</h3>
						<span class={`shp-health shp-health--${healthTone(s.overall_health)}`}>
							{healthLabel(s.overall_health)}
						</span>
					</div>

					{#if s.role_title || s.organization}
						<p class="shp-card__role">
							{#if s.role_title}{s.role_title}{/if}
							{#if s.role_title && s.organization}&nbsp;@&nbsp;{/if}
							{#if s.organization}{s.organization}{/if}
						</p>
					{/if}

					<p class="shp-card__type">
						{typeLabel(s.stakeholder_type)}
						{#if s.committee_seats}
							<span class="shp-card__seats">· {s.committee_seats}</span>
						{/if}
					</p>

					<div class="shp-card__footer">
						<span
							class="shp-card__touch"
							class:shp-card__touch--late={cadenceExceeded(
								s.days_since_last_interaction,
								s.cadence_target_days
							)}
						>
							{lastTouchLabel(s.days_since_last_interaction)}
						</span>
						<span class="shp-card__commitments">
							{s.open_commitments_count} open
							{s.open_commitments_count === 1 ? 'commitment' : 'commitments'}
						</span>
					</div>
				</a>
			{/each}
		</div>
	{/if}
</main>

{#if showNewModal}
	<NewStakeholderModal
		onClose={() => (showNewModal = false)}
		onCreated={() => (showNewModal = false)}
	/>
{/if}

<style>
	.shp-page {
		padding: var(--lq-space-6);
		max-width: 1100px;
		margin: 0 auto;
	}

	.shp-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.shp-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-4);
	}

	.shp-rollup-link {
		display: inline-block;
		margin-top: var(--lq-space-2);
		color: var(--lq-accent);
		font-weight: 500;
		text-decoration: none;
	}

	.shp-rollup-link:hover {
		text-decoration: underline;
	}

	.shp-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		cursor: pointer;
		font-weight: 500;
		font-size: 14px;
		line-height: 1.5;
	}

	.shp-btn-primary:hover {
		filter: brightness(0.95);
	}

	.shp-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.shp-filters {
		display: flex;
		flex-wrap: wrap;
		gap: var(--lq-space-2);
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.shp-chip {
		display: inline-flex;
		align-items: center;
		gap: var(--lq-space-1);
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

	.shp-chip:hover {
		border-color: var(--lq-accent);
	}

	.shp-chip:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.shp-chip--active {
		background: var(--lq-accent-soft);
		border-color: var(--lq-accent-border);
		color: var(--lq-accent);
		font-weight: 600;
	}

	.shp-chip--attention.shp-chip--active {
		background: var(--lq-warn-soft);
		border-color: var(--lq-warn-border);
		color: var(--lq-warn);
	}

	.shp-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.shp-state-msg--error {
		color: var(--lq-error);
	}

	.shp-empty {
		text-align: center;
		padding: var(--lq-space-8) var(--lq-space-4);
	}

	.shp-empty__copy {
		color: var(--lq-text-secondary);
		margin-bottom: var(--lq-space-4);
	}

	.shp-grid {
		display: grid;
		gap: var(--lq-space-4);
		grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
	}

	.shp-card {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-4);
		text-decoration: none;
		color: inherit;
		transition:
			border-color 0.15s ease,
			box-shadow 0.15s ease;
	}

	.shp-card:hover {
		border-color: var(--lq-accent-border);
		box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
	}

	.shp-card:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.shp-card__top {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--lq-space-2);
	}

	.shp-card__name {
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0;
	}

	.shp-card__role {
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
		margin: 0;
	}

	.shp-card__type {
		color: var(--lq-text-tertiary);
		font-size: 0.8rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		margin: 0;
	}

	.shp-card__seats {
		text-transform: none;
		letter-spacing: normal;
	}

	.shp-card__footer {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--lq-space-2);
		border-top: 1px solid var(--lq-border);
		padding-top: var(--lq-space-2);
		margin-top: var(--lq-space-1);
		font-size: 0.8rem;
	}

	.shp-card__touch {
		color: var(--lq-text-secondary);
	}

	.shp-card__touch--late {
		color: var(--lq-error);
		font-weight: 600;
	}

	.shp-card__commitments {
		color: var(--lq-text-tertiary);
		white-space: nowrap;
	}

	/* Health chip tones */
	.shp-health {
		flex-shrink: 0;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: var(--lq-radius-pill);
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
		border: 1px solid transparent;
	}

	.shp-health--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}

	.shp-health--info {
		background: var(--lq-tier-soft);
		color: var(--lq-tier);
		border-color: var(--lq-tier-border);
	}

	.shp-health--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}

	.shp-health--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}

	.shp-health--muted {
		background: var(--lq-inset);
		color: var(--lq-text-tertiary);
		border-color: var(--lq-border);
	}
</style>
