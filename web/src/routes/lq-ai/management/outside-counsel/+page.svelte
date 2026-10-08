<!--
  /lq-ai/management/outside-counsel — the Outside Counsel module.

  "Trackable spend — by quarter, by year, by firm." One GET /summary call
  drives the Spend and Alerts sections; firms, invoices and the value
  ledger load alongside it. Everything is entered by hand here or through
  the MCP connector (AI invoice upload is on the roadmap). The GC's policy
  — 10% minimum discount, 5% maximum increase, two billers per task,
  110%/125% budget bands — is applied server-side; this page renders it.
  "Draft spend story" runs the spend_story AI job (draft-then-confirm).
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { managementAiApi, managementOutsideCounselApi as oc } from '$lib/lq-ai/api';
	import type {
		OcBand,
		OcFirm,
		OcInvoice,
		OcInvoiceLineIn,
		OcPracticeArea,
		OcSummary,
		OcValueCategory,
		OcValueEntry
	} from '$lib/lq-ai/types';
	import {
		PRACTICE_AREAS,
		TIMEKEEPER_TITLES,
		VALUE_CATEGORIES,
		areaLabel,
		bandLabel,
		bandTone,
		barPct,
		categoryLabel,
		formatMoney,
		formatPct,
		partnerStatusLabel,
		quarterLabel
	} from '$lib/lq-ai/management/outsideCounsel';
	import MgmtAiJobRunner from '$lib/lq-ai/components/MgmtAiJobRunner.svelte';

	let year = new Date().getFullYear();
	let summary: OcSummary | null = null;
	let firms: OcFirm[] = [];
	let invoices: OcInvoice[] = [];
	let ledger: OcValueEntry[] = [];
	let loading = true;
	let error: string | null = null;
	let actionError: string | null = null;

	async function refresh() {
		loading = summary === null;
		try {
			[summary, firms, invoices, ledger] = await Promise.all([
				oc.getSummary(year),
				oc.listFirms(),
				oc.listInvoices({ year }),
				oc.listValueEntries({ year })
			]);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load Outside Counsel';
		} finally {
			loading = false;
		}
	}

	onMount(refresh);

	function changeYear(delta: number) {
		year += delta;
		void refresh();
	}

	async function act(fn: () => Promise<unknown>) {
		actionError = null;
		try {
			await fn();
			await refresh();
			return true;
		} catch (e) {
			actionError = e instanceof Error ? e.message : 'That change was not saved.';
			return false;
		}
	}

	$: maxFirm = Math.max(0, ...(summary?.by_firm ?? []).map((r) => Number(r.amount)));
	$: maxArea = Math.max(0, ...(summary?.by_practice_area ?? []).map((r) => Number(r.amount)));
	let yearBand: OcBand | null = null;
	$: yearBand =
		summary?.year_pct_of_budget == null
			? null
			: Number(summary.year_pct_of_budget) >= 125
				? 'red'
				: Number(summary.year_pct_of_budget) >= 110
					? 'yellow'
					: 'green';
	$: alertCount = summary
		? summary.partner_alerts.length + summary.rate_flags.length + summary.staffing_flags.length
		: 0;

	// ----- Spend story (AI) -----
	let storyJobId: string | null = null;
	let storyStarting = false;
	let storyError: string | null = null;

	async function draftStory() {
		storyStarting = true;
		storyError = null;
		try {
			const job = await managementAiApi.createAiJob({ job_type: 'spend_story' });
			storyJobId = job.id;
		} catch (e) {
			storyError = e instanceof Error ? e.message : 'Could not start the spend story';
		} finally {
			storyStarting = false;
		}
	}

	// ----- Budgets -----
	let budgetPeriod = `${year}-Q1`;
	let budgetArea: 'all' | OcPracticeArea = 'all';
	let budgetAmount = '';

	async function saveBudget() {
		if (!budgetAmount) return;
		const ok = await act(() =>
			oc.upsertBudget({ period: budgetPeriod, practice_area: budgetArea, amount: budgetAmount })
		);
		if (ok) budgetAmount = '';
	}

	// ----- Firms & partners -----
	let showFirmForm = false;
	let firmName = '';
	let firmDiscount = '';
	let firmIncrease = '';

	async function addFirm() {
		if (!firmName.trim()) return;
		const ok = await act(() =>
			oc.createFirm({
				name: firmName.trim(),
				discount_pct: firmDiscount || null,
				rate_increase_pct: firmIncrease || null,
				rate_year: firmIncrease ? year : null
			})
		);
		if (ok) {
			firmName = firmDiscount = firmIncrease = '';
			showFirmForm = false;
		}
	}

	let partnerDraft: Record<string, string> = {};

	async function addPartner(firmId: string) {
		const name = (partnerDraft[firmId] ?? '').trim();
		if (!name) return;
		const ok = await act(() => oc.addPartner(firmId, { name }));
		if (ok) partnerDraft = { ...partnerDraft, [firmId]: '' };
	}

	// ----- Invoices -----
	let openInvoiceId: string | null = null;
	let showInvoiceForm = false;
	let invFirmId = '';
	let invNumber = '';
	let invDate = new Date().toISOString().slice(0, 10);
	let invArea: OcPracticeArea = 'commercial';
	let invLines: OcInvoiceLineIn[] = [];

	function blankLine(): OcInvoiceLineIn {
		return {
			work_date: invDate,
			timekeeper: '',
			title: 'associate',
			task: '',
			hours: '',
			rate: ''
		};
	}

	function openInvoiceForm() {
		showInvoiceForm = true;
		invFirmId = invFirmId || firms[0]?.id || '';
		if (invLines.length === 0) invLines = [blankLine()];
	}

	$: invTotal = invLines.reduce(
		(sum, l) => sum + (Number(l.hours) || 0) * (Number(l.rate) || 0),
		0
	);

	async function addInvoice() {
		const lines = invLines.filter((l) => l.timekeeper.trim() && l.task.trim() && l.hours && l.rate);
		if (!invFirmId || lines.length === 0) {
			actionError = 'Pick a firm and enter at least one complete line.';
			return;
		}
		const ok = await act(() =>
			oc.createInvoice({
				firm_id: invFirmId,
				invoice_number: invNumber || null,
				invoice_date: invDate,
				practice_area: invArea,
				lines
			})
		);
		if (ok) {
			invNumber = '';
			invLines = [];
			showInvoiceForm = false;
		}
	}

	function flaggedKey(inv: OcInvoice, date: string, timekeeper: string): boolean {
		return inv.staffing_flags.some(
			(f) => f.work_date === date && f.timekeepers.includes(timekeeper.trim())
		);
	}

	// ----- Value ledger -----
	let showLedgerForm = false;
	let vPeriod = `${year}-Q1`;
	let vCategory: OcValueCategory = 'billing_adjustments';
	let vAmount = '';
	let vDescription = '';
	let vMethod = '';
	let vSource = '';

	async function addLedgerEntry() {
		if (!vAmount || !vDescription.trim() || !vMethod.trim() || !vSource.trim()) {
			actionError = 'Every ledger entry needs an amount, a description, its method and its source.';
			return;
		}
		const ok = await act(() =>
			oc.createValueEntry({
				period: vPeriod,
				category: vCategory,
				amount: vAmount,
				description: vDescription.trim(),
				method_note: vMethod.trim(),
				source: vSource.trim()
			})
		);
		if (ok) {
			vAmount = vDescription = vMethod = vSource = '';
			showLedgerForm = false;
		}
	}

	$: quarters = [1, 2, 3, 4].map((q) => `${year}-Q${q}`);
