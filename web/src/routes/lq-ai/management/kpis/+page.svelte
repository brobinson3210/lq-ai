<!--
  /lq-ai/management/kpis — the KPIs & OKRs dashboard.

  One GET /management/dashboard call renders two department sections of KPI
  stat tiles plus a per-member Team section. Each tile lazily fetches its
  full series (/series, batched with Promise.all) for an inline SVG
  sparkline; the tile itself is the link to the detail page. The interview
  wizard CTA opens /lq-ai/management/kpis/wizard (draft-then-confirm).
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { managementKpisApi } from '$lib/lq-ai/api';
	import type { KpiDashboard, KpiDatapoint, KpiRead } from '$lib/lq-ai/types';
	import {
		attainmentLabel,
		attainmentTone,
		deltaOf,
		formatKpiValue,
		periodLabel,
		unitSuffix
	} from '$lib/lq-ai/management/kpis';

	import NewKpiModal from '$lib/lq-ai/components/NewKpiModal.svelte';

	let dashboard: KpiDashboard | null = null;
	let loading = true;
	let error: string | null = null;
	let showNewModal = false;

	/** kpi id → datapoints once its /series call resolves; null = fetch failed. */
	let seriesMap: Record<string, KpiDatapoint[] | null> = {};

	function allKpis(d: KpiDashboard): KpiRead[] {
		const seen = new Set<string>();
		const out: KpiRead[] = [];
		for (const kpi of [
			...d.departments.legal,
			...d.departments.compliance,
			...d.team.flatMap((t) => t.kpis)
		]) {
			if (!seen.has(kpi.id)) {
				seen.add(kpi.id);
				out.push(kpi);
			}
		}
		return out;
	}

	async function loadSparklines(d: KpiDashboard) {
		const kpis = allKpis(d);
		await Promise.all(
			kpis.map(async (kpi) => {
				try {
					const series = await managementKpisApi.getSeries(kpi.id);
					seriesMap = { ...seriesMap, [kpi.id]: series.datapoints };
				} catch {
					seriesMap = { ...seriesMap, [kpi.id]: null };
				}
			})
		);
	}

	async function refresh() {
		loading = true;
		try {
			dashboard = await managementKpisApi.getDashboard();
			error = null;
			seriesMap = {};
			void loadSparklines(dashboard);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load the KPI dashboard';
		} finally {
			loading = false;
		}
	}

	onMount(refresh);

	$: departmentSections = dashboard
		? [
				{ key: 'legal', label: 'Legal', kpis: dashboard.departments.legal },
				{ key: 'compliance', label: 'Compliance', kpis: dashboard.departments.compliance }
			]
		: [];

	$: isEmpty =
		dashboard !== null &&
		dashboard.departments.legal.length === 0 &&
		dashboard.departments.compliance.length === 0 &&
		dashboard.team.every((t) => t.kpis.length === 0);

	// ----- Sparkline geometry (140×36, single accent series, no axes) -----

	const SPARK_W = 140;
	const SPARK_H = 36;
	const SPARK_PAD = 3;

	interface Spark {
		linePath: string;
		areaPath: string;
		targetY: number | null;
		lone: { x: number; y: number } | null;
		min: number;
		max: number;
		count: number;
	}

	function sparkOf(dps: KpiDatapoint[], target: string | null | undefined): Spark | null {
		const values = dps.map((d) => Number(d.value)).filter((n) => Number.isFinite(n));
		if (values.length === 0) return null;
		const targetN = target !== null && target !== undefined ? Number(target) : NaN;
		const domain = Number.isFinite(targetN) ? [...values, targetN] : values;
		let lo = Math.min(...domain);
		let hi = Math.max(...domain);
		if (lo === hi) {
			lo -= 1;
			hi += 1;
		}
		const y = (v: number) =>
			SPARK_H - SPARK_PAD - ((v - lo) / (hi - lo)) * (SPARK_H - 2 * SPARK_PAD);
		const x = (i: number) =>
			values.length === 1
				? SPARK_W / 2
				: SPARK_PAD + (i / (values.length - 1)) * (SPARK_W - 2 * SPARK_PAD);
		const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
		const linePath = `M${pts.join(' L')}`;
		const areaPath =
			values.length > 1
				? `${linePath} L${x(values.length - 1).toFixed(1)},${SPARK_H - SPARK_PAD} L${x(0).toFixed(1)},${SPARK_H - SPARK_PAD} Z`
				: '';
		return {
			linePath,
			areaPath,
			targetY: Number.isFinite(targetN) ? y(targetN) : null,
			lone: values.length === 1 ? { x: SPARK_W / 2, y: y(values[0]) } : null,
			min: Math.min(...values),
			max: Math.max(...values),
			count: values.length
		};
	}

	function sparkLabel(kpi: KpiRead, spark: Spark): string {
		const latest = formatKpiValue(kpi.latest_value, kpi.unit);
		const lo = formatKpiValue(String(spark.min), kpi.unit);
		const hi = formatKpiValue(String(spark.max), kpi.unit);
		return `${kpi.name} trend: latest ${latest}, range ${lo} to ${hi} across ${spark.count} ${
			spark.count === 1 ? 'period' : 'periods'
		}`;
	}
