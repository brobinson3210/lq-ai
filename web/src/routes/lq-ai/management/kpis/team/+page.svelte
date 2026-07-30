<!--
  /lq-ai/management/kpis/team — the team roster behind individual KPIs.

  Member cards (name, role, department chip, seniority, kpi_count) with an
  expandable detail (strengths / development areas / notes + inline edit),
  each member's individual KPIs linked to their detail pages, and the
  review-prep AI feature: per-member 1-on-1 briefs assembled from that
  person's actual numbers, with a past-preps list. Comp-adjacent — private
  to the caller.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { managementAiApi, managementKpisApi } from '$lib/lq-ai/api';
	import type { KpiDepartment, KpiRead, MgmtAiJobListRow, TeamMemberRead } from '$lib/lq-ai/types';
	import { formatDateTime } from '$lib/lq-ai/management/stakeholders';
	import { renderBriefMarkdown } from '$lib/lq-ai/management/markdown';
	import MgmtAiJobRunner from '$lib/lq-ai/components/MgmtAiJobRunner.svelte';
	import {
		DEPARTMENT_OPTIONS,
		attainmentLabel,
		attainmentTone,
		departmentLabel,
		formatKpiValue,
		performanceRating,
		unitSuffix
	} from '$lib/lq-ai/management/kpis';
	import NewTeamMemberModal from '$lib/lq-ai/components/NewTeamMemberModal.svelte';

	let members: TeamMemberRead[] = [];
	let kpisByMember: Record<string, KpiRead[]> = {};
	let loading = true;
	let error: string | null = null;
	let showNewModal = false;

	async function refresh() {
		loading = true;
		try {
			const [memberRows, individualKpis] = await Promise.all([
				managementKpisApi.listTeamMembers(),
				managementKpisApi.listKpis({ scope: 'individual' })
			]);
			members = memberRows;
			const map: Record<string, KpiRead[]> = {};
			for (const kpi of individualKpis) {
				if (!kpi.team_member_id) continue;
				(map[kpi.team_member_id] ??= []).push(kpi);
			}
			kpisByMember = map;
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load the team roster';
		} finally {
			loading = false;
		}
	}

	onMount(refresh);

	// ----- Expand / collapse -----

	let expanded: Record<string, boolean> = {};

	function toggleExpanded(id: string) {
		expanded = { ...expanded, [id]: !expanded[id] };
	}

	// ----- Inline edit -----

	let editingId: string | null = null;
	let editError: string | null = null;
	let savingEdit = false;
	let draftName = '';
	let draftRole = '';
	let draftDepartment: KpiDepartment = 'legal';
	let draftSeniority = '';
	let draftStrengths = '';
	let draftDevelopment = '';
	let draftNotes = '';

	function startEdit(m: TeamMemberRead) {
		editingId = m.id;
		draftName = m.name;
		draftRole = m.role_title;
		draftDepartment = m.department;
		draftSeniority = m.seniority ?? '';
		draftStrengths = m.strengths_md ?? '';
		draftDevelopment = m.development_areas_md ?? '';
		draftNotes = m.notes_md ?? '';
		editError = null;
		expanded = { ...expanded, [m.id]: true };
	}

	async function saveEdit() {
		if (!editingId) return;
		if (!draftName.trim()) {
			editError = 'A name is required.';
			return;
		}
		if (!draftRole.trim()) {
			editError = 'A role / title is required.';
			return;
		}
		savingEdit = true;
		editError = null;
		try {
			const updated = await managementKpisApi.patchTeamMember(editingId, {
				name: draftName.trim(),
				role_title: draftRole.trim(),
				department: draftDepartment,
				seniority: draftSeniority.trim() || null,
				strengths_md: draftStrengths.trim() || null,
				development_areas_md: draftDevelopment.trim() || null,
				notes_md: draftNotes.trim() || null
			});
			members = members.map((m) => (m.id === updated.id ? updated : m));
			editingId = null;
		} catch (e) {
			editError = e instanceof Error ? e.message : 'Failed to save the member';
		} finally {
			savingEdit = false;
		}
	}

	// ----- Review prep (AI) -----

	let reviewJobIds: Record<string, string> = {};
	let creatingReviewId: string | null = null;
	let reviewCreateErrors: Record<string, string | null> = {};

	async function prepareReview(memberId: string) {
		creatingReviewId = memberId;
		reviewCreateErrors = { ...reviewCreateErrors, [memberId]: null };
		try {
			const job = await managementAiApi.createAiJob({
				job_type: 'review_prep',
				team_member_id: memberId
			});
			reviewJobIds = { ...reviewJobIds, [memberId]: job.id };
		} catch (e) {
			reviewCreateErrors = {
				...reviewCreateErrors,
				[memberId]: e instanceof Error ? e.message : 'Failed to start the review prep'
			};
		} finally {
			creatingReviewId = null;
		}
	}

	function onReviewDone(memberId: string) {
		// A finished prep belongs in the past-preps list on next open.
		pastPreps = { ...pastPreps, [memberId]: null };
	}

	// Past preps — lazy-loaded per member, newest first.
	let pastPrepsOpen: Record<string, boolean> = {};
	let pastPreps: Record<string, MgmtAiJobListRow[] | null> = {};
	let pastPrepsLoading: Record<string, boolean> = {};
	let pastPrepsErrors: Record<string, string | null> = {};
	let openPastPrepIds: Record<string, string | null> = {};

	async function togglePastPreps(memberId: string) {
		const open = !pastPrepsOpen[memberId];
		pastPrepsOpen = { ...pastPrepsOpen, [memberId]: open };
		if (open && !pastPreps[memberId]) {
			pastPrepsLoading = { ...pastPrepsLoading, [memberId]: true };
			pastPrepsErrors = { ...pastPrepsErrors, [memberId]: null };
			try {
				const rows = await managementAiApi.listAiJobs({
					jobType: 'review_prep',
					teamMemberId: memberId
				});
				pastPreps = {
					...pastPreps,
					[memberId]: rows.filter((r) => r.status === 'done' && r.result_md)
				};
			} catch (e) {
				pastPrepsErrors = {
					...pastPrepsErrors,
					[memberId]: e instanceof Error ? e.message : 'Failed to load past preps'
				};
			} finally {
				pastPrepsLoading = { ...pastPrepsLoading, [memberId]: false };
			}
		}
	}

	// ----- Soft delete (two-step confirm) -----

	let confirmingDeleteId: string | null = null;
	let deleteError: string | null = null;
	let deleting = false;

	async function confirmDelete(id: string) {
		deleting = true;
		deleteError = null;
		try {
			await managementKpisApi.deleteTeamMember(id);
			confirmingDeleteId = null;
			await refresh();
		} catch (e) {
			deleteError = e instanceof Error ? e.message : 'Failed to remove the member';
		} finally {
			deleting = false;
		}
	}
