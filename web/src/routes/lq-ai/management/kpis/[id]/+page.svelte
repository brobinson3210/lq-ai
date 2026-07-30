<!--
  /lq-ai/management/kpis/[id] — the KPI detail page.

  One GET /series call renders the meta header, the inline-SVG trend chart
  (single series, accent stroke, target/baseline reference lines, hover
  crosshair + tooltip), the record-datapoint form (409 → inline overwrite
  confirm), and the datapoint table — which doubles as the chart's
  accessible data view.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { afterNavigate, goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { managementBackHref } from '$lib/lq-ai/management/backNav';
	import { managementKpisApi, LQAIApiError } from '$lib/lq-ai/api';
	import type {
		KpiCadence,
		KpiDatapoint,
		KpiDepartment,
		KpiDirection,
		KpiRead,
		TeamMemberRead
	} from '$lib/lq-ai/types';
	import {
		CADENCE_LABELS,
		CADENCE_OPTIONS,
		DEPARTMENT_OPTIONS,
		DIRECTION_LABELS,
		DIRECTION_OPTIONS,
		SCOPE_LABELS,
		attainmentLabel,
		attainmentTone,
		departmentLabel,
		deltaOf,
		formatKpiValue,
		nextPeriod,
		periodLabel,
		unitSuffix
	} from '$lib/lq-ai/management/kpis';
	import { formatDateTime } from '$lib/lq-ai/management/stakeholders';

	$: kpiId = $page.params.id;

	let kpi: KpiRead | null = null;
	let datapoints: KpiDatapoint[] = [];
	let memberName: string | null = null;
	let loading = true;
	let error: string | null = null;

	async function loadSeries() {
		if (!kpiId) return;
		const series = await managementKpisApi.getSeries(kpiId);
		kpi = series.kpi;
		datapoints = series.datapoints;
		dpPeriod = nextPeriod(kpi.latest_period, kpi.cadence);
		if (kpi.team_member_id) {
			try {
				memberName = (await managementKpisApi.getTeamMember(kpi.team_member_id)).name;
			} catch {
				memberName = null;
			}
		} else {
			memberName = null;
		}
	}

	let backHref = '/lq-ai/management/kpis';
	afterNavigate((nav) => {
		backHref = managementBackHref(nav, '/lq-ai/management/kpis');
	});

	onMount(async () => {
		loading = true;
		try {
			await loadSeries();
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load the KPI';
		} finally {
			loading = false;
		}
	});

	// ----- Meta edit mode -----

	let editing = false;
	let saving = false;
	let editError: string | null = null;
	let draftName = '';
	let draftDepartment: KpiDepartment = 'legal';
	let draftUnit = '';
	let draftCadence: KpiCadence = 'monthly';
	let draftDirection: KpiDirection = 'higher_is_better';
	let draftBaseline = '';
	let draftTarget = '';
	let draftRationale = '';

	function startEdit() {
		if (!kpi) return;
		draftName = kpi.name;
		draftDepartment = kpi.department;
		draftUnit = kpi.unit;
		draftCadence = kpi.cadence;
		draftDirection = kpi.direction;
		draftBaseline = kpi.baseline ?? '';
		draftTarget = kpi.target ?? '';
		draftRationale = kpi.rationale_md ?? '';
		editError = null;
		editing = true;
	}

	async function saveEdit() {
		if (!kpi) return;
		if (!draftName.trim()) {
			editError = 'A KPI name is required.';
			return;
		}
		if (!draftUnit.trim()) {
			editError = 'A unit is required.';
			return;
		}
		for (const [label, raw] of [
			['Baseline', draftBaseline],
			['Target', draftTarget]
		] as const) {
			if (raw.trim() && !Number.isFinite(Number(raw.trim()))) {
				editError = `${label} must be a number.`;
				return;
			}
		}
		saving = true;
		editError = null;
		try {
			kpi = await managementKpisApi.patchKpi(kpi.id, {
				name: draftName.trim(),
				department: draftDepartment,
				unit: draftUnit.trim(),
				cadence: draftCadence,
				direction: draftDirection,
				baseline: draftBaseline.trim() || null,
				target: draftTarget.trim() || null,
				rationale_md: draftRationale.trim() || null
			});
			editing = false;
			dpPeriod = nextPeriod(kpi.latest_period, kpi.cadence);
		} catch (e) {
			editError = e instanceof Error ? e.message : 'Failed to save the KPI';
		} finally {
			saving = false;
		}
	}

	// ----- Soft delete (two-step confirm) -----

	let confirmingDelete = false;
	let deleting = false;
	let deleteError: string | null = null;

	async function confirmDelete() {
		if (!kpi) return;
		deleting = true;
		deleteError = null;
		try {
			await managementKpisApi.deleteKpi(kpi.id);
			goto('/lq-ai/management/kpis');
		} catch (e) {
			deleteError = e instanceof Error ? e.message : 'Failed to delete the KPI';
			deleting = false;
		}
	}

	// ----- Trend chart geometry -----

	const CHART_H = 260;
	const MARGIN = { top: 16, right: 64, bottom: 32, left: 56 };
	let chartWidth = 640; // bound to the container's clientWidth

	interface ChartPoint {
		x: number;
		y: number;
		period: string;
		value: string;
		note: string | null;
	}

	interface ChartModel {
		points: ChartPoint[];
		linePath: string;
		yTicks: { y: number; label: string }[];
		xTicks: { x: number; label: string }[];
		targetY: number | null;
		baselineY: number | null;
		yMin: number;
		clipped: boolean;
		innerLeft: number;
		innerRight: number;
		innerTop: number;
		innerBottom: number;
	}

	function niceStep(rough: number): number {
		const pow = Math.pow(10, Math.floor(Math.log10(rough)));
		const frac = rough / pow;
		const nice = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
		return nice * pow;
	}

	function tickLabel(v: number, unit: string): string {
		if (unit === 'USD' && Math.abs(v) >= 1000) {
			return v.toLocaleString('en-US', {
				style: 'currency',
				currency: 'USD',
				notation: 'compact',
				minimumFractionDigits: 0,
				maximumFractionDigits: 1
			});
		}
		const n = v.toLocaleString('en-US', { maximumFractionDigits: 2 });
		return unit === '%' ? `${n}%` : unit === 'USD' ? `$${n}` : n;
	}

	function buildChart(k: KpiRead, dps: KpiDatapoint[], width: number): ChartModel | null {
		const rows = dps.map((d) => ({ ...d, n: Number(d.value) })).filter((d) => Number.isFinite(d.n));
		if (rows.length === 0) return null;

		const innerLeft = MARGIN.left;
		const innerRight = Math.max(width - MARGIN.right, innerLeft + 40);
		const innerTop = MARGIN.top;
		const innerBottom = CHART_H - MARGIN.bottom;

		const values = rows.map((d) => d.n);
		const refs: number[] = [];
		const targetN = k.target !== null && k.target !== undefined ? Number(k.target) : NaN;
		const baselineN = k.baseline !== null && k.baseline !== undefined ? Number(k.baseline) : NaN;
		if (Number.isFinite(targetN)) refs.push(targetN);
		if (Number.isFinite(baselineN)) refs.push(baselineN);

		// One y-axis. Zero-based for count/USD/% units; 'days' (and other
		// unlisted units) may use a tight domain — the min tick labels the clip.
		const zeroBased = k.unit === 'count' || k.unit === 'USD' || k.unit === '%';
		let lo = zeroBased ? 0 : Math.min(...values, ...refs);
		let hi = Math.max(...values, ...refs);
		if (!zeroBased) {
			const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.1 || 1;
			lo = Math.max(0, lo - pad);
		}
		if (hi === lo) hi = lo + 1;
		hi = hi + (hi - lo) * 0.06;

		const step = niceStep((hi - lo) / 4);
		const tickStart = Math.ceil(lo / step) * step;
		const yTickVals: number[] = [];
		for (let v = tickStart; v <= hi + 1e-9; v += step) yTickVals.push(v);
		if (yTickVals.length === 0 || Math.abs(yTickVals[0] - lo) > 1e-9) yTickVals.unshift(lo);

		const y = (v: number) => innerBottom - ((v - lo) / (hi - lo)) * (innerBottom - innerTop);
		const x = (i: number) =>
			rows.length === 1
				? (innerLeft + innerRight) / 2
				: innerLeft + (i / (rows.length - 1)) * (innerRight - innerLeft);

		const points: ChartPoint[] = rows.map((d, i) => ({
			x: x(i),
			y: y(d.n),
			period: d.period,
			value: d.value,
			note: d.note_md ?? null
		}));
		const linePath = `M${points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' L')}`;

		// Label every Nth period so labels don't collide (~70px per label).
		const maxLabels = Math.max(2, Math.floor((innerRight - innerLeft) / 70));
		const every = Math.max(1, Math.ceil(rows.length / maxLabels));
		const xTicks = points
			.map((p, i) => ({ x: p.x, label: periodLabel(p.period), i }))
			.filter(({ i }) => i % every === 0 || i === rows.length - 1);

		return {
			points,
			linePath,
			yTicks: yTickVals.map((v) => ({ y: y(v), label: tickLabel(v, k.unit) })),
			xTicks,
			targetY: Number.isFinite(targetN) ? y(targetN) : null,
			baselineY: Number.isFinite(baselineN) ? y(baselineN) : null,
			yMin: lo,
			clipped: !zeroBased && lo > 0,
			innerLeft,
			innerRight,
			innerTop,
			innerBottom
		};
	}

	$: chart = kpi ? buildChart(kpi, datapoints, chartWidth) : null;

	// ----- Chart hover layer -----

	let hover: ChartPoint | null = null;

	function onChartMove(e: MouseEvent) {
		if (!chart) return;
		const svg = e.currentTarget as SVGElement;
		const rect = svg.getBoundingClientRect();
		const mx = ((e.clientX - rect.left) / rect.width) * chartWidth;
		let nearest: ChartPoint | null = null;
		let best = Infinity;
		for (const p of chart.points) {
			const d = Math.abs(p.x - mx);
			if (d < best) {
				best = d;
				nearest = p;
			}
		}
		hover = nearest;
	}

	function onChartLeave() {
		hover = null;
	}

	// ----- Record datapoint -----

	let dpPeriod = '';
	let dpValue = '';
	let dpNote = '';
	let dpError: string | null = null;
	let dpSubmitting = false;
	/** Set when a 409 came back; carries the period + the existing value. */
	let overwritePrompt: { period: string; existing: string | null } | null = null;

	async function submitDatapoint(overwrite = false) {
		if (!kpi) return;
		if (!dpPeriod.trim()) {
			dpError = 'A period is required.';
			return;
		}
		if (!dpValue.trim() || !Number.isFinite(Number(dpValue.trim()))) {
			dpError = 'The value must be a number.';
			return;
		}
		dpSubmitting = true;
		dpError = null;
		try {
			await managementKpisApi.createDatapoint(
				kpi.id,
				{
					period: dpPeriod.trim(),
					value: dpValue.trim(),
					note_md: dpNote.trim() || undefined
				},
				{ overwrite }
			);
			overwritePrompt = null;
			dpValue = '';
			dpNote = '';
			await loadSeries();
		} catch (e) {
			if (e instanceof LQAIApiError && e.status === 409 && !overwrite) {
				const existing = datapoints.find((d) => d.period === dpPeriod.trim())?.value ?? null;
				overwritePrompt = { period: dpPeriod.trim(), existing };
			} else {
				dpError = e instanceof Error ? e.message : 'Failed to record the datapoint';
			}
		} finally {
			dpSubmitting = false;
		}
	}

	$: tableRows = [...datapoints].reverse();

	$: delta = kpi ? deltaOf(kpi.latest_value, kpi.previous_value, kpi.direction, kpi.unit) : null;
</script>

<main class="kdt-page" data-testid="lq-ai-mgmt-kpi-detail-page">
	<a class="kdt-back" href={backHref}>← Back</a>

	{#if loading}
		<p class="lq-text-body kdt-state-msg">Loading KPI…</p>
	{:else if error || !kpi}
		<p class="lq-text-body kdt-state-msg kdt-state-msg--error" role="alert">
			{error ?? 'KPI not found'}
		</p>
	{:else}
		<header class="kdt-header">
			<div class="kdt-header__main">
				<h1 class="lq-text-page-h">{kpi.name}</h1>
				<p class="kdt-meta">
					{departmentLabel(kpi.department)}
					· {SCOPE_LABELS[kpi.scope]}{#if kpi.scope === 'individual' && memberName}&nbsp;· {memberName}{/if}
					· {CADENCE_LABELS[kpi.cadence]}
					· {DIRECTION_LABELS[kpi.direction]}
				</p>
			</div>
			<div class="kdt-header__actions">
				{#if !editing}
					<button
						type="button"
						class="kdt-btn-secondary"
						data-testid="lq-ai-mgmt-kpi-detail-edit"
						on:click={startEdit}
					>
						Edit
					</button>
				{/if}
				{#if confirmingDelete}
					<span class="kdt-confirm" role="alertdialog" aria-label="Confirm KPI deletion">
						Delete this KPI?
						<button
							type="button"
							class="kdt-btn-danger"
							disabled={deleting}
							data-testid="lq-ai-mgmt-kpi-detail-delete-confirm"
							on:click={confirmDelete}
						>
							{deleting ? 'Deleting…' : 'Yes, delete'}
						</button>
						<button
							type="button"
							class="kdt-btn-secondary"
							disabled={deleting}
							on:click={() => (confirmingDelete = false)}
						>
							Cancel
						</button>
					</span>
				{:else}
					<button
						type="button"
						class="kdt-btn-ghost-danger"
						data-testid="lq-ai-mgmt-kpi-detail-delete"
						on:click={() => (confirmingDelete = true)}
					>
						Delete
					</button>
				{/if}
			</div>
		</header>
		{#if deleteError}
			<p class="kdt-inline-error" role="alert">{deleteError}</p>
		{/if}

		{#if editing}
			<section class="kdt-section" aria-label="Edit KPI">
				<form class="kdt-edit-form" on:submit|preventDefault={saveEdit}>
					<div class="kdt-form-row">
						<label class="kdt-label" for="kdt-name">Name</label>
						<input
							id="kdt-name"
							class="kdt-input"
							type="text"
							bind:value={draftName}
							maxlength="200"
						/>
					</div>
					<div class="kdt-form-grid">
						<div class="kdt-form-row">
							<label class="kdt-label" for="kdt-department">Department</label>
							<select id="kdt-department" class="kdt-input" bind:value={draftDepartment}>
								{#each DEPARTMENT_OPTIONS as opt (opt.value)}
									<option value={opt.value}>{opt.label}</option>
								{/each}
							</select>
						</div>
						<div class="kdt-form-row">
							<label class="kdt-label" for="kdt-unit">Unit</label>
							<input
								id="kdt-unit"
								class="kdt-input"
								type="text"
								bind:value={draftUnit}
								maxlength="32"
							/>
						</div>
						<div class="kdt-form-row">
							<label class="kdt-label" for="kdt-cadence">Cadence</label>
							<select id="kdt-cadence" class="kdt-input" bind:value={draftCadence}>
								{#each CADENCE_OPTIONS as opt (opt.value)}
									<option value={opt.value}>{opt.label}</option>
								{/each}
							</select>
						</div>
						<div class="kdt-form-row">
							<label class="kdt-label" for="kdt-direction">Direction</label>
							<select id="kdt-direction" class="kdt-input" bind:value={draftDirection}>
								{#each DIRECTION_OPTIONS as opt (opt.value)}
									<option value={opt.value}>{opt.label}</option>
								{/each}
							</select>
						</div>
						<div class="kdt-form-row">
							<label class="kdt-label" for="kdt-baseline">Baseline</label>
							<input
								id="kdt-baseline"
								class="kdt-input"
								type="text"
								inputmode="decimal"
								bind:value={draftBaseline}
							/>
						</div>
						<div class="kdt-form-row">
							<label class="kdt-label" for="kdt-target">Target</label>
							<input
								id="kdt-target"
								class="kdt-input"
								type="text"
								inputmode="decimal"
								bind:value={draftTarget}
								data-testid="lq-ai-mgmt-kpi-detail-target-input"
							/>
						</div>
					</div>
					<div class="kdt-form-row">
						<label class="kdt-label" for="kdt-rationale">Why this number predicts success</label>
						<textarea id="kdt-rationale" class="kdt-textarea" rows="3" bind:value={draftRationale}
						></textarea>
					</div>
					{#if editError}
						<p class="kdt-inline-error" role="alert">{editError}</p>
					{/if}
					<div class="kdt-form-actions">
						<button
							type="button"
							class="kdt-btn-secondary"
							disabled={saving}
							on:click={() => (editing = false)}
						>
							Cancel
						</button>
						<button
							type="submit"
							class="kdt-btn-primary"
							disabled={saving}
							data-testid="lq-ai-mgmt-kpi-detail-save"
						>
							{saving ? 'Saving…' : 'Save changes'}
						</button>
					</div>
				</form>
			</section>
		{:else}
			<section class="kdt-stats" aria-label="Current standing">
				<div class="kdt-stat">
					<span class="kdt-stat__caption">Latest ({periodLabel(kpi.latest_period)})</span>
					<span class="kdt-stat__value">
						{formatKpiValue(kpi.latest_value, kpi.unit)}
						{#if unitSuffix(kpi.unit) && kpi.latest_value !== null}
							<span class="kdt-stat__unit">{unitSuffix(kpi.unit)}</span>
						{/if}
					</span>
					{#if delta}
						<span
							class="kdt-stat__delta"
							class:kdt-stat__delta--good={delta.improving}
							class:kdt-stat__delta--bad={!delta.improving}
						>
							<span aria-hidden="true">{delta.improving ? '▲' : '▼'}</span>
							{delta.text} vs prior
						</span>
					{/if}
				</div>
				<div class="kdt-stat">
					<span class="kdt-stat__caption">Target</span>
					<span class="kdt-stat__value">{formatKpiValue(kpi.target, kpi.unit)}</span>
					{#if attainmentLabel(kpi.attainment_pct)}
						<span class={`kdt-chip kdt-chip--${attainmentTone(kpi.attainment_pct)}`}>
							{attainmentLabel(kpi.attainment_pct)}
						</span>
					{/if}
				</div>
				<div class="kdt-stat">
					<span class="kdt-stat__caption">Baseline</span>
					<span class="kdt-stat__value">{formatKpiValue(kpi.baseline, kpi.unit)}</span>
				</div>
				<div class="kdt-stat">
					<span class="kdt-stat__caption">Datapoints</span>
					<span class="kdt-stat__value">{kpi.datapoint_count}</span>
				</div>
			</section>
			{#if kpi.rationale_md}
				<p class="kdt-rationale">{kpi.rationale_md}</p>
			{/if}
		{/if}

		<!-- ── Trend chart ─────────────────────────────────────────── -->
		<section class="kdt-section" aria-labelledby="kdt-chart-h">
			<h2 id="kdt-chart-h" class="kdt-section__title">Trend</h2>
			{#if !chart}
				<p class="kdt-empty-line">
					No datapoints yet — record the first one below and the trend appears here.
				</p>
			{:else}
				<div
					class="kdt-chart-wrap"
					bind:clientWidth={chartWidth}
					data-testid="lq-ai-mgmt-kpi-detail-chart"
				>
					<svg
						class="kdt-chart"
						viewBox={`0 0 ${chartWidth} ${CHART_H}`}
						width="100%"
						height={CHART_H}
						role="img"
						aria-label={`Trend of ${kpi.name}: ${chart.points.length} ${
							chart.points.length === 1 ? 'period' : 'periods'
						}, latest ${formatKpiValue(kpi.latest_value, kpi.unit)}. Full data in the table below.`}
						on:mousemove={onChartMove}
						on:mouseleave={onChartLeave}
					>
						<title>Trend of {kpi.name} — the table below carries the same data</title>

						<!-- recessive grid + y tick labels (text tokens, never accent) -->
						{#each chart.yTicks as t (t.y)}
							<line
								x1={chart.innerLeft}
								x2={chart.innerRight}
								y1={t.y}
								y2={t.y}
								class="kdt-chart__grid"
							/>
							<text x={chart.innerLeft - 8} y={t.y + 3} class="kdt-chart__ytick">{t.label}</text>
						{/each}

						<!-- x ticks -->
						{#each chart.xTicks as t (t.x)}
							<line
								x1={t.x}
								x2={t.x}
								y1={chart.innerBottom}
								y2={chart.innerBottom + 4}
								class="kdt-chart__tickmark"
							/>
							<text x={t.x} y={chart.innerBottom + 18} class="kdt-chart__xtick">{t.label}</text>
						{/each}

						<!-- reference lines: target (dashed) + baseline (dotted), muted ink -->
						{#if chart.targetY !== null}
							<line
								x1={chart.innerLeft}
								x2={chart.innerRight}
								y1={chart.targetY}
								y2={chart.targetY}
								class="kdt-chart__ref kdt-chart__ref--target"
							/>
							<text x={chart.innerRight + 6} y={chart.targetY + 3} class="kdt-chart__reflabel">
								target
							</text>
						{/if}
						{#if chart.baselineY !== null}
							<line
								x1={chart.innerLeft}
								x2={chart.innerRight}
								y1={chart.baselineY}
								y2={chart.baselineY}
								class="kdt-chart__ref kdt-chart__ref--baseline"
							/>
							<text x={chart.innerRight + 6} y={chart.baselineY + 3} class="kdt-chart__reflabel">
								baseline
							</text>
						{/if}

						<!-- the series -->
						{#if chart.points.length > 1}
							<path d={chart.linePath} class="kdt-chart__line" />
						{/if}
						{#each chart.points as p (p.period)}
							<!-- ≥8px hit area behind the visible dot -->
							<circle cx={p.x} cy={p.y} r="9" class="kdt-chart__hit" />
							<circle
								cx={p.x}
								cy={p.y}
								r={hover?.period === p.period ? 4.5 : 3}
								class="kdt-chart__dot"
							/>
						{/each}

						<!-- crosshair -->
						{#if hover}
							<line
								x1={hover.x}
								x2={hover.x}
								y1={chart.innerTop}
								y2={chart.innerBottom}
								class="kdt-chart__crosshair"
							/>
						{/if}

						<!-- transparent hover-capture overlay -->
						<rect
							x={chart.innerLeft}
							y={chart.innerTop}
							width={chart.innerRight - chart.innerLeft}
							height={chart.innerBottom - chart.innerTop}
							fill="transparent"
						/>
					</svg>

					{#if hover}
						<div
							class="kdt-tooltip"
							style={`left: ${Math.min(Math.max(hover.x, 70), chartWidth - 80)}px; top: ${Math.max(hover.y - 12, 8)}px;`}
							role="status"
						>
							<span class="kdt-tooltip__period">{periodLabel(hover.period)}</span>
							<span class="kdt-tooltip__value">{formatKpiValue(hover.value, kpi.unit)}</span>
							{#if hover.note}
								<span class="kdt-tooltip__note">{hover.note}</span>
							{/if}
						</div>
					{/if}
				</div>
				{#if chart.clipped}
					<p class="kdt-chart-note">
						y-axis starts at {tickLabel(chart.yMin, kpi.unit)} (not zero) to show the movement in
						{kpi.unit}.
					</p>
				{/if}
			{/if}
		</section>

		<!-- ── Record datapoint ────────────────────────────────────── -->
		<section class="kdt-section" aria-labelledby="kdt-record-h">
			<h2 id="kdt-record-h" class="kdt-section__title">Record datapoint</h2>
			<form
				class="kdt-inline-form"
				on:submit|preventDefault={() => submitDatapoint(false)}
				data-testid="lq-ai-mgmt-kpi-detail-datapoint-form"
			>
				<label class="kdt-inline-label">
					Period
					<input
						type="text"
						class="kdt-input kdt-input--compact"
						bind:value={dpPeriod}
						placeholder={kpi.cadence === 'monthly' ? 'YYYY-MM' : 'YYYY-Qn'}
						aria-label={`Period (${kpi.cadence === 'monthly' ? 'YYYY-MM' : 'YYYY-Qn'})`}
						disabled={dpSubmitting}
						data-testid="lq-ai-mgmt-kpi-detail-datapoint-period"
					/>
				</label>
				<label class="kdt-inline-label">
					Value
					<input
						type="text"
						inputmode="decimal"
						class="kdt-input kdt-input--compact"
						bind:value={dpValue}
						placeholder={kpi.unit}
						aria-label={`Value in ${kpi.unit}`}
						disabled={dpSubmitting}
						data-testid="lq-ai-mgmt-kpi-detail-datapoint-value"
					/>
				</label>
				<label class="kdt-inline-label kdt-inline-form__grow">
					Note
					<input
						type="text"
						class="kdt-input kdt-input--compact"
						bind:value={dpNote}
						placeholder="Context worth remembering (optional)"
						aria-label="Datapoint note (optional)"
						disabled={dpSubmitting}
					/>
				</label>
				<button
					type="submit"
					class="kdt-btn-primary"
					disabled={dpSubmitting}
					data-testid="lq-ai-mgmt-kpi-detail-datapoint-submit"
				>
					{dpSubmitting ? 'Recording…' : 'Record'}
				</button>
			</form>
			{#if dpError}
				<p class="kdt-inline-error" role="alert">{dpError}</p>
			{/if}
			{#if overwritePrompt}
				<div
					class="kdt-overwrite"
					role="alertdialog"
					aria-label="Confirm datapoint overwrite"
					data-testid="lq-ai-mgmt-kpi-detail-overwrite-confirm"
				>
					<p class="kdt-overwrite__copy">
						{periodLabel(overwritePrompt.period)} already has a value{#if overwritePrompt.existing !== null}
							({overwritePrompt.existing}){/if}. Overwrite?
					</p>
					<div class="kdt-overwrite__actions">
						<button
							type="button"
							class="kdt-btn-primary"
							disabled={dpSubmitting}
							data-testid="lq-ai-mgmt-kpi-detail-overwrite-yes"
							on:click={() => submitDatapoint(true)}
						>
							Overwrite
						</button>
						<button
							type="button"
							class="kdt-btn-secondary"
							disabled={dpSubmitting}
							on:click={() => (overwritePrompt = null)}
						>
							Cancel
						</button>
					</div>
				</div>
			{/if}
		</section>

		<!-- ── Datapoint table (the chart's accessible data view) ──── -->
		<section class="kdt-section" aria-labelledby="kdt-table-h">
			<h2 id="kdt-table-h" class="kdt-section__title">Datapoints</h2>
			{#if tableRows.length === 0}
				<p class="kdt-empty-line">No datapoints recorded yet.</p>
			{:else}
				<div class="kdt-table-wrap">
					<table class="kdt-table" data-testid="lq-ai-mgmt-kpi-detail-table">
						<caption class="kdt-visually-hidden">
							All datapoints for {kpi.name}, newest first — the data behind the trend chart.
						</caption>
						<thead>
							<tr>
								<th scope="col">Period</th>
								<th scope="col">Value</th>
								<th scope="col">Note</th>
								<th scope="col">Recorded</th>
							</tr>
						</thead>
						<tbody>
							{#each tableRows as row (row.period)}
								<tr>
									<th scope="row">{periodLabel(row.period)}</th>
									<td>{formatKpiValue(row.value, kpi.unit)}</td>
									<td class="kdt-table__note">{row.note_md ?? '—'}</td>
									<td>{row.created_at ? formatDateTime(row.created_at) : '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/if}
</main>

<style>
	.kdt-page {
		padding: var(--lq-space-6);
		max-width: 950px;
		margin: 0 auto;
	}

	.kdt-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.kdt-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.kdt-state-msg--error {
		color: var(--lq-error);
	}

	.kdt-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-3);
	}

	.kdt-meta {
		margin-top: var(--lq-space-1);
		color: var(--lq-text-tertiary);
		font-size: 0.85rem;
	}

	.kdt-header__actions {
		display: flex;
		gap: var(--lq-space-2);
		align-items: center;
		flex-wrap: wrap;
	}

	.kdt-confirm {
		display: inline-flex;
		align-items: center;
		gap: var(--lq-space-2);
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
	}

	.kdt-stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: var(--lq-space-3);
		margin-bottom: var(--lq-space-3);
	}

	.kdt-stat {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-3);
	}

	.kdt-stat__caption {
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--lq-text-tertiary);
	}

	.kdt-stat__value {
		font-size: 1.35rem;
		font-weight: 700;
		color: var(--lq-text-primary);
		line-height: 1.15;
	}

	.kdt-stat__unit {
		font-size: 0.8rem;
		font-weight: 500;
		color: var(--lq-text-tertiary);
	}

	.kdt-stat__delta {
		font-size: 0.8rem;
		color: var(--lq-text-secondary);
	}

	.kdt-stat__delta--good {
		color: var(--lq-accent);
		font-weight: 600;
	}

	.kdt-stat__delta--bad {
		color: var(--lq-error);
		font-weight: 600;
	}

	.kdt-chip {
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

	.kdt-chip--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}

	.kdt-chip--info {
		background: var(--lq-tier-soft);
		color: var(--lq-tier);
		border-color: var(--lq-tier-border);
	}

	.kdt-chip--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}

	.kdt-chip--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}

	.kdt-chip--muted {
		background: var(--lq-inset);
		color: var(--lq-text-tertiary);
		border-color: var(--lq-border);
	}

	.kdt-rationale {
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
		line-height: 1.6;
		white-space: pre-wrap;
		margin: 0 0 var(--lq-space-4);
	}

	.kdt-section {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-4);
		margin-bottom: var(--lq-space-4);
	}

	.kdt-section__title {
		font-size: 1rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0 0 var(--lq-space-3);
	}

	.kdt-empty-line {
		color: var(--lq-text-tertiary);
		font-size: 0.9rem;
		margin: 0;
	}

	/* ── Chart ── */

	.kdt-chart-wrap {
		position: relative;
		width: 100%;
	}

	.kdt-chart {
		display: block;
		width: 100%;
	}

	.kdt-chart__grid {
		stroke: var(--lq-border);
		stroke-width: 1;
	}

	.kdt-chart__tickmark {
		stroke: var(--lq-border);
		stroke-width: 1;
	}

	.kdt-chart__ytick {
		fill: var(--lq-text-tertiary);
		font-size: 11px;
		text-anchor: end;
	}

	.kdt-chart__xtick {
		fill: var(--lq-text-tertiary);
		font-size: 11px;
		text-anchor: middle;
	}

	.kdt-chart__ref {
		stroke: var(--lq-text-tertiary);
		stroke-width: 1;
	}

	.kdt-chart__ref--target {
		stroke-dasharray: 6 4;
	}

	.kdt-chart__ref--baseline {
		stroke-dasharray: 2 3;
	}

	.kdt-chart__reflabel {
		fill: var(--lq-text-tertiary);
		font-size: 10px;
		text-anchor: start;
	}

	.kdt-chart__line {
		fill: none;
		stroke: var(--lq-accent);
		stroke-width: 2;
		stroke-linejoin: round;
		stroke-linecap: round;
	}

	.kdt-chart__dot {
		fill: var(--lq-accent);
		pointer-events: none;
	}

	.kdt-chart__hit {
		fill: transparent;
	}

	.kdt-chart__crosshair {
		stroke: var(--lq-border);
		stroke-width: 1;
		pointer-events: none;
	}

	.kdt-tooltip {
		position: absolute;
		transform: translate(-50%, -100%);
		display: flex;
		flex-direction: column;
		gap: 2px;
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
		padding: var(--lq-space-1) var(--lq-space-2);
		pointer-events: none;
		max-width: 240px;
		z-index: 10;
	}

	.kdt-tooltip__period {
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.03em;
		text-transform: uppercase;
		color: var(--lq-text-tertiary);
	}

	.kdt-tooltip__value {
		font-size: 0.9rem;
		font-weight: 700;
		color: var(--lq-text-primary);
	}

	.kdt-tooltip__note {
		font-size: 0.75rem;
		color: var(--lq-text-secondary);
	}

	.kdt-chart-note {
		margin: var(--lq-space-2) 0 0;
		font-size: 0.75rem;
		color: var(--lq-text-tertiary);
	}

	/* ── Forms ── */

	.kdt-inline-form {
		display: flex;
		flex-wrap: wrap;
		gap: var(--lq-space-2);
		align-items: flex-end;
		margin-bottom: var(--lq-space-2);
	}

	.kdt-inline-label {
		display: flex;
		flex-direction: column;
		gap: 2px;
		font-size: 12px;
		font-weight: 500;
		color: var(--lq-text-secondary);
	}

	.kdt-inline-form__grow {
		flex: 1;
		min-width: 180px;
	}

	.kdt-edit-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-3);
	}

	.kdt-form-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: var(--lq-space-3);
	}

	.kdt-form-row {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.kdt-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.kdt-input,
	.kdt-textarea {
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		font-size: 14px;
		color: var(--lq-text-primary);
		box-sizing: border-box;
		transition: border-color 0.15s ease;
	}

	.kdt-input:focus,
	.kdt-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.kdt-textarea {
		resize: vertical;
	}

	.kdt-input--compact {
		font-size: 13px;
		padding: var(--lq-space-1) var(--lq-space-2);
	}

	.kdt-form-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-3);
	}

	.kdt-inline-error {
		color: var(--lq-error);
		font-size: 13px;
		margin: 0 0 var(--lq-space-2);
	}

	.kdt-overwrite {
		border: 1px solid var(--lq-warn-border);
		background: var(--lq-warn-soft);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-3);
		margin-top: var(--lq-space-2);
	}

	.kdt-overwrite__copy {
		margin: 0 0 var(--lq-space-2);
		font-size: 0.9rem;
		color: var(--lq-text-primary);
	}

	.kdt-overwrite__actions {
		display: flex;
		gap: var(--lq-space-2);
	}

	/* ── Buttons ── */

	.kdt-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.kdt-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.kdt-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kdt-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.kdt-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.kdt-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.kdt-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kdt-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.kdt-btn-danger {
		background: var(--lq-error);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.kdt-btn-danger:focus-visible {
		outline: 2px solid var(--lq-error);
		outline-offset: 2px;
	}

	.kdt-btn-ghost-danger {
		background: transparent;
		color: var(--lq-error);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.kdt-btn-ghost-danger:hover {
		background: var(--lq-error-soft);
	}

	.kdt-btn-ghost-danger:focus-visible {
		outline: 2px solid var(--lq-error);
		outline-offset: 2px;
	}

	/* ── Table ── */

	.kdt-table-wrap {
		overflow-x: auto;
	}

	.kdt-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.9rem;
	}

	.kdt-table th,
	.kdt-table td {
		text-align: left;
		padding: var(--lq-space-2) var(--lq-space-3);
		border-bottom: 1px solid var(--lq-border);
		color: var(--lq-text-primary);
	}

	.kdt-table thead th {
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--lq-text-tertiary);
	}

	.kdt-table tbody th {
		font-weight: 600;
		white-space: nowrap;
	}

	.kdt-table__note {
		color: var(--lq-text-secondary);
	}

	.kdt-visually-hidden {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}
</style>
