<!--
  /lq-ai/management — the Management tab landing: the GC's department cockpit.

  Two zones per the tab design:
   - Relationships: the constituencies where trust is built day by day
     (each space opens a filtered view of the Stakeholders module).
   - Operations: the pillar modules — instruments through which the
     department proves it can be trusted.

  Tiles carry an IN PRODUCTION or ROADMAP tag; roadmap tiles open a
  value-prop page (/lq-ai/management/roadmap/[id]).
-->
<script lang="ts">
	import {
		MANAGEMENT_MODULES,
		DASHBOARD_MODULE,
		RELATIONSHIP_SPACES
	} from '$lib/lq-ai/management/modules';
	import { managementUrgentApi } from '$lib/lq-ai/api';
	import type { UrgentItem, UrgentMattersResponse } from '$lib/lq-ai/types';

	const operationsModules = [
		...MANAGEMENT_MODULES.filter((m) => m.zone === 'operations'),
		DASHBOARD_MODULE
	];
	const stakeholdersModule = MANAGEMENT_MODULES.find((m) => m.id === 'stakeholders')!;

	let showUrgent = false;
	let urgentLoading = false;
	let urgentError: string | null = null;
	let urgent: UrgentMattersResponse | null = null;

	async function toggleUrgent() {
		showUrgent = !showUrgent;
		if (!showUrgent || urgent) return;
		urgentLoading = true;
		urgentError = null;
		try {
			urgent = await managementUrgentApi.getUrgentMatters();
		} catch (e) {
			urgentError = e instanceof Error ? e.message : 'Failed to load urgent matters';
		} finally {
			urgentLoading = false;
		}
	}

	function dueLabel(item: UrgentItem): string | null {
		if (item.days_until_due === null) return null;
		if (item.days_until_due < 0)
			return `${-item.days_until_due} day${item.days_until_due === -1 ? '' : 's'} overdue`;
		if (item.days_until_due === 0) return 'due today';
		return `due in ${item.days_until_due} day${item.days_until_due === 1 ? '' : 's'}`;
	}
</script>