</script>

<main class="tmr-page" data-testid="lq-ai-mgmt-team-page">
	<a class="tmr-back" href="/lq-ai/management/kpis">← KPIs &amp; OKRs</a>

	<header class="tmr-header">
		<div>
			<h1 class="lq-text-page-h">Team roster</h1>
			<p class="tmr-sub lq-text-body">
				The people behind the individual KPIs — strengths, development areas, and each person's
				numbers.
			</p>
		</div>
		<button
			type="button"
			class="tmr-btn-primary"
			data-testid="lq-ai-mgmt-team-new-btn"
			on:click={() => (showNewModal = true)}
		>
			+ Add member
		</button>
	</header>

	{#if deleteError}
		<p class="tmr-inline-error" role="alert">{deleteError}</p>
	{/if}

	{#if loading}
		<p class="lq-text-body tmr-state-msg">Loading the roster…</p>
	{:else if error}
		<p class="lq-text-body tmr-state-msg tmr-state-msg--error" role="alert">
			Couldn't load the roster: {error}
		</p>
	{:else if members.length === 0}
		<section class="tmr-empty" data-testid="lq-ai-mgmt-team-empty">
			<p class="lq-text-body tmr-empty__copy">
				No team members yet. Individual KPIs need people — add the first member.
			</p>
			<button type="button" class="tmr-btn-primary" on:click={() => (showNewModal = true)}>
				+ Add member
			</button>
		</section>
	{:else}
		<div class="tmr-grid" data-testid="lq-ai-mgmt-team-list">
			{#each members as m (m.id)}
				{@const memberKpis = kpisByMember[m.id] ?? []}
				{@const rating = performanceRating(memberKpis)}
				<article
					class="tmr-card"
					aria-label={`Team member: ${m.name}`}
					data-testid={`lq-ai-mgmt-team-card-${m.id}`}
				>
					<div class="tmr-card__top">
						<div>
							<h3 class="tmr-card__name">{m.name}</h3>
							<p class="tmr-card__role">
								{m.role_title}{#if m.seniority}
									<span class="tmr-card__seniority">· {m.seniority}</span>{/if}
							</p>
						</div>
						<span class="tmr-badges">
							<span class={`tmr-dept tmr-dept--${m.department}`}
								>{departmentLabel(m.department)}</span
							>
							{#if rating}
								<span
									class={`tmr-dept tmr-perf tmr-perf--${rating.tone}`}
									data-testid={`lq-ai-mgmt-team-perf-${m.id}`}
									title="Average attainment across this member's KPIs"
								>
									{rating.label}
								</span>
							{/if}
						</span>
					</div>

					<p class="tmr-card__count">
						{m.kpi_count}
						{m.kpi_count === 1 ? 'KPI' : 'KPIs'}
					</p>

					{#if memberKpis.length > 0}
						<ul class="tmr-kpis">
							{#each memberKpis as kpi (kpi.id)}
								<li>
									<a class="tmr-kpi-row" href={`/lq-ai/management/kpis/${kpi.id}`}>
										<span class="tmr-kpi-row__name">{kpi.name}</span>
										<span class="tmr-kpi-row__value">
											{formatKpiValue(kpi.latest_value, kpi.unit)}
											{#if unitSuffix(kpi.unit) && kpi.latest_value !== null}
												<span class="tmr-kpi-row__unit">{unitSuffix(kpi.unit)}</span>
											{/if}
										</span>
										{#if attainmentLabel(kpi.attainment_pct)}
											<span class={`tmr-chip tmr-chip--${attainmentTone(kpi.attainment_pct)}`}>
												{attainmentLabel(kpi.attainment_pct)}
											</span>
										{/if}
									</a>
								</li>
							{/each}
						</ul>
					{/if}

					<button
						type="button"
						class="tmr-expand"
						aria-expanded={!!expanded[m.id]}
						data-testid={`lq-ai-mgmt-team-expand-${m.id}`}
						on:click={() => toggleExpanded(m.id)}
					>
						{expanded[m.id] ? 'Hide detail' : 'Show detail'}
					</button>

					{#if expanded[m.id]}
						{#if editingId === m.id}
							<form class="tmr-edit-form" on:submit|preventDefault={saveEdit}>
								<div class="tmr-form-grid">
									<div class="tmr-form-row">
										<label class="tmr-label" for={`tmr-name-${m.id}`}>Name</label>
										<input
											id={`tmr-name-${m.id}`}
											class="tmr-input"
											type="text"
											maxlength="200"
											bind:value={draftName}
										/>
									</div>
									<div class="tmr-form-row">
										<label class="tmr-label" for={`tmr-role-${m.id}`}>Role / title</label>
										<input
											id={`tmr-role-${m.id}`}
											class="tmr-input"
											type="text"
											maxlength="200"
											bind:value={draftRole}
										/>
									</div>
									<div class="tmr-form-row">
										<label class="tmr-label" for={`tmr-dept-${m.id}`}>Department</label>
										<select id={`tmr-dept-${m.id}`} class="tmr-input" bind:value={draftDepartment}>
											{#each DEPARTMENT_OPTIONS as opt (opt.value)}
												<option value={opt.value}>{opt.label}</option>
											{/each}
										</select>
									</div>
									<div class="tmr-form-row">
										<label class="tmr-label" for={`tmr-seniority-${m.id}`}>Seniority</label>
										<input
											id={`tmr-seniority-${m.id}`}
											class="tmr-input"
											type="text"
											maxlength="100"
											bind:value={draftSeniority}
										/>
									</div>
								</div>
								<div class="tmr-form-row">
									<label class="tmr-label" for={`tmr-strengths-${m.id}`}>Strengths</label>
									<textarea
										id={`tmr-strengths-${m.id}`}
										class="tmr-textarea"
										rows="2"
										bind:value={draftStrengths}
									></textarea>
								</div>
								<div class="tmr-form-row">
									<label class="tmr-label" for={`tmr-development-${m.id}`}>Development areas</label>
									<textarea
										id={`tmr-development-${m.id}`}
										class="tmr-textarea"
										rows="2"
										bind:value={draftDevelopment}
									></textarea>
								</div>
								<div class="tmr-form-row">
									<label class="tmr-label" for={`tmr-notes-${m.id}`}>Notes</label>
									<textarea
										id={`tmr-notes-${m.id}`}
										class="tmr-textarea"
										rows="2"
										bind:value={draftNotes}
									></textarea>
								</div>
								{#if editError}
									<p class="tmr-inline-error" role="alert">{editError}</p>
								{/if}
								<div class="tmr-form-actions">
									<button
										type="button"
										class="tmr-btn-secondary"
										disabled={savingEdit}
										on:click={() => (editingId = null)}
									>
										Cancel
									</button>
									<button
										type="submit"
										class="tmr-btn-primary"
										disabled={savingEdit}
										data-testid={`lq-ai-mgmt-team-save-${m.id}`}
									>
										{savingEdit ? 'Saving…' : 'Save'}
									</button>
								</div>
							</form>
						{:else}
							<div class="tmr-detail">
								<h4 class="tmr-subhead">Strengths</h4>
								<p class="tmr-md">{m.strengths_md || '—'}</p>
								<h4 class="tmr-subhead">Development areas</h4>
								<p class="tmr-md">{m.development_areas_md || '—'}</p>
								{#if m.notes_md}
									<h4 class="tmr-subhead">Notes</h4>
									<p class="tmr-md">{m.notes_md}</p>
								{/if}
								<div class="tmr-detail__actions">
									<button
										type="button"
										class="tmr-btn-secondary"
										data-testid={`lq-ai-mgmt-team-edit-${m.id}`}
										on:click={() => startEdit(m)}
									>
										Edit
									</button>
									{#if confirmingDeleteId === m.id}
										<span
											class="tmr-confirm"
											role="alertdialog"
											aria-label="Confirm member removal"
										>
											Remove {m.name}?
											<button
												type="button"
												class="tmr-btn-danger"
												disabled={deleting}
												data-testid={`lq-ai-mgmt-team-delete-confirm-${m.id}`}
												on:click={() => confirmDelete(m.id)}
											>
												{deleting ? 'Removing…' : 'Yes, remove'}
											</button>
											<button
												type="button"
												class="tmr-btn-secondary"
												disabled={deleting}
												on:click={() => (confirmingDeleteId = null)}
											>
												Cancel
											</button>
										</span>
									{:else}
										<button
											type="button"
											class="tmr-btn-ghost-danger"
											data-testid={`lq-ai-mgmt-team-delete-${m.id}`}
											on:click={() => (confirmingDeleteId = m.id)}
										>
											Remove
										</button>
									{/if}
								</div>
							</div>
						{/if}
					{/if}

					<button
						type="button"
						class="tmr-btn-review"
						disabled={creatingReviewId === m.id}
						title="A 1-on-1 review-prep brief assembled from this person's actual numbers."
						data-testid="lq-ai-mgmt-team-review-cta"
						on:click={() => prepareReview(m.id)}
					>
						{creatingReviewId === m.id ? 'Starting…' : 'Prepare 1-on-1 review ✦'}
					</button>

					{#if reviewCreateErrors[m.id]}
						<p class="tmr-inline-error" role="alert">{reviewCreateErrors[m.id]}</p>
					{/if}

					{#if reviewJobIds[m.id]}
						<div
							class="tmr-review-panel"
							data-testid={`lq-ai-mgmt-team-review-panel-${m.id}`}
							aria-label={`Review prep for ${m.name}`}
						>
							<MgmtAiJobRunner
								jobId={reviewJobIds[m.id]}
								runningCopy="Drafting the review prep…"
								onDone={() => onReviewDone(m.id)}
								onRetry={() => prepareReview(m.id)}
							/>
							<p class="tmr-review-hint">Private to you. Draft — your judgment prevails.</p>
						</div>
					{/if}

					<div class="tmr-pastpreps">
						<button
							type="button"
							class="tmr-expand"
							aria-expanded={!!pastPrepsOpen[m.id]}
							data-testid={`lq-ai-mgmt-team-past-preps-toggle-${m.id}`}
							on:click={() => togglePastPreps(m.id)}
						>
							{pastPrepsOpen[m.id] ? 'Hide past preps' : 'Past preps'}
						</button>
						{#if pastPrepsOpen[m.id]}
							{#if pastPrepsLoading[m.id]}
								<p class="tmr-empty-line">Loading past preps…</p>
							{:else if pastPrepsErrors[m.id]}
								<p class="tmr-inline-error" role="alert">{pastPrepsErrors[m.id]}</p>
							{:else if !pastPreps[m.id] || pastPreps[m.id]?.length === 0}
								<p class="tmr-empty-line">No preps yet — the first one lands here.</p>
							{:else}
								<ul class="tmr-pastpreps-list">
									{#each pastPreps[m.id] ?? [] as prep (prep.id)}
										<li class="tmr-pastprep">
											<button
												type="button"
												class="tmr-pastprep__row"
												aria-expanded={openPastPrepIds[m.id] === prep.id}
												on:click={() =>
													(openPastPrepIds = {
														...openPastPrepIds,
														[m.id]: openPastPrepIds[m.id] === prep.id ? null : prep.id
													})}
											>
												Prep · {formatDateTime(prep.completed_at ?? prep.created_at)}
											</button>
											{#if openPastPrepIds[m.id] === prep.id}
												<!-- eslint-disable-next-line svelte/no-at-html-tags — sanitised via DOMPurify in renderBriefMarkdown -->
												<div class="tmr-prep-md">{@html renderBriefMarkdown(prep.result_md)}</div>
											{/if}
										</li>
									{/each}
								</ul>
							{/if}
						{/if}
					</div>
				</article>
			{/each}
		</div>
	{/if}
</main>

{#if showNewModal}
	<NewTeamMemberModal
		onClose={() => (showNewModal = false)}
		onCreated={() => {
			showNewModal = false;
			void refresh();
		}}
	/>
{/if}

<style>
	.tmr-page {
		padding: var(--lq-space-6);
		max-width: 1100px;
		margin: 0 auto;
	}

	.tmr-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.tmr-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-5, 1.25rem);
	}

	.tmr-sub {
		margin-top: var(--lq-space-2);
		color: var(--lq-text-secondary);
	}

	.tmr-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.tmr-state-msg--error {
		color: var(--lq-error);
	}

	.tmr-empty {
		text-align: center;
		padding: var(--lq-space-8) var(--lq-space-4);
	}

	.tmr-empty__copy {
		color: var(--lq-text-secondary);
		margin-bottom: var(--lq-space-4);
	}

	.tmr-grid {
		display: grid;
		gap: var(--lq-space-4);
		grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
	}

	.tmr-card {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-4);
	}

	.tmr-card__top {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-2);
	}

	.tmr-card__name {
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: 0;
	}

	.tmr-card__role {
		color: var(--lq-text-secondary);
		font-size: 0.85rem;
		margin: var(--lq-space-1) 0 0;
	}

	.tmr-card__seniority {
		color: var(--lq-text-tertiary);
	}

	.tmr-card__count {
		font-size: 0.75rem;
		color: var(--lq-text-tertiary);
		margin: 0;
	}

	.tmr-badges {
		display: flex;
		align-items: center;
		gap: var(--lq-space-1);
		flex-shrink: 0;
	}

	.tmr-dept {
		flex-shrink: 0;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: var(--lq-radius-pill);
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
		border: 1px solid var(--lq-border);
		background: var(--lq-inset);
		color: var(--lq-text-secondary);
	}

	/* Performance read — same pill as the department badge, traffic-lit:
	   green exceeds / amber meets / red below. Tones match the attainment
	   chips on the KPI rows below, so the card can't contradict itself. */
	.tmr-perf--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}

	.tmr-perf--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}

	.tmr-perf--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}

	.tmr-kpis {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.tmr-kpi-row {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		padding: var(--lq-space-1) var(--lq-space-2);
		border-radius: var(--lq-radius);
		text-decoration: none;
		color: inherit;
		font-size: 0.85rem;
	}

	.tmr-kpi-row .tmr-chip {
		flex-shrink: 0;
		white-space: nowrap;
	}

	.tmr-kpi-row:hover {
		background: var(--lq-inset);
	}

	.tmr-kpi-row:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	/* Long KPI names were being crushed into a sliver beside the unshrinkable
	   value + chip, wrapping letter-by-letter. The name keeps a readable
	   minimum width, wraps at word boundaries only, and the row wraps the
	   value/chip to the next line when the card is narrow. */
	.tmr-kpi-row {
		flex-wrap: wrap;
	}

	.tmr-kpi-row__name {
		flex: 1 1 14ch;
		min-width: 10ch;
		color: var(--lq-accent);
		overflow-wrap: normal;
		word-break: normal;
	}

	.tmr-kpi-row__value {
		flex-shrink: 0;
		font-weight: 600;
		color: var(--lq-text-primary);
		white-space: nowrap;
	}

	.tmr-kpi-row__unit {
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--lq-text-tertiary);
	}

	.tmr-chip {
		font-size: 0.65rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: var(--lq-radius-pill);
		padding: 0.1rem 0.45rem;
		white-space: nowrap;
		border: 1px solid transparent;
	}

	.tmr-chip--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}

	.tmr-chip--info {
		background: var(--lq-tier-soft);
		color: var(--lq-tier);
		border-color: var(--lq-tier-border);
	}

	.tmr-chip--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}

	.tmr-chip--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}

	.tmr-chip--muted {
		background: var(--lq-inset);
		color: var(--lq-text-tertiary);
		border-color: var(--lq-border);
	}

	.tmr-expand {
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

	.tmr-expand:hover {
		border-color: var(--lq-accent);
		color: var(--lq-accent);
	}

	.tmr-expand:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.tmr-detail {
		border-top: 1px dashed var(--lq-border);
		padding-top: var(--lq-space-2);
	}

	.tmr-subhead {
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--lq-text-tertiary);
		margin: var(--lq-space-2) 0 var(--lq-space-1);
	}

	.tmr-md {
		color: var(--lq-text-secondary);
		font-size: 0.85rem;
		line-height: 1.55;
		white-space: pre-wrap;
		margin: 0;
	}

	.tmr-detail__actions {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		margin-top: var(--lq-space-3);
		flex-wrap: wrap;
	}

	.tmr-confirm {
		display: inline-flex;
		align-items: center;
		gap: var(--lq-space-2);
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
	}

	.tmr-edit-form {
		border-top: 1px dashed var(--lq-border);
		padding-top: var(--lq-space-3);
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-3);
	}

	.tmr-form-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: var(--lq-space-3);
	}

	.tmr-form-row {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.tmr-label {
		font-size: 12px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.tmr-input,
	.tmr-textarea {
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-2);
		font-size: 13px;
		color: var(--lq-text-primary);
		box-sizing: border-box;
		transition: border-color 0.15s ease;
	}

	.tmr-input:focus,
	.tmr-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.tmr-textarea {
		resize: vertical;
	}

	.tmr-form-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-2);
	}

	.tmr-inline-error {
		color: var(--lq-error);
		font-size: 13px;
		margin: 0 0 var(--lq-space-2);
	}

	.tmr-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.tmr-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.tmr-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.tmr-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.tmr-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.tmr-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.tmr-btn-secondary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.tmr-btn-secondary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.tmr-btn-danger {
		background: var(--lq-error);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.tmr-btn-danger:focus-visible {
		outline: 2px solid var(--lq-error);
		outline-offset: 2px;
	}

	.tmr-btn-ghost-danger {
		background: transparent;
		color: var(--lq-error);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.tmr-btn-ghost-danger:hover {
		background: var(--lq-error-soft);
	}

	.tmr-btn-ghost-danger:focus-visible {
		outline: 2px solid var(--lq-error);
		outline-offset: 2px;
	}

	.tmr-btn-review {
		align-self: flex-start;
		margin-top: var(--lq-space-1);
		background: transparent;
		color: var(--lq-accent);
		border: 1px solid var(--lq-accent-border, var(--lq-accent));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.tmr-btn-review:hover:not(:disabled) {
		background: var(--lq-accent-soft);
	}

	.tmr-btn-review:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.tmr-btn-review:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.tmr-review-panel {
		border-top: 1px dashed var(--lq-border);
		padding-top: var(--lq-space-2);
	}

	.tmr-review-hint {
		font-size: 0.75rem;
		color: var(--lq-text-tertiary);
		font-style: italic;
		margin: var(--lq-space-2) 0 0;
	}

	.tmr-pastpreps {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
	}

	.tmr-pastpreps-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.tmr-pastprep {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		background: var(--lq-inset);
	}

	.tmr-pastprep__row {
		display: block;
		width: 100%;
		text-align: left;
		background: none;
		border: 0;
		padding: var(--lq-space-1) var(--lq-space-2);
		font-size: 0.8rem;
		color: var(--lq-text-primary);
		cursor: pointer;
	}

	.tmr-pastprep__row:hover {
		color: var(--lq-accent);
	}

	.tmr-pastprep__row:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.tmr-prep-md {
		padding: 0 var(--lq-space-2) var(--lq-space-2);
		color: var(--lq-text-primary);
		font-size: 0.85rem;
		line-height: 1.6;
	}

	.tmr-prep-md :global(h1),
	.tmr-prep-md :global(h2),
	.tmr-prep-md :global(h3) {
		font-size: 0.9rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: var(--lq-space-2) 0 var(--lq-space-1);
	}

	.tmr-prep-md :global(p),
	.tmr-prep-md :global(ul),
	.tmr-prep-md :global(ol) {
		margin: 0 0 var(--lq-space-2);
	}

	.tmr-prep-md :global(ul),
	.tmr-prep-md :global(ol) {
		padding-left: 1.25rem;
	}

	.tmr-prep-md :global(.mgmt-src-marker) {
		font-family: var(--lq-font-mono, ui-monospace, monospace);
		font-size: 0.72em;
		color: var(--lq-text-tertiary);
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-pill);
		padding: 0.05em 0.45em;
		white-space: nowrap;
	}
</style>