</script>

<main class="kpd-page" data-testid="lq-ai-mgmt-kpis-page">
	<a class="kpd-back" href="/lq-ai/management">← Management</a>

	<header class="kpd-header">
		<div>
			<h1 class="lq-text-page-h">KPIs &amp; OKRs</h1>
			<a
				class="kpd-roster-link"
				href="/lq-ai/management/kpis/team"
				data-testid="lq-ai-mgmt-kpis-team-link"
			>
				Team roster →
			</a>
		</div>
		<div class="kpd-header__actions">
			<a
				class="kpd-btn-wizard"
				href="/lq-ai/management/kpis/wizard"
				title="The interview wizard asks what a seasoned GC would ask, then drafts your KPI catalog for line-by-line confirmation."
				data-testid="lq-ai-mgmt-kpis-wizard-cta"
			>
				Interview wizard ✦
			</a>
			<button
				type="button"
				class="kpd-btn-primary"
				data-testid="lq-ai-mgmt-kpis-new-btn"
				on:click={() => (showNewModal = true)}
			>
				+ New KPI
			</button>
		</div>
	</header>

	{#if loading}
		<p class="lq-text-body kpd-state-msg">Loading the KPI dashboard…</p>
	{:else if error}
		<p class="lq-text-body kpd-state-msg kpd-state-msg--error" role="alert">
			Couldn't load the KPI dashboard: {error}
		</p>
	{:else if dashboard}
		{#if isEmpty && dashboard.team.length === 0}
			<section class="kpd-empty" data-testid="lq-ai-mgmt-kpis-empty">
				<p class="lq-text-body kpd-empty__copy">
					No KPIs yet. Three to five outcome numbers — not vanity metrics — are how legal proves its
					value. Start the catalog.
				</p>
				<button type="button" class="kpd-btn-primary" on:click={() => (showNewModal = true)}>
					+ New KPI
				</button>
			</section>
		{:else}
			{#each departmentSections as section (section.key)}
				<section
					class="kpd-section"
					aria-labelledby={`kpd-dept-${section.key}-h`}
					data-testid={`lq-ai-mgmt-kpis-dept-${section.key}`}
				>
					<h2 id={`kpd-dept-${section.key}-h`} class="kpd-section__title">{section.label}</h2>
					{#if section.kpis.length === 0}
						<p class="kpd-empty-line">No {section.label} department KPIs yet.</p>
					{:else}
						<div class="kpd-grid">
							{#each section.kpis as kpi (kpi.id)}
								{@const delta = deltaOf(
									kpi.latest_value,
									kpi.previous_value,
									kpi.direction,
									kpi.unit
								)}
								{@const attainment = attainmentLabel(kpi.attainment_pct)}
								{@const dps = seriesMap[kpi.id]}
								{@const spark = dps ? sparkOf(dps, kpi.target) : null}
								<a
									class="kpd-tile"
									href={`/lq-ai/management/kpis/${kpi.id}`}
									aria-label={`Open KPI: ${kpi.name}`}
									data-testid={`lq-ai-mgmt-kpis-tile-${kpi.id}`}
								>
									<h3 class="kpd-tile__name">{kpi.name}</h3>
									<p class="kpd-tile__hero">
										{formatKpiValue(kpi.latest_value, kpi.unit)}
										{#if unitSuffix(kpi.unit) && kpi.latest_value !== null}
											<span class="kpd-tile__unit">{unitSuffix(kpi.unit)}</span>
										{/if}
									</p>
									{#if delta}
										<p
											class="kpd-tile__delta"
											class:kpd-tile__delta--good={delta.improving}
											class:kpd-tile__delta--bad={!delta.improving}
										>
											<span aria-hidden="true">{delta.improving ? '▲' : '▼'}</span>
											{delta.text} vs prior · {periodLabel(kpi.latest_period)}
										</p>
									{:else if kpi.latest_period}
										<p class="kpd-tile__delta kpd-tile__delta--muted">
											{periodLabel(kpi.latest_period)} — no prior period
										</p>
									{:else}
										<p class="kpd-tile__delta kpd-tile__delta--muted">No datapoints yet</p>
									{/if}
									{#if attainment}
										<span class={`kpd-chip kpd-chip--${attainmentTone(kpi.attainment_pct)}`}>
											{attainment}
										</span>
									{/if}
									<div class="kpd-tile__spark">
										{#if dps === undefined}
											<div class="kpd-spark-skeleton" aria-hidden="true"></div>
										{:else if spark}
											<svg
												class="kpd-spark"
												viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
												width={SPARK_W}
												height={SPARK_H}
												role="img"
												aria-label={sparkLabel(kpi, spark)}
											>
												<title>{sparkLabel(kpi, spark)}</title>
												{#if spark.targetY !== null}
													<line
														x1={SPARK_PAD}
														x2={SPARK_W - SPARK_PAD}
														y1={spark.targetY}
														y2={spark.targetY}
														class="kpd-spark__target"
													/>
												{/if}
												{#if spark.areaPath}
													<path d={spark.areaPath} class="kpd-spark__area" />
												{/if}
												{#if spark.lone}
													<circle
														cx={spark.lone.x}
														cy={spark.lone.y}
														r="2.5"
														class="kpd-spark__dot"
													/>
												{:else}
													<path d={spark.linePath} class="kpd-spark__line" />
												{/if}
											</svg>
										{:else}
											<p class="kpd-spark-none">No trend yet</p>
										{/if}
									</div>
								</a>
							{/each}
						</div>
					{/if}
				</section>
			{/each}

			<section
				class="kpd-section"
				aria-labelledby="kpd-team-h"
				data-testid="lq-ai-mgmt-kpis-team-section"
			>
				<h2 id="kpd-team-h" class="kpd-section__title">Team</h2>
				{#if dashboard.team.length === 0}
					<p class="kpd-empty-line">
						No team members yet — add the roster on the
						<a href="/lq-ai/management/kpis/team">Team roster</a> page.
					</p>
				{:else}
					<div class="kpd-team-grid">
						{#each dashboard.team as entry (entry.member.id)}
							<article class="kpd-member" aria-label={`KPIs for ${entry.member.name}`}>
								<div class="kpd-member__head">
									<div>
										<h3 class="kpd-member__name">{entry.member.name}</h3>
										<p class="kpd-member__role">{entry.member.role_title}</p>
									</div>
									<span class="kpd-member__count">
										{entry.member.kpi_count}
										{entry.member.kpi_count === 1 ? 'KPI' : 'KPIs'}
									</span>
								</div>
								{#if entry.kpis.length === 0}
									<p class="kpd-empty-line">No individual KPIs yet.</p>
								{:else}
									<ul class="kpd-member__rows">
										{#each entry.kpis as kpi (kpi.id)}
											{@const delta = deltaOf(
												kpi.latest_value,
												kpi.previous_value,
												kpi.direction,
												kpi.unit
											)}
											<li>
												<a class="kpd-member-row" href={`/lq-ai/management/kpis/${kpi.id}`}>
													<span class="kpd-member-row__name">{kpi.name}</span>
													<span class="kpd-member-row__value">
														{formatKpiValue(kpi.latest_value, kpi.unit)}
														{#if unitSuffix(kpi.unit) && kpi.latest_value !== null}
															<span class="kpd-tile__unit">{unitSuffix(kpi.unit)}</span>
														{/if}
													</span>
													{#if delta}
														<span
															class="kpd-member-row__delta"
															class:kpd-tile__delta--good={delta.improving}
															class:kpd-tile__delta--bad={!delta.improving}
														>
															<span aria-hidden="true">{delta.improving ? '▲' : '▼'}</span>
															{delta.text}
														</span>
													{/if}
													{#if attainmentLabel(kpi.attainment_pct)}
														<span
															class={`kpd-chip kpd-chip--sm kpd-chip--${attainmentTone(kpi.attainment_pct)}`}
														>
															{attainmentLabel(kpi.attainment_pct)}
														</span>
													{/if}
												</a>
											</li>
										{/each}
									</ul>
								{/if}
							</article>
						{/each}
					</div>
				{/if}
			</section>
		{/if}
	{/if}
</main>

{#if showNewModal}
	<NewKpiModal onClose={() => (showNewModal = false)} onCreated={() => (showNewModal = false)} />
{/if}

<style>
	.kpd-page {
		padding: var(--lq-space-6);
		max-width: 1100px;
		margin: 0 auto;
	}

	.kpd-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.kpd-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.kpd-roster-link {
		display: inline-block;
		margin-top: var(--lq-space-2);
		color: var(--lq-accent);
		font-weight: 500;
		text-decoration: none;
	}

	.kpd-roster-link:hover {
		text-decoration: underline;
	}

	.kpd-header__actions {
		display: flex;
		gap: var(--lq-space-2);
		align-items: center;
		flex-wrap: wrap;
	}

	.kpd-btn-primary {
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

	.kpd-btn-primary:hover {
		filter: brightness(0.95);
	}

	.kpd-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kpd-btn-wizard {
		display: inline-block;
		background: transparent;
		color: var(--lq-accent);
		border: 1px solid var(--lq-accent-border, var(--lq-accent));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		line-height: 1.5;
		text-decoration: none;
		cursor: pointer;
	}

	.kpd-btn-wizard:hover {
		background: var(--lq-accent-soft);
	}

	.kpd-btn-wizard:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kpd-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.kpd-state-msg--error {
		color: var(--lq-error);
	}

	.kpd-empty {
		text-align: center;
		padding: var(--lq-space-8) var(--lq-space-4);
	}

	.kpd-empty__copy {
		color: var(--lq-text-secondary);
		margin-bottom: var(--lq-space-4);
	}

	.kpd-empty-line {
		color: var(--lq-text-tertiary);
		font-size: 0.9rem;
		margin: 0 0 var(--lq-space-2);
	}

	.kpd-empty-line a {
		color: var(--lq-accent);
	}

	.kpd-section {
		margin-bottom: var(--lq-space-6);
	}

	.kpd-section__title {
		font-size: 1rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0 0 var(--lq-space-3);
	}

	.kpd-grid {
		display: grid;
		gap: var(--lq-space-4);
		grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
	}

	.kpd-tile {
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

	.kpd-tile:hover {
		border-color: var(--lq-accent-border);
		box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
	}

	.kpd-tile:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kpd-tile__name {
		font-weight: 600;
		font-size: 0.9rem;
		color: var(--lq-text-primary);
		margin: 0;
	}

	.kpd-tile__hero {
		font-size: 1.75rem;
		font-weight: 700;
		color: var(--lq-text-primary);
		margin: 0;
		line-height: 1.1;
	}

	.kpd-tile__unit {
		font-size: 0.85rem;
		font-weight: 500;
		color: var(--lq-text-tertiary);
	}

	.kpd-tile__delta {
		font-size: 0.8rem;
		color: var(--lq-text-secondary);
		margin: 0;
	}

	.kpd-tile__delta--good {
		color: var(--lq-accent);
		font-weight: 600;
	}

	.kpd-tile__delta--bad {
		color: var(--lq-error);
		font-weight: 600;
	}

	.kpd-tile__delta--muted {
		color: var(--lq-text-tertiary);
	}

	.kpd-chip {
		align-self: flex-start;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: var(--lq-radius-pill);
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
		border: 1px solid transparent;
	}

	.kpd-chip--sm {
		font-size: 0.65rem;
		padding: 0.1rem 0.45rem;
	}

	.kpd-chip--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}

	.kpd-chip--info {
		background: var(--lq-tier-soft);
		color: var(--lq-tier);
		border-color: var(--lq-tier-border);
	}

	.kpd-chip--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}

	.kpd-chip--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}

	.kpd-chip--muted {
		background: var(--lq-inset);
		color: var(--lq-text-tertiary);
		border-color: var(--lq-border);
	}

	.kpd-tile__spark {
		margin-top: auto;
		padding-top: var(--lq-space-2);
	}

	.kpd-spark {
		display: block;
	}

	.kpd-spark__line {
		fill: none;
		stroke: var(--lq-accent);
		stroke-width: 2;
		stroke-linejoin: round;
		stroke-linecap: round;
	}

	.kpd-spark__area {
		fill: var(--lq-accent);
		opacity: 0.08;
		stroke: none;
	}

	.kpd-spark__dot {
		fill: var(--lq-accent);
	}

	.kpd-spark__target {
		stroke: var(--lq-text-tertiary);
		stroke-width: 1;
		stroke-dasharray: 2 3;
	}

	.kpd-spark-skeleton {
		width: 140px;
		height: 36px;
		border-radius: var(--lq-radius);
		background: var(--lq-inset);
		animation: kpd-pulse 1.2s ease-in-out infinite;
	}

	@keyframes kpd-pulse {
		0%,
		100% {
			opacity: 0.5;
		}
		50% {
			opacity: 1;
		}
	}

	.kpd-spark-none {
		font-size: 0.75rem;
		color: var(--lq-text-tertiary);
		margin: 0;
	}

	.kpd-team-grid {
		display: grid;
		gap: var(--lq-space-4);
		grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
	}

	.kpd-member {
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-4);
	}

	.kpd-member__head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-2);
		margin-bottom: var(--lq-space-3);
	}

	.kpd-member__name {
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0;
	}

	.kpd-member__role {
		color: var(--lq-text-secondary);
		font-size: 0.85rem;
		margin: var(--lq-space-1) 0 0;
	}

	.kpd-member__count {
		font-size: 0.75rem;
		color: var(--lq-text-tertiary);
		white-space: nowrap;
	}

	.kpd-member__rows {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.kpd-member-row {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		padding: var(--lq-space-1) var(--lq-space-2);
		border-radius: var(--lq-radius);
		text-decoration: none;
		color: inherit;
		font-size: 0.85rem;
	}

	.kpd-member-row:hover {
		background: var(--lq-inset);
	}

	.kpd-member-row:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kpd-member-row__name {
		flex: 1;
		color: var(--lq-text-primary);
		min-width: 0;
	}

	.kpd-member-row__value {
		font-weight: 600;
		color: var(--lq-text-primary);
		white-space: nowrap;
	}

	.kpd-member-row__delta {
		font-size: 0.75rem;
		white-space: nowrap;
	}
</style>
