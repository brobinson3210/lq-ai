<!--
  /lq-ai/management/kpis/wizard — the KPI interview wizard.

  A data-free interview script (GET /management/kpi-wizard/questions, four
  sections A-D), answers submitted as a kpi_draft AI job, then a
  line-by-line review step: each drafted KPI is a card with an inline edit
  form (NewKpiModal validation) and Add-to-catalog / Skip actions, plus the
  "what we chose NOT to measure" exclusions — part of the work product.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { managementAiApi, managementKpisApi } from '$lib/lq-ai/api';
	import type {
		KpiCadence,
		KpiDepartment,
		KpiDirection,
		MgmtAiJob,
		MgmtKpiDraftNotMeasured,
		MgmtKpiDraftResult,
		MgmtKpiWizardAnswer,
		MgmtKpiWizardQuestion
	} from '$lib/lq-ai/types';
	import {
		CADENCE_OPTIONS,
		DEPARTMENT_OPTIONS,
		DIRECTION_OPTIONS,
		departmentLabel
	} from '$lib/lq-ai/management/kpis';
	import MgmtAiJobRunner from '$lib/lq-ai/components/MgmtAiJobRunner.svelte';
	import { validateNewKpi } from '$lib/lq-ai/components/NewKpiModal.svelte';

	// ----- Interview script -----

	const SECTION_TITLES: Record<string, string> = {
		A: 'A · Objective',
		B: 'B · Metric families',
		C: 'C · Vanity check',
		D: 'D · Baselines & targets'
	};

	let questions: MgmtKpiWizardQuestion[] = [];
	let loading = true;
	let loadError: string | null = null;

	onMount(async () => {
		try {
			const res = await managementAiApi.getWizardQuestions();
			questions = res.questions;
			loadError = null;
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Failed to load the interview questions';
		} finally {
			loading = false;
		}
	});

	/** Sections in first-appearance order, questions kept in served order. */
	$: sections = questions.reduce<{ section: string; questions: MgmtKpiWizardQuestion[] }[]>(
		(acc, q) => {
			const bucket = acc.find((s) => s.section === q.section);
			if (bucket) bucket.questions.push(q);
			else acc.push({ section: q.section, questions: [q] });
			return acc;
		},
		[]
	);

	// ----- Answers + submit -----

	let step: 'interview' | 'drafting' | 'review' = 'interview';
	let answers: Record<string, string> = {};
	let answerErrors: Record<string, string> = {};
	let submitError: string | null = null;
	let creatingJob = false;
	let jobId: string | null = null;
	let lastSubmittedAnswers: MgmtKpiWizardAnswer[] = [];

	function collectAnswers(): MgmtKpiWizardAnswer[] | null {
		const errors: Record<string, string> = {};
		const out: MgmtKpiWizardAnswer[] = [];
		for (const q of questions) {
			const raw = (answers[q.id] ?? '').trim();
			if (!raw) {
				if (!q.optional) errors[q.id] = 'This question needs an answer before the draft.';
				continue;
			}
			out.push({ question_id: q.id, answer: raw });
		}
		answerErrors = errors;
		return Object.keys(errors).length > 0 ? null : out;
	}

	async function submitInterview() {
		submitError = null;
		const collected = collectAnswers();
		if (!collected) {
			submitError = 'A few required questions still need answers — they are marked below.';
			return;
		}
		lastSubmittedAnswers = collected;
		await createDraftJob(collected);
	}

	async function createDraftJob(collected: MgmtKpiWizardAnswer[]) {
		creatingJob = true;
		submitError = null;
		try {
			const job = await managementAiApi.createAiJob({ job_type: 'kpi_draft', answers: collected });
			jobId = job.id;
			step = 'drafting';
		} catch (e) {
			submitError = e instanceof Error ? e.message : 'Failed to start the draft';
			step = 'interview';
		} finally {
			creatingJob = false;
		}
	}

	function retryDraft() {
		if (lastSubmittedAnswers.length > 0) void createDraftJob(lastSubmittedAnswers);
	}

	// ----- Review step -----

	interface DraftCard {
		key: number;
		name: string;
		department: KpiDepartment;
		unit: string;
		cadence: KpiCadence;
		direction: KpiDirection;
		baseline: string;
		target: string;
		rationale: string;
		editing: boolean;
		state: 'pending' | 'adding' | 'added' | 'skipped';
		error: string | null;
	}

	let cards: DraftCard[] = [];
	let notMeasured: MgmtKpiDraftNotMeasured[] = [];
	let addingAll = false;

	function onDraftDone(job: MgmtAiJob) {
		const result = job.result_json as unknown as MgmtKpiDraftResult | null;
		cards = (result?.kpis ?? []).map((k, i) => ({
			key: i,
			name: k.name,
			department: k.department,
			unit: k.unit,
			cadence: k.cadence,
			direction: k.direction,
			baseline: k.baseline ?? '',
			target: k.target ?? '',
			rationale: k.rationale_md,
			editing: false,
			state: 'pending',
			error: null
		}));
		notMeasured = result?.not_measured ?? [];
		step = 'review';
	}

	function updateCard(key: number, patch: Partial<DraftCard>) {
		cards = cards.map((c) => (c.key === key ? { ...c, ...patch } : c));
	}

	async function addCard(card: DraftCard): Promise<boolean> {
		const validation = validateNewKpi({
			name: card.name,
			department: card.department,
			scope: 'department',
			team_member_id: '',
			unit: card.unit,
			baseline: card.baseline,
			target: card.target
		});
		if (!validation.valid) {
			const firstError =
				validation.nameError ??
				validation.departmentError ??
				validation.unitError ??
				validation.baselineError ??
				validation.targetError ??
				'Fix the highlighted fields.';
			updateCard(card.key, { error: firstError, editing: true });
			return false;
		}
		updateCard(card.key, { state: 'adding', error: null });
		try {
			await managementKpisApi.createKpi({
				name: card.name.trim(),
				department: card.department,
				scope: 'department',
				unit: card.unit.trim(),
				cadence: card.cadence,
				direction: card.direction,
				baseline: card.baseline.trim() || undefined,
				target: card.target.trim() || undefined,
				rationale_md: card.rationale.trim() || undefined
			});
			updateCard(card.key, { state: 'added', editing: false });
			return true;
		} catch (e) {
			updateCard(card.key, {
				state: 'pending',
				error: e instanceof Error ? e.message : 'Failed to add the KPI'
			});
			return false;
		}
	}

	async function addAllRemaining() {
		addingAll = true;
		for (const card of cards.filter((c) => c.state === 'pending')) {
			// Sequential on purpose: keeps per-card errors attributable.
			await addCard(card);
		}
		addingAll = false;
	}

	$: addedCount = cards.filter((c) => c.state === 'added').length;
	$: pendingCount = cards.filter((c) => c.state === 'pending').length;