<main class="lq-mgmt-page" data-testid="lq-ai-management-page">
	<header class="lq-page-header">
		<h1 class="lq-text-page-h">Management</h1>
		<p class="lq-mgmt-thesis lq-text-body">
			To be successful, a GC must have the trust of the enterprise and each of its stakeholders. To
			earn that trust, good relationships, being responsive and constant reprioritization of matters
			are key. This Management tab is an operational tracking system by which GCs can track and
			improve their stakeholder relationships, responsiveness, priorities, and their own and their
			team&rsquo;s performance.
		</p>

		<div class="lq-mgmt-urgent">
			<button
				type="button"
				class="lq-mgmt-urgent-btn"
				aria-expanded={showUrgent}
				data-testid="lq-ai-mgmt-urgent-cta"
				on:click={toggleUrgent}
			>
				Urgent Matters
			</button>
			{#if showUrgent}
				<div class="lq-mgmt-urgent-panel" data-testid="lq-ai-mgmt-urgent-panel">
					{#if urgentLoading}
						<p class="lq-mgmt-urgent-note">Scanning commitments, cadences, and KPIs…</p>
					{:else if urgentError}
						<p class="lq-mgmt-urgent-note" role="alert">{urgentError}</p>
					{:else if urgent}
						{#if urgent.red.length === 0 && urgent.yellow.length === 0}
							<p class="lq-mgmt-urgent-note">
								Nothing needs you right now — no overdue commitments, cadence breaches, or
								off-target KPIs in the window.
							</p>
						{:else}
							{#if urgent.red.length > 0}
								<h3 class="lq-mgmt-urgent-h lq-mgmt-urgent-h--red">Red — needs you now</h3>
								<ul class="lq-mgmt-urgent-list">
									{#each urgent.red as item}
										<li class="lq-mgmt-urgent-item lq-mgmt-urgent-item--red">
											<a href={item.link} class="lq-mgmt-urgent-title">{item.title}</a>
											<p class="lq-mgmt-urgent-why">
												{item.why}{#if dueLabel(item)}
													&nbsp;·&nbsp;{dueLabel(item)}{/if}
											</p>
											<p class="lq-mgmt-urgent-next">→ {item.next_step}</p>
										</li>
									{/each}
								</ul>
							{/if}
							{#if urgent.yellow.length > 0}
								<h3 class="lq-mgmt-urgent-h lq-mgmt-urgent-h--yellow">
									Yellow — needs your attention in the next 5 days
								</h3>
								<ul class="lq-mgmt-urgent-list">
									{#each urgent.yellow as item}
										<li class="lq-mgmt-urgent-item lq-mgmt-urgent-item--yellow">
											<a href={item.link} class="lq-mgmt-urgent-title">{item.title}</a>
											<p class="lq-mgmt-urgent-why">
												{item.why}{#if dueLabel(item)}
													&nbsp;·&nbsp;{dueLabel(item)}{/if}
											</p>
											<p class="lq-mgmt-urgent-next">→ {item.next_step}</p>
										</li>
									{/each}
								</ul>
							{/if}
						{/if}
					{/if}
				</div>
			{/if}
		</div>
	</header>

	<section class="lq-mgmt-zone" aria-labelledby="zone-relationships">
		<div class="lq-mgmt-zone-head">
			<h2 id="zone-relationships" class="lq-mgmt-zone-title">Relationships</h2>
			<p class="lq-mgmt-zone-sub">
				Each module tracks what&rsquo;s needed, for whom and when.
				<span class="lq-mgmt-tag lq-mgmt-tag-prod">In production</span>
			</p>
		</div>
		<div class="lq-mgmt-grid">
			{#each RELATIONSHIP_SPACES as space (space.id)}
				<a
					class="lq-mgmt-card"
					href={`${stakeholdersModule.route}?space=${space.id}`}
					data-testid={`lq-ai-mgmt-space-${space.id}`}
				>
					<h3 class="lq-mgmt-card-title">{space.label}</h3>
					<p class="lq-mgmt-card-desc">{space.descriptor}</p>
					<span class="lq-mgmt-card-cta">Open space →</span>
				</a>
			{/each}
		</div>
	</section>

	<section class="lq-mgmt-zone" aria-labelledby="zone-operations">
		<div class="lq-mgmt-zone-head">
			<h2 id="zone-operations" class="lq-mgmt-zone-title">Operations</h2>
			<p class="lq-mgmt-zone-sub">
				The modules by which Legal and the GC measure and report their value and ROI.
			</p>
		</div>
		<div class="lq-mgmt-grid">
			{#each operationsModules as mod (mod.id)}
				<a
					class="lq-mgmt-card"
					class:lq-mgmt-card-roadmap={mod.status === 'roadmap'}
					href={mod.route}
					data-testid={`lq-ai-mgmt-module-${mod.id}`}
				>
					<span class="lq-mgmt-card-topline">
						<span class="lq-mgmt-card-icon" aria-hidden="true">{mod.icon}</span>
						{#if mod.status === 'production'}
							<span class="lq-mgmt-tag lq-mgmt-tag-prod">In production</span>
						{:else}
							<span class="lq-mgmt-tag lq-mgmt-tag-road">Roadmap</span>
						{/if}
					</span>
					<h3 class="lq-mgmt-card-title">{mod.label}</h3>
					<p class="lq-mgmt-card-desc">{mod.tagline}</p>
					<span class="lq-mgmt-card-cta">
						{mod.status === 'production' ? 'Open module →' : 'See what’s coming →'}
					</span>
				</a>
			{/each}
		</div>
	</section>
</main>

<style>
	.lq-mgmt-page {
		padding: var(--lq-space-6);
		max-width: 1100px;
		margin: 0 auto;
	}

	.lq-page-header {
		margin-bottom: var(--lq-space-6);
	}

	.lq-mgmt-thesis {
		margin-top: var(--lq-space-3);
		color: var(--lq-text-secondary);
		max-width: 72ch;
		line-height: 1.6;
	}

	/* The triage button is the page's centre of gravity — centred, and
	   pulsing so it reads as "look here first" without a badge count. */
	.lq-mgmt-urgent {
		margin-top: var(--lq-space-4);
		text-align: center;
	}

	.lq-mgmt-urgent-btn {
		background: var(--lq-error, #b3261e);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-5, 1.25rem);
		font-weight: 600;
		font-size: 1rem;
		cursor: pointer;
	}

	.lq-mgmt-urgent-btn:hover {
		filter: brightness(0.92);
	}

	.lq-mgmt-urgent-btn:focus-visible {
		outline: 2px solid var(--lq-error, #b3261e);
		outline-offset: 2px;
	}

	@keyframes lq-mgmt-urgent-pulse {
		0%,
		100% {
			box-shadow: 0 0 0 0 color-mix(in srgb, var(--lq-error, #b3261e) 60%, transparent);
			filter: brightness(1);
		}
		50% {
			box-shadow: 0 0 0 8px color-mix(in srgb, var(--lq-error, #b3261e) 0%, transparent);
			filter: brightness(1.18);
		}
	}

	/* Motion is opt-in: users who ask for reduced motion get a solid button. */
	@media (prefers-reduced-motion: no-preference) {
		.lq-mgmt-urgent-btn {
			animation: lq-mgmt-urgent-pulse 1.4s ease-in-out infinite;
		}

		.lq-mgmt-urgent-btn:hover,
		.lq-mgmt-urgent-btn[aria-expanded='true'] {
			animation: none;
		}
	}

	.lq-mgmt-urgent-panel {
		margin: var(--lq-space-3) auto 0;
		padding: var(--lq-space-4);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		max-width: 78ch;
		line-height: 1.6;
		text-align: left;
	}

	.lq-mgmt-urgent-note {
		color: var(--lq-text-secondary);
		margin: 0;
	}

	.lq-mgmt-urgent-h {
		font-size: 0.8rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		margin: 0 0 var(--lq-space-2);
	}

	.lq-mgmt-urgent-h--red {
		color: var(--lq-error, #b3261e);
	}

	.lq-mgmt-urgent-h--yellow {
		color: var(--lq-warn, #9a6b00);
		margin-top: var(--lq-space-4);
	}

	.lq-mgmt-urgent-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-3);
	}

	.lq-mgmt-urgent-item {
		border-left: 3px solid var(--lq-border);
		padding-left: var(--lq-space-3);
	}

	.lq-mgmt-urgent-item--red {
		border-left-color: var(--lq-error, #b3261e);
	}

	.lq-mgmt-urgent-item--yellow {
		border-left-color: var(--lq-warn, #9a6b00);
	}

	.lq-mgmt-urgent-title {
		font-weight: 600;
		color: var(--lq-text-primary);
		text-decoration: none;
	}

	.lq-mgmt-urgent-title:hover {
		color: var(--lq-accent);
		text-decoration: underline;
	}

	.lq-mgmt-urgent-why {
		margin: 2px 0 0;
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
	}

	.lq-mgmt-urgent-next {
		margin: 2px 0 0;
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
		font-style: italic;
	}

	.lq-mgmt-zone {
		margin-bottom: var(--lq-space-8, 3rem);
	}

	.lq-mgmt-zone-head {
		margin-bottom: var(--lq-space-4);
	}

	.lq-mgmt-zone-title {
		font-size: 1.25rem;
		font-weight: 600;
		color: var(--lq-text-primary);
	}

	.lq-mgmt-zone-sub {
		margin-top: var(--lq-space-2);
		color: var(--lq-text-secondary);
		max-width: 72ch;
	}

	.lq-mgmt-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
		gap: var(--lq-space-4);
	}

	.lq-mgmt-card {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
		padding: var(--lq-space-5, 1.25rem);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		text-decoration: none;
		transition: border-color 0.15s ease;
	}

	.lq-mgmt-card:hover {
		border-color: var(--lq-accent);
	}

	.lq-mgmt-card-roadmap {
		opacity: 0.85;
	}

	.lq-mgmt-card-topline {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.lq-mgmt-card-icon {
		font-size: 1.5rem;
	}

	.lq-mgmt-card-title {
		font-weight: 600;
		color: var(--lq-text-primary);
	}

	.lq-mgmt-card-desc {
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
		line-height: 1.5;
		flex: 1;
	}

	.lq-mgmt-card-cta {
		color: var(--lq-accent);
		font-size: 0.85rem;
		font-weight: 500;
	}

	.lq-mgmt-tag {
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: 999px;
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
	}

	.lq-mgmt-tag-prod {
		background: color-mix(in srgb, #22c55e 18%, transparent);
		color: #15803d;
	}

	:global(.dark) .lq-mgmt-tag-prod {
		color: #4ade80;
	}

	.lq-mgmt-tag-road {
		background: color-mix(in srgb, #8b5cf6 15%, transparent);
		color: #6d28d9;
	}

	:global(.dark) .lq-mgmt-tag-road {
		color: #a78bfa;
	}
</style>
