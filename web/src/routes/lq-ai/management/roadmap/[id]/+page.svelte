<!--
  /lq-ai/management/roadmap/[id] — value-prop page for a roadmap module.

  Roadmap tiles on the Management landing open here: what the module will
  manage, the relationship it serves (the trust frame), and how AI earns
  its keep — honest about state per the project's no-overclaiming posture.
-->
<script lang="ts">
	import { page } from '$app/stores';
	import { moduleById } from '$lib/lq-ai/management/modules';

	$: mod = moduleById($page.params.id ?? '');
</script>

<main class="lq-mgmt-roadmap-page" data-testid="lq-ai-mgmt-roadmap-page">
	<a class="lq-mgmt-back" href="/lq-ai/management">← Management</a>

	{#if !mod}
		<h1 class="lq-text-page-h">Module not found</h1>
		<p class="lq-text-body">No module with id “{$page.params.id}”.</p>
	{:else}
		<header class="lq-mgmt-roadmap-head">
			<span class="lq-mgmt-roadmap-icon" aria-hidden="true">{mod.icon}</span>
			<div>
				<h1 class="lq-text-page-h">{mod.label}</h1>
				<span class="lq-mgmt-tag lq-mgmt-tag-road">Roadmap</span>
			</div>
		</header>

		<p class="lq-mgmt-roadmap-tagline">{mod.tagline}</p>

		<section class="lq-mgmt-roadmap-section">
			<h2>Why it earns trust</h2>
			<p>{mod.trustFrame}</p>
		</section>

		<section class="lq-mgmt-roadmap-section">
			<h2>What it will manage</h2>
			<p>{mod.whatItManages}</p>
		</section>

		<section class="lq-mgmt-roadmap-section">
			<h2>Where AI earns its keep</h2>
			<p>{mod.aiAngle}</p>
		</section>

		<p class="lq-mgmt-roadmap-note">
			This module is planned, not yet built. The Management tab ships in stages — Stakeholder
			Management and KPIs are live today; this page describes what comes next.
		</p>
	{/if}
</main>

<style>
	.lq-mgmt-roadmap-page {
		padding: var(--lq-space-6);
		max-width: 760px;
		margin: 0 auto;
	}

	.lq-mgmt-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.lq-mgmt-roadmap-head {
		display: flex;
		align-items: center;
		gap: var(--lq-space-4);
		margin-bottom: var(--lq-space-3);
	}

	.lq-mgmt-roadmap-icon {
		font-size: 2.5rem;
	}

	.lq-mgmt-roadmap-tagline {
		color: var(--lq-text-secondary);
		font-size: 1.05rem;
		line-height: 1.6;
		margin-bottom: var(--lq-space-6);
	}

	.lq-mgmt-roadmap-section {
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.lq-mgmt-roadmap-section h2 {
		font-size: 1rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin-bottom: var(--lq-space-2);
	}

	.lq-mgmt-roadmap-section p {
		color: var(--lq-text-secondary);
		line-height: 1.65;
	}

	.lq-mgmt-roadmap-note {
		margin-top: var(--lq-space-6);
		padding: var(--lq-space-4);
		border: 1px dashed var(--lq-border);
		border-radius: var(--lq-radius-lg);
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
		line-height: 1.6;
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

	.lq-mgmt-tag-road {
		background: color-mix(in srgb, #8b5cf6 15%, transparent);
		color: #6d28d9;
	}

	:global(.dark) .lq-mgmt-tag-road {
		color: #a78bfa;
	}
</style>