</script>

<main class="kwz-page" data-testid="lq-ai-mgmt-wizard-page">
	<a class="kwz-back" href="/lq-ai/management/kpis">← KPIs &amp; OKRs</a>

	<header class="kwz-header">
		<h1 class="lq-text-page-h">KPI interview wizard ✦</h1>
	</header>

	{#if step === 'interview'}
		<section class="kwz-intro" data-testid="lq-ai-mgmt-wizard-intro">
			<p class="lq-text-body kwz-intro__copy">
				A short structured interview — the wizard asks what a seasoned GC would ask, then drafts a
				KPI catalog you confirm line by line. The script is data-free: your answers travel only to
				this instance's configured model, and nothing lands in the catalog until you approve it.
			</p>
		</section>

		{#if loading}
			<p class="lq-text-body kwz-state-msg">Loading the interview…</p>
		{:else if loadError}
			<p class="lq-text-body kwz-state-msg kwz-state-msg--error" role="alert">
				Couldn't load the interview questions: {loadError}
			</p>
		{:else}
			<form
				class="kwz-form"
				data-testid="lq-ai-mgmt-wizard-form"
				on:submit|preventDefault={submitInterview}
			>
				{#each sections as sec (sec.section)}
					<section class="kwz-section" aria-labelledby={`kwz-sec-${sec.section}-h`}>
						<h2 id={`kwz-sec-${sec.section}-h`} class="kwz-section__title">
							{SECTION_TITLES[sec.section] ?? `Section ${sec.section}`}
						</h2>
						{#each sec.questions as q (q.id)}
							<div class="kwz-question">
								<label class="kwz-label" for={`kwz-q-${q.id}`}>
									{q.prompt}
									{#if q.optional}<span class="kwz-optional">optional</span>{/if}
								</label>
								<p class="kwz-hint">{q.hint}</p>
								<textarea
									id={`kwz-q-${q.id}`}
									class="kwz-textarea"
									class:kwz-textarea--error={!!answerErrors[q.id]}
									rows="3"
									bind:value={answers[q.id]}
									disabled={creatingJob}
									data-testid={`lq-ai-mgmt-wizard-q-${q.id}`}
									aria-describedby={answerErrors[q.id] ? `kwz-q-${q.id}-error` : undefined}
								></textarea>
								{#if answerErrors[q.id]}
									<p id={`kwz-q-${q.id}-error`} class="kwz-field-error" role="alert">
										{answerErrors[q.id]}
									</p>
								{/if}
							</div>
						{/each}
					</section>
				{/each}

				{#if submitError}
					<p class="kwz-submit-error" role="alert">{submitError}</p>
				{/if}

				<div class="kwz-submit-bar">
					<button
						type="submit"
						class="kwz-btn-primary"
						disabled={creatingJob || questions.length === 0}
						data-testid="lq-ai-mgmt-wizard-submit"
					>
						{creatingJob ? 'Starting the draft…' : 'Draft my KPI catalog ✦'}
					</button>
				</div>
			</form>
		{/if}
	{:else if step === 'drafting' && jobId}
		<section class="kwz-drafting" data-testid="lq-ai-mgmt-wizard-drafting" aria-label="Drafting">
			<MgmtAiJobRunner
				{jobId}
				runningCopy="Drafting your KPI catalog…"
				onDone={onDraftDone}
				onRetry={retryDraft}
			/>
		</section>
	{:else if step === 'review'}
		<section class="kwz-review" data-testid="lq-ai-mgmt-wizard-review" aria-label="Draft review">
			<p class="lq-text-body kwz-review__lede">
				The draft, line by line. Edit anything, then add each KPI to the catalog — or skip it.
				Nothing is saved until you say so.
			</p>

			<div class="kwz-cards">
				{#each cards as card (card.key)}
					<article
						class="kwz-card"
						class:kwz-card--added={card.state === 'added'}
						class:kwz-card--skipped={card.state === 'skipped'}
						aria-label={`Drafted KPI: ${card.name}`}
						data-testid={`lq-ai-mgmt-wizard-card-${card.key}`}
					>
						{#if card.editing}
							<div class="kwz-edit-grid">
								<div class="kwz-field kwz-field--wide">
									<label class="kwz-label" for={`kwz-name-${card.key}`}>Name</label>
									<input
										id={`kwz-name-${card.key}`}
										class="kwz-input"
										type="text"
										maxlength="200"
										value={card.name}
										on:input={(e) => updateCard(card.key, { name: e.currentTarget.value })}
									/>
								</div>
								<div class="kwz-field">
									<label class="kwz-label" for={`kwz-dept-${card.key}`}>Department</label>
									<select
										id={`kwz-dept-${card.key}`}
										class="kwz-input"
										value={card.department}
										on:change={(e) =>
											updateCard(card.key, {
												department: e.currentTarget.value as KpiDepartment
											})}
									>
										{#each DEPARTMENT_OPTIONS as opt (opt.value)}
											<option value={opt.value}>{opt.label}</option>
										{/each}
									</select>
								</div>
								<div class="kwz-field">
									<label class="kwz-label" for={`kwz-unit-${card.key}`}>Unit</label>
									<input
										id={`kwz-unit-${card.key}`}
										class="kwz-input"
										type="text"
										maxlength="32"
										value={card.unit}
										on:input={(e) => updateCard(card.key, { unit: e.currentTarget.value })}
									/>
								</div>
								<div class="kwz-field">
									<label class="kwz-label" for={`kwz-cadence-${card.key}`}>Cadence</label>
									<select
										id={`kwz-cadence-${card.key}`}
										class="kwz-input"
										value={card.cadence}
										on:change={(e) =>
											updateCard(card.key, { cadence: e.currentTarget.value as KpiCadence })}
									>
										{#each CADENCE_OPTIONS as opt (opt.value)}
											<option value={opt.value}>{opt.label}</option>
										{/each}
									</select>
								</div>
								<div class="kwz-field">
									<label class="kwz-label" for={`kwz-direction-${card.key}`}>Direction</label>
									<select
										id={`kwz-direction-${card.key}`}
										class="kwz-input"
										value={card.direction}
										on:change={(e) =>
											updateCard(card.key, {
												direction: e.currentTarget.value as KpiDirection
											})}
									>
										{#each DIRECTION_OPTIONS as opt (opt.value)}
											<option value={opt.value}>{opt.label}</option>
										{/each}
									</select>
								</div>
								<div class="kwz-field">
									<label class="kwz-label" for={`kwz-baseline-${card.key}`}>Baseline</label>
									<input
										id={`kwz-baseline-${card.key}`}
										class="kwz-input"
										type="text"
										inputmode="decimal"
										value={card.baseline}
										on:input={(e) => updateCard(card.key, { baseline: e.currentTarget.value })}
									/>
								</div>
								<div class="kwz-field">
									<label class="kwz-label" for={`kwz-target-${card.key}`}>Target</label>
									<input
										id={`kwz-target-${card.key}`}
										class="kwz-input"
										type="text"
										inputmode="decimal"
										value={card.target}
										on:input={(e) => updateCard(card.key, { target: e.currentTarget.value })}
									/>
								</div>
								<div class="kwz-field kwz-field--wide">
									<label class="kwz-label" for={`kwz-rationale-${card.key}`}>
										Why this number predicts success
									</label>
									<textarea
										id={`kwz-rationale-${card.key}`}
										class="kwz-textarea"
										rows="3"
										value={card.rationale}
										on:input={(e) => updateCard(card.key, { rationale: e.currentTarget.value })}
									></textarea>
								</div>
							</div>
							<button
								type="button"
								class="kwz-btn-mini"
								on:click={() => updateCard(card.key, { editing: false })}
							>
								Done editing
							</button>
						{:else}
							<div class="kwz-card__head">
								<h3 class="kwz-card__name">{card.name}</h3>
								<span class="kwz-chip kwz-chip--muted">{departmentLabel(card.department)}</span>
							</div>
							<p class="kwz-card__meta">
								{card.unit} · {card.cadence} · {card.direction === 'higher_is_better'
									? 'higher is better'
									: 'lower is better'}
							</p>
							{#if card.baseline || card.target}
								<p class="kwz-card__targets">
									{#if card.baseline}<span>baseline {card.baseline}</span>{/if}
									{#if card.baseline && card.target}<span aria-hidden="true"> → </span>{/if}
									{#if card.target}<span>target {card.target}</span>{/if}
								</p>
							{/if}
							<p class="kwz-card__rationale">{card.rationale}</p>
						{/if}

						{#if card.error}
							<p class="kwz-field-error" role="alert">{card.error}</p>
						{/if}

						<div class="kwz-card__actions">
							{#if card.state === 'added'}
								<span class="kwz-added" data-testid={`lq-ai-mgmt-wizard-added-${card.key}`}>
									Added ✓
								</span>
							{:else if card.state === 'skipped'}
								<span class="kwz-skipped">Skipped</span>
								<button
									type="button"
									class="kwz-btn-mini"
									on:click={() => updateCard(card.key, { state: 'pending' })}
								>
									Undo
								</button>
							{:else}
								{#if !card.editing}
									<button
										type="button"
										class="kwz-btn-mini"
										data-testid={`lq-ai-mgmt-wizard-edit-${card.key}`}
										on:click={() => updateCard(card.key, { editing: true })}
									>
										Edit
									</button>
								{/if}
								<button
									type="button"
									class="kwz-btn-primary kwz-btn-primary--sm"
									disabled={card.state === 'adding' || addingAll}
									data-testid={`lq-ai-mgmt-wizard-add-${card.key}`}
									on:click={() => addCard(card)}
								>
									{card.state === 'adding' ? 'Adding…' : 'Add to catalog'}
								</button>
								<button
									type="button"
									class="kwz-btn-secondary"
									disabled={card.state === 'adding' || addingAll}
									data-testid={`lq-ai-mgmt-wizard-skip-${card.key}`}
									on:click={() => updateCard(card.key, { state: 'skipped' })}
								>
									Skip
								</button>
							{/if}
						</div>
					</article>
				{/each}
			</div>

			{#if notMeasured.length > 0}
				<section
					class="kwz-notmeasured"
					aria-labelledby="kwz-notmeasured-h"
					data-testid="lq-ai-mgmt-wizard-notmeasured"
				>
					<h2 id="kwz-notmeasured-h" class="kwz-section__title">What we chose NOT to measure</h2>
					<p class="kwz-notmeasured__lede">
						Deliberate exclusions — part of the work product. A short catalog is a defensible one.
					</p>
					<ul class="kwz-notmeasured__list">
						{#each notMeasured as item (item.name)}
							<li class="kwz-notmeasured__row">
								<span class="kwz-notmeasured__name">{item.name}</span>
								<span class="kwz-notmeasured__reason">{item.reason}</span>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<div class="kwz-finish-bar" data-testid="lq-ai-mgmt-wizard-finish">
				<span class="kwz-finish-bar__count">
					{addedCount} of {cards.length} added to the catalog
				</span>
				{#if pendingCount > 0}
					<button
						type="button"
						class="kwz-btn-secondary"
						disabled={addingAll}
						data-testid="lq-ai-mgmt-wizard-add-all"
						on:click={addAllRemaining}
					>
						{addingAll ? 'Adding…' : `Add all remaining (${pendingCount})`}
					</button>
				{/if}
				<a class="kwz-btn-primary kwz-finish-link" href="/lq-ai/management/kpis">
					Back to the dashboard →
				</a>
			</div>
		</section>
	{/if}
</main>

<style>
	.kwz-page {
		padding: var(--lq-space-6);
		max-width: 860px;
		margin: 0 auto;
	}

	.kwz-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.kwz-header {
		margin-bottom: var(--lq-space-4);
	}

	.kwz-intro {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-4);
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.kwz-intro__copy {
		color: var(--lq-text-secondary);
		margin: 0;
		line-height: 1.6;
	}

	.kwz-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.kwz-state-msg--error {
		color: var(--lq-error);
	}

	.kwz-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-5, 1.25rem);
	}

	.kwz-section {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-4);
	}

	.kwz-section__title {
		font-size: 1rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0 0 var(--lq-space-3);
	}

	.kwz-question {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
		margin-bottom: var(--lq-space-4);
	}

	.kwz-question:last-child {
		margin-bottom: 0;
	}

	.kwz-label {
		font-size: 14px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.kwz-optional {
		font-weight: 400;
		font-size: 12px;
		color: var(--lq-text-tertiary);
		margin-left: var(--lq-space-1);
	}

	.kwz-hint {
		font-size: 12px;
		color: var(--lq-text-tertiary);
		margin: 0;
	}

	.kwz-input,
	.kwz-textarea {
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		font-size: 14px;
		color: var(--lq-text-primary);
		box-sizing: border-box;
		width: 100%;
		transition: border-color 0.15s ease;
	}

	.kwz-input:focus,
	.kwz-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.kwz-textarea {
		resize: vertical;
	}

	.kwz-textarea--error {
		border-color: var(--lq-error);
	}

	.kwz-field-error {
		font-size: 12px;
		color: var(--lq-error);
		margin: 0;
	}

	.kwz-submit-error {
		font-size: 13px;
		color: var(--lq-error);
		background: var(--lq-error-soft);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		margin: 0;
	}

	.kwz-submit-bar {
		display: flex;
		justify-content: flex-end;
	}

	.kwz-drafting {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-4);
	}

	.kwz-review__lede {
		color: var(--lq-text-secondary);
		margin: 0 0 var(--lq-space-4);
	}

	.kwz-cards {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-4);
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.kwz-card {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-4);
	}

	.kwz-card--added {
		border-color: var(--lq-accent-border);
		background: var(--lq-accent-soft);
	}

	.kwz-card--skipped {
		opacity: 0.55;
	}

	.kwz-card__head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-2);
	}

	.kwz-card__name {
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0;
	}

	.kwz-card__meta {
		font-size: 0.8rem;
		color: var(--lq-text-tertiary);
		margin: 0;
	}

	.kwz-card__targets {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0;
	}

	.kwz-card__rationale {
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
		line-height: 1.55;
		margin: 0;
	}

	.kwz-chip {
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: var(--lq-radius-pill);
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
		border: 1px solid transparent;
	}

	.kwz-chip--muted {
		background: var(--lq-inset);
		color: var(--lq-text-secondary);
		border-color: var(--lq-border);
	}

	.kwz-edit-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: var(--lq-space-3);
	}

	.kwz-field {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.kwz-field--wide {
		grid-column: 1 / -1;
	}

	.kwz-card__actions {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		margin-top: var(--lq-space-1);
	}

	.kwz-added {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--lq-accent);
	}

	.kwz-skipped {
		font-size: 0.85rem;
		color: var(--lq-text-tertiary);
	}

	.kwz-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
		text-decoration: none;
	}

	.kwz-btn-primary--sm {
		padding: var(--lq-space-1) var(--lq-space-3);
		font-size: 13px;
	}

	.kwz-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.kwz-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kwz-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.kwz-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.kwz-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.kwz-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kwz-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.kwz-btn-mini {
		align-self: flex-start;
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-sm);
		padding: 2px var(--lq-space-2);
		font-size: 12px;
		font-weight: 500;
		cursor: pointer;
	}

	.kwz-btn-mini:hover {
		border-color: var(--lq-accent);
		color: var(--lq-accent);
	}

	.kwz-btn-mini:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.kwz-notmeasured {
		border: 1px dashed var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-4);
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.kwz-notmeasured__lede {
		font-size: 0.85rem;
		color: var(--lq-text-tertiary);
		margin: 0 0 var(--lq-space-3);
	}

	.kwz-notmeasured__list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
	}

	.kwz-notmeasured__row {
		display: flex;
		gap: var(--lq-space-3);
		align-items: baseline;
		flex-wrap: wrap;
	}

	.kwz-notmeasured__name {
		font-weight: 600;
		font-size: 0.85rem;
		color: var(--lq-text-primary);
		text-decoration: line-through;
		text-decoration-color: var(--lq-text-tertiary);
	}

	.kwz-notmeasured__reason {
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
	}

	.kwz-finish-bar {
		display: flex;
		align-items: center;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		border-top: 1px solid var(--lq-border);
		padding-top: var(--lq-space-4);
	}

	.kwz-finish-bar__count {
		flex: 1;
		font-size: 0.9rem;
		color: var(--lq-text-secondary);
	}

	.kwz-finish-link {
		display: inline-block;
	}
</style>