</script>

<main class="ocp-page" data-testid="lq-ai-mgmt-oc-page">
	<a class="ocp-back" href="/lq-ai/management">← Management</a>

	<header class="ocp-header">
		<div>
			<h1 class="lq-text-page-h">Outside Counsel</h1>
			<p class="ocp-sub">
				Trackable spend — by quarter, by year, by firm. "I hire lawyers, not law firms."
			</p>
		</div>
		<div class="ocp-header__actions">
			<div class="ocp-year" role="group" aria-label="Year">
				<button type="button" on:click={() => changeYear(-1)} aria-label="Previous year">‹</button>
				<span data-testid="lq-ai-mgmt-oc-year">{year}</span>
				<button type="button" on:click={() => changeYear(1)} aria-label="Next year">›</button>
			</div>
			<button
				type="button"
				class="ocp-btn-ghost"
				on:click={draftStory}
				disabled={storyStarting}
				title="Drafts a memo to the CFO from this page's numbers, with every figure cited. You review it before it goes anywhere."
				data-testid="lq-ai-mgmt-oc-story-btn"
			>
				{storyStarting ? 'Starting…' : 'Draft spend story ✦'}
			</button>
		</div>
	</header>

	{#if summary}
		<p class="ocp-policy">
			Policy: discount at least <b>{summary.min_discount_pct}%</b> · hourly increase at most
			<b>{summary.max_rate_increase_pct}%</b> · no more than
			<b>{summary.max_billers_per_task}</b> people billing one task on one day · spend
			<b>110%</b> of budget is a watch, <b>125%</b> is red.
		</p>
	{/if}

	{#if actionError}
		<p class="ocp-msg ocp-msg--error" role="alert">{actionError}</p>
	{/if}

	{#if storyError}
		<p class="ocp-msg ocp-msg--error" role="alert">{storyError}</p>
	{/if}
	{#if storyJobId}
		<section class="ocp-card ocp-story" aria-label="Spend story draft">
			<div class="ocp-card__head">
				<h2 class="ocp-h2">Spend story — draft for the CFO</h2>
				<button type="button" class="ocp-link" on:click={() => (storyJobId = null)}>Close</button>
			</div>
			<MgmtAiJobRunner
				jobId={storyJobId}
				runningCopy="Drafting your spend story…"
				onRetry={draftStory}
			/>
		</section>
	{/if}

	{#if loading}
		<p class="ocp-msg">Loading Outside Counsel…</p>
	{:else if error}
		<p class="ocp-msg ocp-msg--error" role="alert">Couldn't load Outside Counsel: {error}</p>
	{:else if summary}
		<!-- ===== Alerts ===== -->
		{#if alertCount > 0}
			<section class="ocp-card ocp-alerts" data-testid="lq-ai-mgmt-oc-alerts">
				<h2 class="ocp-h2">Needs your attention</h2>
				<ul class="ocp-alert-list">
					{#each summary.partner_alerts as a (a.partner_id)}
						<li class="ocp-alert ocp-alert--red">
							<b>Partner left:</b>
							{a.partner_name} has left {a.firm_name}{a.left_at ? ` (${a.left_at})` : ''}. Decide
							whether to follow them or choose a replacement.
						</li>
					{/each}
					{#each summary.rate_flags as f (f.firm_id + f.issue)}
						<li class="ocp-alert" class:ocp-alert--red={f.band === 'red'}>
							{#if f.issue === 'discount_below_floor'}
								<b>Discount below {summary.min_discount_pct}%:</b> {f.firm_name} gives {f.value}%.
							{:else}
								<b>Rate increase above {summary.max_rate_increase_pct}%:</b>
								{f.firm_name} raised hourly rates {f.value}%.
							{/if}
						</li>
					{/each}
					{#each summary.staffing_flags as s (s.invoice_id + s.work_date + s.task)}
						<li class="ocp-alert">
							<b>Staffing:</b>
							{s.timekeepers.length} people billed "{s.task}" on {s.work_date} ({s.firm_name}{s.invoice_number
								? `, invoice ${s.invoice_number}`
								: ''}) — {formatMoney(s.amount)}. {s.timekeepers.join(', ')}.
						</li>
					{/each}
				</ul>
			</section>
		{/if}

		<!-- ===== Spend ===== -->
		<section class="ocp-section" aria-labelledby="ocp-spend-h">
			<h2 id="ocp-spend-h" class="ocp-h2">Spend vs budget — {year}</h2>
			<div class="ocp-year-tile">
				<div>
					<p class="ocp-year-tile__label">Year to date</p>
					<p class="ocp-year-tile__hero">{formatMoney(summary.year_actual)}</p>
					<p class="ocp-year-tile__sub">
						of {formatMoney(summary.year_budget)} budget · {formatPct(summary.year_pct_of_budget)}
					</p>
				</div>
				<span class={`ocp-chip ocp-chip--${bandTone(yearBand)}`}>{bandLabel(yearBand)}</span>
			</div>

			<div class="ocp-table-wrap">
				<table class="ocp-table" data-testid="lq-ai-mgmt-oc-quarters">
					<thead>
						<tr><th>Quarter</th><th>Budget</th><th>Actual</th><th>% of budget</th><th></th></tr>
					</thead>
					<tbody>
						{#each summary.quarters as q (q.period)}
							<tr>
								<td>{quarterLabel(q.period)}</td>
								<td>{formatMoney(q.budget)}</td>
								<td>{formatMoney(q.actual)}</td>
								<td>{formatPct(q.pct_of_budget)}</td>
								<td>
									{#if q.band}
										<span class={`ocp-chip ocp-chip--sm ocp-chip--${bandTone(q.band)}`}>
											{bandLabel(q.band)}
										</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<form class="ocp-inline-form" on:submit|preventDefault={saveBudget}>
				<span class="ocp-inline-form__label">Set a budget:</span>
				<select bind:value={budgetPeriod} aria-label="Quarter">
					{#each quarters as p (p)}<option value={p}>{quarterLabel(p)}</option>{/each}
				</select>
				<select bind:value={budgetArea} aria-label="Practice area">
					<option value="all">Department total</option>
					{#each PRACTICE_AREAS as a (a.value)}<option value={a.value}>{a.label}</option>{/each}
				</select>
				<input
					type="number"
					min="0"
					step="1000"
					placeholder="Amount ($)"
					bind:value={budgetAmount}
					aria-label="Budget amount"
				/>
				<button type="submit" class="ocp-btn">Save</button>
			</form>

			<div class="ocp-two-col">
				<div class="ocp-card">
					<h3 class="ocp-h3">By firm</h3>
					{#if summary.by_firm.length === 0}
						<p class="ocp-empty">No invoices recorded for {year}.</p>
					{:else}
						{#each summary.by_firm as r (r.key)}
							<div class="ocp-bar-row">
								<span class="ocp-bar-row__label">{r.label}</span>
								<span class="ocp-bar"
									><span style={`width:${barPct(r.amount, maxFirm)}%`}></span></span
								>
								<span class="ocp-bar-row__value">{formatMoney(r.amount)}</span>
							</div>
						{/each}
					{/if}
				</div>
				<div class="ocp-card">
					<h3 class="ocp-h3">By practice area</h3>
					{#if summary.by_practice_area.length === 0}
						<p class="ocp-empty">No invoices recorded for {year}.</p>
					{:else}
						{#each summary.by_practice_area as r (r.key)}
							<div class="ocp-bar-row">
								<span class="ocp-bar-row__label">{r.label}</span>
								<span class="ocp-bar"
									><span style={`width:${barPct(r.amount, maxArea)}%`}></span></span
								>
								<span class="ocp-bar-row__value">
									{formatMoney(r.amount)}{r.budget ? ` / ${formatMoney(r.budget)}` : ''}
								</span>
							</div>
						{/each}
					{/if}
				</div>
			</div>
		</section>

		<!-- ===== Firms & partners ===== -->
		<section class="ocp-section" aria-labelledby="ocp-firms-h">
			<div class="ocp-section__head">
				<h2 id="ocp-firms-h" class="ocp-h2">Firms &amp; chosen partners</h2>
				<button type="button" class="ocp-btn" on:click={() => (showFirmForm = !showFirmForm)}>
					+ Add firm
				</button>
			</div>
			{#if showFirmForm}
				<form class="ocp-card ocp-form" on:submit|preventDefault={addFirm}>
					<label>Firm name <input bind:value={firmName} maxlength="200" required /></label>
					<label
						>Discount %
						<input type="number" min="0" max="100" step="0.1" bind:value={firmDiscount} /></label
					>
					<label
						>Hourly increase this year %
						<input type="number" step="0.1" bind:value={firmIncrease} /></label
					>
					<button type="submit" class="ocp-btn ocp-btn--primary">Save firm</button>
				</form>
			{/if}
			{#if firms.length === 0}
				<p class="ocp-empty">
					No firms yet. Add the firms on your panel, then the partners you chose.
				</p>
			{:else}
				<div class="ocp-firm-grid">
					{#each firms as f (f.id)}
						<article class="ocp-card ocp-firm" data-testid={`lq-ai-mgmt-oc-firm-${f.id}`}>
							<h3 class="ocp-h3">{f.name}</h3>
							<div class="ocp-firm__chips">
								<span
									class={`ocp-chip ocp-chip--sm ocp-chip--${f.below_discount_floor ? 'error' : f.discount_pct ? 'good' : 'muted'}`}
								>
									Discount {f.discount_pct ? `${f.discount_pct}%` : 'not recorded'}
								</span>
								<span
									class={`ocp-chip ocp-chip--sm ocp-chip--${f.above_increase_cap ? 'warn' : f.rate_increase_pct ? 'good' : 'muted'}`}
								>
									Increase {f.rate_increase_pct ? `${f.rate_increase_pct}%` : 'not recorded'}
								</span>
							</div>
							<p class="ocp-firm__spend">
								{formatMoney(f.spend_total)} across {f.invoice_count}
								{f.invoice_count === 1 ? 'invoice' : 'invoices'} (all years)
							</p>
							<ul class="ocp-partners">
								{#each f.partners as p (p.id)}
									<li class:ocp-partner--left={p.status === 'left_firm'}>
										<span>
											<b>{p.name}</b>
											<span class="ocp-muted">· {partnerStatusLabel(p.status)}</span>
										</span>
										<span class="ocp-partner__actions">
											{#if p.status === 'active'}
												<button
													type="button"
													class="ocp-link"
													on:click={() => act(() => oc.patchPartner(p.id, { status: 'left_firm' }))}
												>
													Partner left the firm
												</button>
											{:else if p.status === 'left_firm'}
												<button
													type="button"
													class="ocp-link"
													on:click={() => act(() => oc.patchPartner(p.id, { status: 'followed' }))}
												>
													Follow them
												</button>
												<button
													type="button"
													class="ocp-link"
													on:click={() => act(() => oc.patchPartner(p.id, { status: 'replaced' }))}
												>
													Chose a replacement
												</button>
											{/if}
										</span>
									</li>
								{/each}
							</ul>
							<form class="ocp-inline-form" on:submit|preventDefault={() => addPartner(f.id)}>
								<input
									placeholder="Add a partner you chose"
									value={partnerDraft[f.id] ?? ''}
									on:input={(e) =>
										(partnerDraft = { ...partnerDraft, [f.id]: e.currentTarget.value })}
									aria-label={`Add a partner at ${f.name}`}
								/>
								<button type="submit" class="ocp-btn">Add</button>
							</form>
						</article>
					{/each}
				</div>
			{/if}
		</section>

		<!-- ===== Invoices ===== -->
		<section class="ocp-section" aria-labelledby="ocp-inv-h">
			<div class="ocp-section__head">
				<h2 id="ocp-inv-h" class="ocp-h2">Invoices — {year}</h2>
				<button
					type="button"
					class="ocp-btn"
					on:click={() => (showInvoiceForm ? (showInvoiceForm = false) : openInvoiceForm())}
					disabled={firms.length === 0}
					title={firms.length === 0 ? 'Add a firm first' : ''}
				>
					+ Add invoice
				</button>
			</div>

			{#if showInvoiceForm}
				<form class="ocp-card ocp-form" on:submit|preventDefault={addInvoice}>
					<div class="ocp-form__row">
						<label
							>Firm
							<select bind:value={invFirmId}>
								{#each firms as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
							</select></label
						>
						<label>Invoice # <input bind:value={invNumber} /></label>
						<label>Invoice date <input type="date" bind:value={invDate} required /></label>
						<label
							>Practice area
							<select bind:value={invArea}>
								{#each PRACTICE_AREAS as a (a.value)}<option value={a.value}>{a.label}</option
									>{/each}
							</select></label
						>
					</div>
					<div class="ocp-table-wrap">
						<table class="ocp-table ocp-table--lines">
							<thead>
								<tr>
									<th>Date</th><th>Timekeeper</th><th>Title</th><th>Task</th><th>Hours</th><th
										>Rate</th
									><th></th>
								</tr>
							</thead>
							<tbody>
								{#each invLines as line, i (i)}
									<tr>
										<td><input type="date" bind:value={line.work_date} aria-label="Work date" /></td
										>
										<td><input bind:value={line.timekeeper} aria-label="Timekeeper" /></td>
										<td>
											<select bind:value={line.title} aria-label="Title">
												{#each TIMEKEEPER_TITLES as t (t.value)}<option value={t.value}
														>{t.label}</option
													>{/each}
											</select>
										</td>
										<td><input bind:value={line.task} aria-label="Task" /></td>
										<td
											><input
												type="number"
												min="0"
												step="0.1"
												bind:value={line.hours}
												aria-label="Hours"
											/></td
										>
										<td
											><input
												type="number"
												min="0"
												step="1"
												bind:value={line.rate}
												aria-label="Rate"
											/></td
										>
										<td>
											<button
												type="button"
												class="ocp-link"
												on:click={() => (invLines = invLines.filter((_, j) => j !== i))}
												aria-label="Remove line">✕</button
											>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					<div class="ocp-form__row">
						<button
							type="button"
							class="ocp-link"
							on:click={() => (invLines = [...invLines, blankLine()])}>+ Add line</button
						>
						<span class="ocp-muted">Total {formatMoney(String(invTotal))}</span>
						<button type="submit" class="ocp-btn ocp-btn--primary">Save invoice</button>
					</div>
				</form>
			{/if}

			{#if invoices.length === 0}
				<p class="ocp-empty">No invoices for {year} yet.</p>
			{:else}
				<div class="ocp-table-wrap">
					<table class="ocp-table" data-testid="lq-ai-mgmt-oc-invoices">
						<thead>
							<tr>
								<th>Date</th><th>Firm</th><th>Invoice</th><th>Area</th><th>Total</th><th>Status</th
								><th></th>
							</tr>
						</thead>
						<tbody>
							{#each invoices as inv (inv.id)}
								<tr
									class="ocp-row-click"
									on:click={() => (openInvoiceId = openInvoiceId === inv.id ? null : inv.id)}
								>
									<td>{inv.invoice_date}</td>
									<td>{inv.firm_name}</td>
									<td>{inv.invoice_number ?? '—'}</td>
									<td>{areaLabel(inv.practice_area)}</td>
									<td>{formatMoney(inv.total)}</td>
									<td>{inv.status === 'paid' ? 'Paid' : 'Received'}</td>
									<td>
										{#if inv.staffing_flags.length > 0}
											<span class="ocp-chip ocp-chip--sm ocp-chip--warn">
												{inv.staffing_flags.length} staffing flag{inv.staffing_flags.length === 1
													? ''
													: 's'}
											</span>
										{/if}
									</td>
								</tr>
								{#if openInvoiceId === inv.id}
									<tr class="ocp-lines-row">
										<td colspan="7">
											<table class="ocp-table ocp-table--inner">
												<thead>
													<tr
														><th>Date</th><th>Timekeeper</th><th>Title</th><th>Task</th><th
															>Hours</th
														><th>Rate</th><th>Amount</th></tr
													>
												</thead>
												<tbody>
													{#each inv.lines as l (l.id)}
														<tr class:ocp-flagged={flaggedKey(inv, l.work_date, l.timekeeper)}>
															<td>{l.work_date}</td>
															<td>{l.timekeeper}</td>
															<td>{l.title}</td>
															<td>{l.task}</td>
															<td>{l.hours}</td>
															<td>{formatMoney(l.rate)}</td>
															<td>{formatMoney(l.amount)}</td>
														</tr>
													{/each}
												</tbody>
											</table>
											<div class="ocp-form__row">
												{#if inv.status !== 'paid'}
													<button
														type="button"
														class="ocp-link"
														on:click={() => act(() => oc.patchInvoice(inv.id, { status: 'paid' }))}
														>Mark paid</button
													>
												{/if}
												<button
													type="button"
													class="ocp-link ocp-link--danger"
													on:click={() => act(() => oc.deleteInvoice(inv.id))}
													>Delete invoice</button
												>
											</div>
										</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>

		<!-- ===== Value ledger ===== -->
		<section class="ocp-section" aria-labelledby="ocp-ledger-h">
			<div class="ocp-section__head">
				<h2 id="ocp-ledger-h" class="ocp-h2">
					Value ledger — {formatMoney(summary.value_total)} in {year}
				</h2>
				<button type="button" class="ocp-btn" on:click={() => (showLedgerForm = !showLedgerForm)}>
					+ Add entry
				</button>
			</div>
			<p class="ocp-sub">
				"Legal pays for itself." Every entry carries how it was calculated and where the number
				comes from — nothing reaches the board without its receipt.
			</p>
			<div class="ocp-cat-grid">
				{#each summary.value_by_category as c (c.key)}
					<div class="ocp-card ocp-cat">
						<p class="ocp-cat__label">{c.label}</p>
						<p class="ocp-cat__value">{formatMoney(c.amount)}</p>
						<p class="ocp-muted">{c.count} {c.count === 1 ? 'entry' : 'entries'}</p>
					</div>
				{/each}
			</div>

			{#if showLedgerForm}
				<form class="ocp-card ocp-form" on:submit|preventDefault={addLedgerEntry}>
					<div class="ocp-form__row">
						<label
							>Quarter
							<select bind:value={vPeriod}>
								{#each quarters as p (p)}<option value={p}>{quarterLabel(p)}</option>{/each}
							</select></label
						>
						<label
							>Category
							<select bind:value={vCategory}>
								{#each VALUE_CATEGORIES as c (c.value)}<option value={c.value}>{c.label}</option
									>{/each}
							</select></label
						>
						<label>Amount ($) <input type="number" min="0" bind:value={vAmount} required /></label>
					</div>
					<label>What it is <input bind:value={vDescription} required /></label>
					<label
						>How it was calculated (required)
						<input
							bind:value={vMethod}
							required
							placeholder="e.g. blended historical cost per contract"
						/></label
					>
					<label
						>Source (required)
						<input bind:value={vSource} required placeholder="e.g. CLM export, Q3" /></label
					>
					<button type="submit" class="ocp-btn ocp-btn--primary">Save entry</button>
				</form>
			{/if}

			{#if ledger.length === 0}
				<p class="ocp-empty">No value-ledger entries for {year} yet.</p>
			{:else}
				<ul class="ocp-ledger">
					{#each ledger as e (e.id)}
						<li class="ocp-card">
							<div class="ocp-ledger__head">
								<b>{formatMoney(e.amount)}</b>
								<span class="ocp-muted">{quarterLabel(e.period)} · {categoryLabel(e.category)}</span
								>
							</div>
							<p>{e.description}</p>
							<p class="ocp-muted"><b>Method:</b> {e.method_note}</p>
							<p class="ocp-muted"><b>Source:</b> {e.source}</p>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{/if}
</main>

<style>
	.ocp-page {
		padding: var(--lq-space-6);
		max-width: 1100px;
		margin: 0 auto;
	}
	.ocp-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}
	.ocp-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-3);
	}
	.ocp-header__actions {
		display: flex;
		gap: var(--lq-space-2);
		align-items: center;
		flex-wrap: wrap;
	}
	.ocp-sub {
		color: var(--lq-text-secondary);
		margin: var(--lq-space-1, 0.25rem) 0 var(--lq-space-3);
	}
	.ocp-policy {
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		margin: 0 0 var(--lq-space-4);
	}
	.ocp-year {
		display: inline-flex;
		align-items: center;
		gap: var(--lq-space-2);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: 0.15rem 0.4rem;
		font-weight: 600;
	}
	.ocp-year button {
		background: none;
		border: 0;
		cursor: pointer;
		font-size: 1.1rem;
		color: var(--lq-accent);
		padding: 0 0.3rem;
	}
	.ocp-btn,
	.ocp-btn-ghost {
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		line-height: 1.5;
		cursor: pointer;
		background: transparent;
		color: var(--lq-accent);
		border: 1px solid var(--lq-accent-border, var(--lq-accent));
	}
	.ocp-btn:hover,
	.ocp-btn-ghost:hover {
		background: var(--lq-accent-soft);
	}
	.ocp-btn:disabled,
	.ocp-btn-ghost:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.ocp-btn--primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
	}
	.ocp-btn--primary:hover {
		background: var(--lq-accent);
		filter: brightness(0.95);
	}
	.ocp-link {
		background: none;
		border: 0;
		color: var(--lq-accent);
		cursor: pointer;
		font-size: 0.85rem;
		padding: 0;
	}
	.ocp-link:hover {
		text-decoration: underline;
	}
	.ocp-link--danger {
		color: var(--lq-error);
	}
	.ocp-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-2) 0;
	}
	.ocp-msg--error {
		color: var(--lq-error);
	}
	.ocp-empty {
		color: var(--lq-text-tertiary);
		font-size: 0.9rem;
	}
	.ocp-muted {
		color: var(--lq-text-tertiary);
		font-size: 0.85rem;
	}
	.ocp-section {
		margin-bottom: var(--lq-space-6);
	}
	.ocp-section__head,
	.ocp-card__head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-3);
	}
	.ocp-h2 {
		font-size: 1rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0 0 var(--lq-space-3);
	}
	.ocp-section__head .ocp-h2,
	.ocp-card__head .ocp-h2 {
		margin: 0;
	}
	.ocp-h3 {
		font-size: 0.95rem;
		font-weight: 600;
		margin: 0 0 var(--lq-space-2);
	}
	.ocp-card {
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-4);
	}
	.ocp-story {
		margin-bottom: var(--lq-space-5, 1.25rem);
	}
	.ocp-alerts {
		margin-bottom: var(--lq-space-6);
		border-color: var(--lq-warn-border);
	}
	.ocp-alert-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: var(--lq-space-2);
	}
	.ocp-alert {
		border-left: 4px solid var(--lq-warn);
		padding: var(--lq-space-2) var(--lq-space-3);
		background: var(--lq-warn-soft);
		border-radius: var(--lq-radius);
	}
	.ocp-alert--red {
		border-left-color: var(--lq-error);
		background: var(--lq-error-soft);
	}
	.ocp-year-tile {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-4);
		margin-bottom: var(--lq-space-3);
	}
	.ocp-year-tile__label {
		color: var(--lq-text-secondary);
		margin: 0;
		font-size: 0.85rem;
	}
	.ocp-year-tile__hero {
		font-size: 1.8rem;
		font-weight: 600;
		margin: 0.1rem 0;
	}
	.ocp-year-tile__sub {
		color: var(--lq-text-secondary);
		margin: 0;
	}
	.ocp-table-wrap {
		overflow-x: auto;
		margin-bottom: var(--lq-space-3);
	}
	.ocp-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.9rem;
	}
	.ocp-table th {
		text-align: left;
		font-weight: 600;
		color: var(--lq-text-secondary);
		border-bottom: 1px solid var(--lq-border);
		padding: var(--lq-space-2);
	}
	.ocp-table td {
		border-bottom: 1px solid var(--lq-border);
		padding: var(--lq-space-2);
		vertical-align: middle;
	}
	.ocp-table--lines input,
	.ocp-table--lines select {
		width: 100%;
		min-width: 5rem;
	}
	.ocp-table--inner {
		background: var(--lq-inset);
		margin-bottom: var(--lq-space-2);
	}
	.ocp-row-click {
		cursor: pointer;
	}
	.ocp-row-click:hover {
		background: var(--lq-inset);
	}
	.ocp-flagged td {
		background: var(--lq-warn-soft);
	}
	.ocp-inline-form {
		display: flex;
		gap: var(--lq-space-2);
		align-items: center;
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-4);
	}
	.ocp-inline-form__label {
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
	}
	.ocp-form {
		display: grid;
		gap: var(--lq-space-3);
		margin-bottom: var(--lq-space-4);
	}
	.ocp-form label {
		display: grid;
		gap: 0.2rem;
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
	}
	.ocp-form__row {
		display: flex;
		gap: var(--lq-space-3);
		align-items: flex-end;
		flex-wrap: wrap;
	}
	input,
	select {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: 0.35rem 0.5rem;
		font: inherit;
		background: var(--lq-canvas);
		color: inherit;
	}
	.ocp-two-col {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
		gap: var(--lq-space-4);
	}
	.ocp-bar-row {
		display: grid;
		grid-template-columns: minmax(8rem, 1.2fr) 2fr auto;
		gap: var(--lq-space-2);
		align-items: center;
		font-size: 0.88rem;
		margin-bottom: var(--lq-space-2);
	}
	.ocp-bar {
		height: 0.55rem;
		background: var(--lq-inset);
		border-radius: 999px;
		overflow: hidden;
	}
	.ocp-bar span {
		display: block;
		height: 100%;
		background: var(--lq-accent);
		border-radius: 999px;
	}
	.ocp-bar-row__value {
		font-variant-numeric: tabular-nums;
	}
	.ocp-firm-grid {
		display: grid;
		gap: var(--lq-space-4);
		grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
	}
	.ocp-firm__chips {
		display: flex;
		gap: var(--lq-space-2);
		flex-wrap: wrap;
	}
	.ocp-firm__spend {
		color: var(--lq-text-secondary);
		font-size: 0.85rem;
		margin: var(--lq-space-2) 0;
	}
	.ocp-partners {
		list-style: none;
		padding: 0;
		margin: 0 0 var(--lq-space-3);
		display: grid;
		gap: var(--lq-space-2);
	}
	.ocp-partners li {
		display: flex;
		justify-content: space-between;
		gap: var(--lq-space-2);
		flex-wrap: wrap;
		font-size: 0.9rem;
	}
	.ocp-partner--left {
		background: var(--lq-error-soft);
		border-radius: var(--lq-radius);
		padding: 0.25rem 0.4rem;
	}
	.ocp-partner__actions {
		display: inline-flex;
		gap: var(--lq-space-2);
	}
	.ocp-cat-grid {
		display: grid;
		gap: var(--lq-space-3);
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		margin-bottom: var(--lq-space-4);
	}
	.ocp-cat__label {
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
		margin: 0;
	}
	.ocp-cat__value {
		font-size: 1.3rem;
		font-weight: 600;
		margin: 0.2rem 0;
	}
	.ocp-ledger {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: var(--lq-space-3);
	}
	.ocp-ledger p {
		margin: 0.2rem 0;
	}
	.ocp-ledger__head {
		display: flex;
		gap: var(--lq-space-3);
		align-items: baseline;
		flex-wrap: wrap;
	}
	.ocp-chip {
		display: inline-block;
		font-size: 0.75rem;
		font-weight: 600;
		padding: 0.15rem 0.55rem;
		border-radius: 999px;
		border: 1px solid transparent;
		white-space: nowrap;
	}
	.ocp-chip--sm {
		font-size: 0.68rem;
		padding: 0.1rem 0.45rem;
	}
	.ocp-chip--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}
	.ocp-chip--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}
	.ocp-chip--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}
	.ocp-chip--muted {
		background: var(--lq-inset);
		color: var(--lq-text-tertiary);
		border-color: var(--lq-border);
	}
</style>
