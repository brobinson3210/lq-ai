<!--
  /lq-ai/management/stakeholders/[id] — the stakeholder dossier.

  Everything the GC knows about one relationship: profile, the interaction
  timeline, open commitments in both directions, and stances by situation
  (latest per topic, with expandable history). Health and cadence are
  editable inline — one click, PATCH, done.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import { managementBackHref } from '$lib/lq-ai/management/backNav';
	import { page } from '$app/stores';
	import { stakeholdersApi } from '$lib/lq-ai/api';
	import type {
		CommitmentDirection,
		CommitmentStatus,
		InteractionChannel,
		Stakeholder,
		StakeholderCommitment,
		StakeholderHealth,
		StakeholderInteraction,
		StakeholderPosition,
		StakeholderStance
	} from '$lib/lq-ai/types';
	import {
		CHANNEL_LABELS,
		CHANNEL_OPTIONS,
		HEALTH_OPTIONS,
		STANCE_LABELS,
		STANCE_OPTIONS,
		cadenceExceeded,
		formatDate,
		formatDateTime,
		healthTone,
		isOverdue,
		lastTouchLabel,
		localDatetimeToISO,
		nowLocalDatetime,
		stanceTone,
		todayISODate,
		typeLabel
	} from '$lib/lq-ai/management/stakeholders';
	import { managementAiApi } from '$lib/lq-ai/api';
	import type { MgmtAiJobListRow } from '$lib/lq-ai/types';
	import { renderBriefMarkdown } from '$lib/lq-ai/management/markdown';
	import MgmtAiJobRunner from '$lib/lq-ai/components/MgmtAiJobRunner.svelte';

	$: stakeholderId = $page.params.id;
	$: spaceParam = $page.url.searchParams.get('space') ?? '';
	$: fallbackUrl = `/lq-ai/management/stakeholders${spaceParam ? `?space=${spaceParam}` : ''}`;

	// History-aware back: a dossier can be reached from the registry, a
	// relationship space, or the commitments rollup — return to wherever
	// the user actually came from within the tab.
	let originUrl: string | null = null;
	afterNavigate((nav) => {
		originUrl = managementBackHref(nav, '') || null;
	});
	$: backUrl = originUrl || fallbackUrl;

	let stakeholder: Stakeholder | null = null;
	let interactions: StakeholderInteraction[] = [];
	let commitments: StakeholderCommitment[] = [];
	let positions: StakeholderPosition[] = [];
	let loading = true;
	let error: string | null = null;

	async function loadAll() {
		if (!stakeholderId) return;
		loading = true;
		try {
			[stakeholder, interactions, commitments, positions] = await Promise.all([
				stakeholdersApi.getStakeholder(stakeholderId),
				stakeholdersApi.listInteractions(stakeholderId),
				stakeholdersApi.listCommitments(stakeholderId),
				stakeholdersApi.listPositions(stakeholderId)
			]);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load stakeholder';
		} finally {
			loading = false;
		}
	}

	onMount(loadAll);

	async function refreshStakeholder() {
		if (!stakeholderId) return;
		stakeholder = await stakeholdersApi.getStakeholder(stakeholderId);
	}

	// ----- Pre-meeting brief (AI) -----

	let briefJobId: string | null = null;
	let briefPanelOpen = false;
	let creatingBrief = false;
	let briefCreateError: string | null = null;

	async function prepareBrief() {
		if (!stakeholderId) return;
		creatingBrief = true;
		briefCreateError = null;
		try {
			const job = await managementAiApi.createAiJob({
				job_type: 'pre_meeting_brief',
				stakeholder_id: stakeholderId
			});
			briefJobId = job.id;
			briefPanelOpen = true;
		} catch (e) {
			briefCreateError = e instanceof Error ? e.message : 'Failed to start the brief';
		} finally {
			creatingBrief = false;
		}
	}

	function onBriefDone() {
		// A finished brief belongs in the past-briefs list on next open.
		pastBriefs = null;
	}

	// Past briefs — lazy-loaded from the list endpoint, newest first.
	let pastBriefsOpen = false;
	let pastBriefs: MgmtAiJobListRow[] | null = null;
	let pastBriefsLoading = false;
	let pastBriefsError: string | null = null;
	let openPastBriefId: string | null = null;

	async function togglePastBriefs() {
		pastBriefsOpen = !pastBriefsOpen;
		if (pastBriefsOpen && pastBriefs === null && stakeholderId) {
			pastBriefsLoading = true;
			pastBriefsError = null;
			try {
				const rows = await managementAiApi.listAiJobs({
					jobType: 'pre_meeting_brief',
					stakeholderId
				});
				pastBriefs = rows.filter((r) => r.status === 'done' && r.result_md);
			} catch (e) {
				pastBriefsError = e instanceof Error ? e.message : 'Failed to load past briefs';
			} finally {
				pastBriefsLoading = false;
			}
		}
	}

	// ----- Health (inline select) -----

	let healthError: string | null = null;

	async function changeHealth(e: Event) {
		if (!stakeholder) return;
		const value = (e.currentTarget as HTMLSelectElement).value;
		healthError = null;
		try {
			stakeholder = await stakeholdersApi.patchStakeholder(stakeholder.id, {
				overall_health: (value || null) as StakeholderHealth | null
			});
		} catch (err) {
			healthError = err instanceof Error ? err.message : 'Failed to update health';
		}
	}

	// ----- Cadence (inline edit) -----

	let editingCadence = false;
	let cadenceDraft = '';
	let cadenceError: string | null = null;

	function startCadenceEdit() {
		cadenceDraft = stakeholder?.cadence_target_days?.toString() ?? '';
		cadenceError = null;
		editingCadence = true;
	}

	async function saveCadence() {
		if (!stakeholder) return;
		const trimmed = cadenceDraft.trim();
		if (trimmed) {
			const n = Number(trimmed);
			if (!Number.isInteger(n) || n < 1) {
				cadenceError = 'Cadence must be a whole number of days (1 or more).';
				return;
			}
		}
		cadenceError = null;
		try {
			stakeholder = await stakeholdersApi.patchStakeholder(stakeholder.id, {
				cadence_target_days: trimmed ? Number(trimmed) : null
			});
			editingCadence = false;
		} catch (err) {
			cadenceError = err instanceof Error ? err.message : 'Failed to update cadence';
		}
	}

	// ----- Profile (compact edit mode) -----

	let editingProfile = false;
	let profileError: string | null = null;
	let savingProfile = false;
	let draftOrganization = '';
	let draftRoleTitle = '';
	let draftCommitteeSeats = '';
	let draftInterests = '';
	let draftCommsPrefs = '';
	let draftNotes = '';

	function startProfileEdit() {
		if (!stakeholder) return;
		draftOrganization = stakeholder.organization ?? '';
		draftRoleTitle = stakeholder.role_title ?? '';
		draftCommitteeSeats = stakeholder.committee_seats ?? '';
		draftInterests = stakeholder.interests_md ?? '';
		draftCommsPrefs = stakeholder.communication_preferences_md ?? '';
		draftNotes = stakeholder.notes_md ?? '';
		profileError = null;
		editingProfile = true;
	}

	async function saveProfile() {
		if (!stakeholder) return;
		savingProfile = true;
		profileError = null;
		try {
			stakeholder = await stakeholdersApi.patchStakeholder(stakeholder.id, {
				organization: draftOrganization.trim() || null,
				role_title: draftRoleTitle.trim() || null,
				committee_seats: draftCommitteeSeats.trim() || null,
				interests_md: draftInterests.trim() || null,
				communication_preferences_md: draftCommsPrefs.trim() || null,
				notes_md: draftNotes.trim() || null
			});
			editingProfile = false;
		} catch (err) {
			profileError = err instanceof Error ? err.message : 'Failed to save profile';
		} finally {
			savingProfile = false;
		}
	}

	// ----- Interactions -----

	let interactionOccurredAt = nowLocalDatetime();
	let interactionChannel: InteractionChannel = 'meeting';
	let interactionSummary = '';
	let interactionError: string | null = null;
	let addingInteraction = false;

	async function addInteraction() {
		if (!stakeholderId) return;
		if (!interactionSummary.trim()) {
			interactionError = 'A short summary is required.';
			return;
		}
		if (!interactionOccurredAt) {
			interactionError = 'When did this happen?';
			return;
		}
		addingInteraction = true;
		interactionError = null;
		try {
			await stakeholdersApi.createInteraction(stakeholderId, {
				occurred_at: localDatetimeToISO(interactionOccurredAt),
				channel: interactionChannel,
				summary_md: interactionSummary.trim()
			});
			interactionSummary = '';
			interactionOccurredAt = nowLocalDatetime();
			[interactions] = await Promise.all([
				stakeholdersApi.listInteractions(stakeholderId),
				refreshStakeholder()
			]);
		} catch (err) {
			interactionError = err instanceof Error ? err.message : 'Failed to log interaction';
		} finally {
			addingInteraction = false;
		}
	}

	// ----- Commitments -----

	$: weOwe = commitments.filter((c) => c.direction === 'we_owe');
	$: theyOwe = commitments.filter((c) => c.direction === 'they_owe');

	let commitmentDirection: CommitmentDirection = 'we_owe';
	let commitmentDescription = '';
	let commitmentDueDate = '';
	let commitmentError: string | null = null;
	let addingCommitment = false;

	async function addCommitment() {
		if (!stakeholderId) return;
		if (!commitmentDescription.trim()) {
			commitmentError = 'A description is required.';
			return;
		}
		addingCommitment = true;
		commitmentError = null;
		try {
			await stakeholdersApi.createCommitment(stakeholderId, {
				direction: commitmentDirection,
				description: commitmentDescription.trim(),
				due_date: commitmentDueDate || undefined
			});
			commitmentDescription = '';
			commitmentDueDate = '';
			[commitments] = await Promise.all([
				stakeholdersApi.listCommitments(stakeholderId),
				refreshStakeholder()
			]);
		} catch (err) {
			commitmentError = err instanceof Error ? err.message : 'Failed to add commitment';
		} finally {
			addingCommitment = false;
		}
	}

	async function changeCommitmentStatus(c: StakeholderCommitment, e: Event) {
		if (!stakeholderId) return;
		const status = (e.currentTarget as HTMLSelectElement).value as CommitmentStatus;
		commitmentError = null;
		try {
			await stakeholdersApi.patchCommitment(c.id, { status });
			[commitments] = await Promise.all([
				stakeholdersApi.listCommitments(stakeholderId),
				refreshStakeholder()
			]);
		} catch (err) {
			commitmentError = err instanceof Error ? err.message : 'Failed to update commitment';
		}
	}

	// ----- Positions (stances by situation) -----

	interface TopicGroup {
		topic: string;
		latest: StakeholderPosition;
		history: StakeholderPosition[];
	}

	function groupByTopic(rows: StakeholderPosition[]): TopicGroup[] {
		const sorted = [...rows].sort(
			(a, b) =>
				b.as_of.localeCompare(a.as_of) || (b.created_at ?? '').localeCompare(a.created_at ?? '')
		);
		const map = new Map<string, StakeholderPosition[]>();
		for (const row of sorted) {
			const bucket = map.get(row.topic);
			if (bucket) bucket.push(row);
			else map.set(row.topic, [row]);
		}
		return [...map.entries()].map(([topic, history]) => ({
			topic,
			latest: history[0],
			history
		}));
	}

	$: topicGroups = groupByTopic(positions);
	let expandedTopics: Record<string, boolean> = {};

	function toggleTopic(topic: string) {
		expandedTopics = { ...expandedTopics, [topic]: !expandedTopics[topic] };
	}

	let positionTopic = '';
	let positionStance: StakeholderStance = 'unknown';
	let positionAsOf = todayISODate();
	let positionNote = '';
	let positionError: string | null = null;
	let addingPosition = false;

	async function addPosition() {
		if (!stakeholderId) return;
		if (!positionTopic.trim()) {
			positionError = 'A topic is required.';
			return;
		}
		if (!positionAsOf) {
			positionError = 'An as-of date is required.';
			return;
		}
		addingPosition = true;
		positionError = null;
		try {
			await stakeholdersApi.createPosition(stakeholderId, {
				topic: positionTopic.trim(),
				stance: positionStance,
				as_of: positionAsOf,
				note_md: positionNote.trim() || undefined
			});
			positionTopic = '';
			positionStance = 'unknown';
			positionAsOf = todayISODate();
			positionNote = '';
			positions = await stakeholdersApi.listPositions(stakeholderId);
		} catch (err) {
			positionError = err instanceof Error ? err.message : 'Failed to record stance';
		} finally {
			addingPosition = false;
		}
	}
</script>

<main class="dsr-page" data-testid="lq-ai-mgmt-dossier-page">
	<a class="dsr-back" href={backUrl}>← Stakeholders</a>

	{#if loading}
		<p class="lq-text-body dsr-state-msg">Loading dossier…</p>
	{:else if error || !stakeholder}
		<p class="lq-text-body dsr-state-msg dsr-state-msg--error" role="alert">
			{error ?? 'Stakeholder not found'}
		</p>
	{:else}
		<header class="dsr-header">
			<div class="dsr-header__main">
				<h1 class="lq-text-page-h">{stakeholder.full_name}</h1>
				<p class="dsr-header__type">{typeLabel(stakeholder.stakeholder_type)}</p>
			</div>

			<div class="dsr-header__meta">
				<label class="dsr-health-label">
					<span class="dsr-meta-caption">Health</span>
					<select
						class={`dsr-health-select dsr-health-select--${healthTone(stakeholder.overall_health)}`}
						value={stakeholder.overall_health ?? ''}
						aria-label="Overall relationship health"
						data-testid="lq-ai-mgmt-dossier-health-select"
						on:change={changeHealth}
					>
						<option value="">Unset</option>
						{#each HEALTH_OPTIONS as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
				</label>

				<div class="dsr-cadence">
					<span class="dsr-meta-caption">Cadence</span>
					<span
						class="dsr-touch"
						class:dsr-touch--late={cadenceExceeded(
							stakeholder.days_since_last_interaction,
							stakeholder.cadence_target_days
						)}
					>
						{lastTouchLabel(stakeholder.days_since_last_interaction)}
					</span>
					{#if editingCadence}
						<span class="dsr-cadence-edit">
							<input
								type="number"
								min="1"
								step="1"
								class="dsr-input dsr-input--tiny"
								bind:value={cadenceDraft}
								aria-label="Touch cadence target in days"
								data-testid="lq-ai-mgmt-dossier-cadence-input"
							/>
							<button type="button" class="dsr-btn-mini" on:click={saveCadence}>Save</button>
							<button
								type="button"
								class="dsr-btn-mini dsr-btn-mini--ghost"
								on:click={() => (editingCadence = false)}
							>
								Cancel
							</button>
						</span>
					{:else}
						<button
							type="button"
							class="dsr-cadence-value"
							aria-label="Edit touch cadence target"
							data-testid="lq-ai-mgmt-dossier-cadence-edit"
							on:click={startCadenceEdit}
						>
							{stakeholder.cadence_target_days
								? `target: every ${stakeholder.cadence_target_days} days`
								: 'no cadence target'} ✎
						</button>
					{/if}
				</div>

				<button
					type="button"
					class="dsr-btn-brief"
					disabled={creatingBrief}
					title="A concise, cited pre-meeting brief drawn from this dossier and your document space."
					data-testid="lq-ai-mgmt-dossier-brief-cta"
					on:click={prepareBrief}
				>
					{creatingBrief ? 'Starting…' : 'Prepare my brief ✦'}
				</button>
			</div>
		</header>
		{#if healthError}
			<p class="dsr-inline-error" role="alert">{healthError}</p>
		{/if}
		{#if cadenceError}
			<p class="dsr-inline-error" role="alert">{cadenceError}</p>
		{/if}
		{#if briefCreateError}
			<p class="dsr-inline-error" role="alert">{briefCreateError}</p>
		{/if}

		<!-- ── Pre-meeting brief ───────────────────────────────────── -->
		{#if briefJobId}
			<section
				class="dsr-section dsr-brief"
				aria-labelledby="dsr-brief-h"
				data-testid="lq-ai-mgmt-dossier-brief-panel"
			>
				<div class="dsr-section-head">
					<h2 id="dsr-brief-h">Pre-meeting brief</h2>
					<div class="dsr-brief-head-actions">
						<button
							type="button"
							class="dsr-btn-mini dsr-btn-mini--ghost"
							data-testid="lq-ai-mgmt-dossier-brief-regenerate"
							disabled={creatingBrief}
							on:click={prepareBrief}
						>
							Regenerate ✦
						</button>
						<button
							type="button"
							class="dsr-btn-mini dsr-btn-mini--ghost"
							aria-expanded={briefPanelOpen}
							aria-controls="dsr-brief-body"
							on:click={() => (briefPanelOpen = !briefPanelOpen)}
						>
							{briefPanelOpen ? 'Collapse' : 'Expand'}
						</button>
					</div>
				</div>
				{#if briefPanelOpen}
					<div id="dsr-brief-body">
						<MgmtAiJobRunner
							jobId={briefJobId}
							runningCopy="Drafting your brief…"
							onDone={onBriefDone}
							onRetry={prepareBrief}
						/>
					</div>
				{/if}
			</section>
		{/if}

		<!-- ── Past briefs ─────────────────────────────────────────── -->
		<section class="dsr-pastbriefs" data-testid="lq-ai-mgmt-dossier-past-briefs">
			<button
				type="button"
				class="dsr-btn-mini dsr-btn-mini--ghost"
				aria-expanded={pastBriefsOpen}
				data-testid="lq-ai-mgmt-dossier-past-briefs-toggle"
				on:click={togglePastBriefs}
			>
				{pastBriefsOpen ? 'Hide past briefs' : 'Past briefs'}
			</button>
			{#if pastBriefsOpen}
				{#if pastBriefsLoading}
					<p class="dsr-empty-line">Loading past briefs…</p>
				{:else if pastBriefsError}
					<p class="dsr-inline-error" role="alert">{pastBriefsError}</p>
				{:else if !pastBriefs || pastBriefs.length === 0}
					<p class="dsr-empty-line">No briefs yet — the first one lands here.</p>
				{:else}
					<ul class="dsr-pastbriefs-list">
						{#each pastBriefs as brief (brief.id)}
							<li class="dsr-pastbrief">
								<button
									type="button"
									class="dsr-pastbrief__row"
									aria-expanded={openPastBriefId === brief.id}
									on:click={() =>
										(openPastBriefId = openPastBriefId === brief.id ? null : brief.id)}
								>
									Brief · {formatDateTime(brief.completed_at ?? brief.created_at)}
								</button>
								{#if openPastBriefId === brief.id}
									<!-- eslint-disable-next-line svelte/no-at-html-tags — sanitised via DOMPurify in renderBriefMarkdown -->
									<div class="dsr-brief-md">{@html renderBriefMarkdown(brief.result_md)}</div>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			{/if}
		</section>

		<!-- ── Profile ─────────────────────────────────────────────── -->
		<section class="dsr-section" aria-labelledby="dsr-profile-h">
			<div class="dsr-section-head">
				<h2 id="dsr-profile-h">Profile</h2>
				{#if !editingProfile}
					<button
						type="button"
						class="dsr-btn-mini dsr-btn-mini--ghost"
						data-testid="lq-ai-mgmt-dossier-edit-profile"
						on:click={startProfileEdit}
					>
						Edit
					</button>
				{/if}
			</div>

			{#if editingProfile}
				<form class="dsr-profile-form" on:submit|preventDefault={saveProfile}>
					<div class="dsr-form-row">
						<label class="dsr-label" for="dsr-org">Organization</label>
						<input id="dsr-org" class="dsr-input" type="text" bind:value={draftOrganization} />
					</div>
					<div class="dsr-form-row">
						<label class="dsr-label" for="dsr-role">Role / title</label>
						<input id="dsr-role" class="dsr-input" type="text" bind:value={draftRoleTitle} />
					</div>
					<div class="dsr-form-row">
						<label class="dsr-label" for="dsr-seats">Committee seats</label>
						<input id="dsr-seats" class="dsr-input" type="text" bind:value={draftCommitteeSeats} />
					</div>
					<div class="dsr-form-row">
						<label class="dsr-label" for="dsr-interests">Interests</label>
						<textarea id="dsr-interests" class="dsr-textarea" rows="3" bind:value={draftInterests}
						></textarea>
					</div>
					<div class="dsr-form-row">
						<label class="dsr-label" for="dsr-comms">Communication preferences</label>
						<textarea id="dsr-comms" class="dsr-textarea" rows="3" bind:value={draftCommsPrefs}
						></textarea>
					</div>
					<div class="dsr-form-row">
						<label class="dsr-label" for="dsr-notes">Notes</label>
						<textarea id="dsr-notes" class="dsr-textarea" rows="4" bind:value={draftNotes}
						></textarea>
					</div>
					{#if profileError}
						<p class="dsr-inline-error" role="alert">{profileError}</p>
					{/if}
					<div class="dsr-form-actions">
						<button
							type="button"
							class="dsr-btn-secondary"
							disabled={savingProfile}
							on:click={() => (editingProfile = false)}
						>
							Cancel
						</button>
						<button
							type="submit"
							class="dsr-btn-primary"
							disabled={savingProfile}
							data-testid="lq-ai-mgmt-dossier-save-profile"
						>
							{savingProfile ? 'Saving…' : 'Save profile'}
						</button>
					</div>
				</form>
			{:else}
				<dl class="dsr-profile-grid">
					<div>
						<dt>Organization</dt>
						<dd>{stakeholder.organization || '—'}</dd>
					</div>
					<div>
						<dt>Role / title</dt>
						<dd>{stakeholder.role_title || '—'}</dd>
					</div>
					<div>
						<dt>Committee seats</dt>
						<dd>{stakeholder.committee_seats || '—'}</dd>
					</div>
				</dl>
				{#if stakeholder.interests_md}
					<h3 class="dsr-subhead">Interests</h3>
					<p class="dsr-md">{stakeholder.interests_md}</p>
				{/if}
				{#if stakeholder.communication_preferences_md}
					<h3 class="dsr-subhead">Communication preferences</h3>
					<p class="dsr-md">{stakeholder.communication_preferences_md}</p>
				{/if}
				{#if stakeholder.notes_md}
					<h3 class="dsr-subhead">Notes</h3>
					<p class="dsr-md">{stakeholder.notes_md}</p>
				{/if}
			{/if}
		</section>

		<!-- ── Interactions ────────────────────────────────────────── -->
		<section class="dsr-section" aria-labelledby="dsr-interactions-h">
			<div class="dsr-section-head">
				<h2 id="dsr-interactions-h">Interactions</h2>
			</div>

			<form
				class="dsr-inline-form"
				on:submit|preventDefault={addInteraction}
				data-testid="lq-ai-mgmt-dossier-add-interaction"
			>
				<input
					type="datetime-local"
					class="dsr-input dsr-input--compact"
					bind:value={interactionOccurredAt}
					aria-label="When the interaction occurred"
					disabled={addingInteraction}
				/>
				<select
					class="dsr-input dsr-input--compact"
					bind:value={interactionChannel}
					aria-label="Interaction channel"
					disabled={addingInteraction}
				>
					{#each CHANNEL_OPTIONS as opt (opt.value)}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>
				<input
					type="text"
					class="dsr-input dsr-inline-form__grow"
					bind:value={interactionSummary}
					placeholder="What happened, in a sentence or two…"
					aria-label="Interaction summary"
					disabled={addingInteraction}
				/>
				<button type="submit" class="dsr-btn-primary" disabled={addingInteraction}>
					{addingInteraction ? 'Logging…' : 'Log'}
				</button>
			</form>
			{#if interactionError}
				<p class="dsr-inline-error" role="alert">{interactionError}</p>
			{/if}

			{#if interactions.length === 0}
				<p class="dsr-empty-line">No interactions logged yet.</p>
			{:else}
				<ol class="dsr-timeline" data-testid="lq-ai-mgmt-dossier-interactions-list">
					{#each interactions as it (it.id)}
						<li class="dsr-timeline__item">
							<div class="dsr-timeline__meta">
								<span class="dsr-timeline__date">{formatDateTime(it.occurred_at)}</span>
								<span class="dsr-channel-badge">{CHANNEL_LABELS[it.channel] ?? it.channel}</span>
							</div>
							<p class="dsr-md">{it.summary_md}</p>
						</li>
					{/each}
				</ol>
			{/if}
		</section>

		<!-- ── Commitments ─────────────────────────────────────────── -->
		<section class="dsr-section" aria-labelledby="dsr-commitments-h">
			<div class="dsr-section-head">
				<h2 id="dsr-commitments-h">Commitments</h2>
			</div>

			<form
				class="dsr-inline-form"
				on:submit|preventDefault={addCommitment}
				data-testid="lq-ai-mgmt-dossier-add-commitment"
			>
				<select
					class="dsr-input dsr-input--compact"
					bind:value={commitmentDirection}
					aria-label="Commitment direction"
					disabled={addingCommitment}
				>
					<option value="we_owe">We owe them</option>
					<option value="they_owe">They owe us</option>
				</select>
				<input
					type="text"
					class="dsr-input dsr-inline-form__grow"
					bind:value={commitmentDescription}
					placeholder="What was promised…"
					aria-label="Commitment description"
					disabled={addingCommitment}
				/>
				<input
					type="date"
					class="dsr-input dsr-input--compact"
					bind:value={commitmentDueDate}
					aria-label="Due date (optional)"
					disabled={addingCommitment}
				/>
				<button type="submit" class="dsr-btn-primary" disabled={addingCommitment}>
					{addingCommitment ? 'Adding…' : 'Add'}
				</button>
			</form>
			{#if commitmentError}
				<p class="dsr-inline-error" role="alert">{commitmentError}</p>
			{/if}

			{#each [{ label: 'We owe them', rows: weOwe, key: 'we-owe' }, { label: 'They owe us', rows: theyOwe, key: 'they-owe' }] as group (group.key)}
				<h3 class="dsr-subhead">{group.label}</h3>
				{#if group.rows.length === 0}
					<p class="dsr-empty-line">Nothing here.</p>
				{:else}
					<ul class="dsr-rows" data-testid={`lq-ai-mgmt-dossier-commitments-${group.key}`}>
						{#each group.rows as c (c.id)}
							<li class="dsr-row" class:dsr-row--closed={c.status !== 'open'}>
								<span class="dsr-row__desc">{c.description}</span>
								<span
									class="dsr-row__due"
									class:dsr-row__due--overdue={isOverdue(c.due_date, c.status)}
								>
									{c.due_date ? `due ${formatDate(c.due_date)}` : 'no due date'}
								</span>
								<select
									class="dsr-input dsr-input--tiny"
									value={c.status}
									aria-label={`Status of commitment: ${c.description}`}
									on:change={(e) => changeCommitmentStatus(c, e)}
								>
									<option value="open">Open</option>
									<option value="done">Done</option>
									<option value="dropped">Dropped</option>
								</select>
							</li>
						{/each}
					</ul>
				{/if}
			{/each}
		</section>

		<!-- ── Positions ───────────────────────────────────────────── -->
		<section class="dsr-section" aria-labelledby="dsr-positions-h">
			<div class="dsr-section-head">
				<h2 id="dsr-positions-h">Stances by situation</h2>
			</div>

			<form
				class="dsr-inline-form"
				on:submit|preventDefault={addPosition}
				data-testid="lq-ai-mgmt-dossier-add-position"
			>
				<input
					type="text"
					class="dsr-input dsr-inline-form__grow"
					bind:value={positionTopic}
					placeholder="Topic, e.g. Series C terms"
					aria-label="Position topic"
					list="dsr-topics"
					disabled={addingPosition}
				/>
				<datalist id="dsr-topics">
					{#each topicGroups as g (g.topic)}
						<option value={g.topic}></option>
					{/each}
				</datalist>
				<select
					class="dsr-input dsr-input--compact"
					bind:value={positionStance}
					aria-label="Stance"
					disabled={addingPosition}
				>
					{#each STANCE_OPTIONS as opt (opt.value)}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>
				<input
					type="date"
					class="dsr-input dsr-input--compact"
					bind:value={positionAsOf}
					aria-label="As of date"
					disabled={addingPosition}
				/>
				<input
					type="text"
					class="dsr-input dsr-inline-form__grow"
					bind:value={positionNote}
					placeholder="Note (optional)"
					aria-label="Position note"
					disabled={addingPosition}
				/>
				<button type="submit" class="dsr-btn-primary" disabled={addingPosition}>
					{addingPosition ? 'Recording…' : 'Record'}
				</button>
			</form>
			{#if positionError}
				<p class="dsr-inline-error" role="alert">{positionError}</p>
			{/if}

			{#if topicGroups.length === 0}
				<p class="dsr-empty-line">No stances recorded yet.</p>
			{:else}
				<ul class="dsr-rows" data-testid="lq-ai-mgmt-dossier-positions-list">
					{#each topicGroups as g (g.topic)}
						<li class="dsr-topic">
							<div class="dsr-topic__head">
								<span class="dsr-topic__name">{g.topic}</span>
								<span class={`dsr-stance dsr-stance--${stanceTone(g.latest.stance)}`}>
									{STANCE_LABELS[g.latest.stance]}
								</span>
								<span class="dsr-topic__asof">as of {formatDate(g.latest.as_of)}</span>
								{#if g.history.length > 1}
									<button
										type="button"
										class="dsr-btn-mini dsr-btn-mini--ghost"
										aria-expanded={!!expandedTopics[g.topic]}
										on:click={() => toggleTopic(g.topic)}
									>
										{expandedTopics[g.topic] ? 'Hide history' : `History (${g.history.length})`}
									</button>
								{/if}
							</div>
							{#if g.latest.note_md}
								<p class="dsr-md dsr-topic__note">{g.latest.note_md}</p>
							{/if}
							{#if expandedTopics[g.topic]}
								<ol class="dsr-topic__history">
									{#each g.history as row (row.id)}
										<li class="dsr-topic__history-row">
											<span class={`dsr-stance dsr-stance--${stanceTone(row.stance)}`}>
												{STANCE_LABELS[row.stance]}
											</span>
											<span class="dsr-topic__asof">{formatDate(row.as_of)}</span>
											{#if row.note_md}
												<span class="dsr-topic__history-note">{row.note_md}</span>
											{/if}
										</li>
									{/each}
								</ol>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</section>

		<!-- ── Roadmap placeholder ─────────────────────────────────── -->
		<aside class="dsr-roadmap-card" data-testid="lq-ai-mgmt-dossier-roadmap-note">
			Live operational widgets (budget status, next report due) — roadmap
		</aside>
	{/if}
</main>

<style>
	.dsr-page {
		padding: var(--lq-space-6);
		max-width: 900px;
		margin: 0 auto;
	}

	.dsr-back {
		display: inline-block;
		color: var(--lq-accent);
		text-decoration: none;
		margin-bottom: var(--lq-space-4);
	}

	.dsr-state-msg {
		color: var(--lq-text-secondary);
		padding: var(--lq-space-4) 0;
	}

	.dsr-state-msg--error {
		color: var(--lq-error);
	}

	.dsr-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--lq-space-4);
		flex-wrap: wrap;
		margin-bottom: var(--lq-space-4);
	}

	.dsr-header__type {
		margin-top: var(--lq-space-1);
		color: var(--lq-text-tertiary);
		font-size: 0.85rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.dsr-header__meta {
		display: flex;
		gap: var(--lq-space-6);
		align-items: flex-start;
		flex-wrap: wrap;
	}

	.dsr-meta-caption {
		display: block;
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--lq-text-tertiary);
		margin-bottom: var(--lq-space-1);
	}

	.dsr-health-select {
		border-radius: var(--lq-radius);
		border: 1px solid var(--lq-border);
		padding: var(--lq-space-1) var(--lq-space-2);
		font-size: 13px;
		font-weight: 600;
		cursor: pointer;
	}

	.dsr-health-select--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}

	.dsr-health-select--info {
		background: var(--lq-tier-soft);
		color: var(--lq-tier);
		border-color: var(--lq-tier-border);
	}

	.dsr-health-select--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}

	.dsr-health-select--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}

	.dsr-health-select--muted {
		background: var(--lq-inset);
		color: var(--lq-text-secondary);
	}

	.dsr-cadence {
		font-size: 13px;
	}

	.dsr-touch {
		display: block;
		color: var(--lq-text-secondary);
	}

	.dsr-touch--late {
		color: var(--lq-error);
		font-weight: 600;
	}

	.dsr-cadence-value {
		background: none;
		border: 0;
		padding: 0;
		margin-top: var(--lq-space-1);
		color: var(--lq-text-tertiary);
		font-size: 12px;
		cursor: pointer;
	}

	.dsr-cadence-value:hover {
		color: var(--lq-accent);
	}

	.dsr-cadence-edit {
		display: inline-flex;
		gap: var(--lq-space-1);
		align-items: center;
		margin-top: var(--lq-space-1);
	}

	.dsr-section {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-lg);
		background: var(--lq-canvas);
		padding: var(--lq-space-4) var(--lq-space-4);
		margin-bottom: var(--lq-space-4);
	}

	.dsr-section-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: var(--lq-space-3);
	}

	.dsr-section-head h2 {
		font-size: 1rem;
		font-weight: 600;
		color: var(--lq-text-primary);
	}

	.dsr-subhead {
		font-size: 0.8rem;
		font-weight: 600;
		letter-spacing: 0.03em;
		text-transform: uppercase;
		color: var(--lq-text-tertiary);
		margin: var(--lq-space-3) 0 var(--lq-space-2);
	}

	.dsr-profile-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: var(--lq-space-3);
		margin: 0;
	}

	.dsr-profile-grid dt {
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--lq-text-tertiary);
	}

	.dsr-profile-grid dd {
		margin: var(--lq-space-1) 0 0;
		color: var(--lq-text-primary);
		font-size: 0.9rem;
	}

	.dsr-md {
		color: var(--lq-text-secondary);
		font-size: 0.9rem;
		line-height: 1.6;
		white-space: pre-wrap;
		margin: 0;
	}

	.dsr-profile-form {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-3);
	}

	.dsr-form-row {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.dsr-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--lq-text-primary);
	}

	.dsr-input,
	.dsr-textarea {
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
		font-size: 14px;
		color: var(--lq-text-primary);
		box-sizing: border-box;
		transition: border-color 0.15s ease;
	}

	.dsr-input:focus,
	.dsr-textarea:focus {
		outline: none;
		border-color: var(--lq-accent);
		box-shadow: 0 0 0 2px var(--lq-accent-soft);
	}

	.dsr-textarea {
		resize: vertical;
	}

	.dsr-input--compact {
		font-size: 13px;
		padding: var(--lq-space-1) var(--lq-space-2);
	}

	.dsr-input--tiny {
		font-size: 12px;
		padding: 2px var(--lq-space-2);
		width: auto;
	}

	.dsr-form-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--lq-space-3);
	}

	.dsr-btn-primary {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.dsr-btn-primary:hover:not(:disabled) {
		filter: brightness(0.95);
	}

	.dsr-btn-primary:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.dsr-btn-primary:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.dsr-btn-secondary {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.dsr-btn-secondary:hover:not(:disabled) {
		background: var(--lq-inset);
	}

	.dsr-btn-mini {
		background: var(--lq-accent);
		color: white;
		border: 0;
		border-radius: var(--lq-radius-sm);
		padding: 2px var(--lq-space-2);
		font-size: 12px;
		font-weight: 500;
		cursor: pointer;
	}

	.dsr-btn-mini--ghost {
		background: transparent;
		color: var(--lq-text-secondary);
		border: 1px solid var(--lq-border);
	}

	.dsr-btn-mini--ghost:hover {
		border-color: var(--lq-accent);
		color: var(--lq-accent);
	}

	.dsr-btn-mini:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.dsr-inline-form {
		display: flex;
		flex-wrap: wrap;
		gap: var(--lq-space-2);
		align-items: center;
		margin-bottom: var(--lq-space-3);
	}

	.dsr-inline-form__grow {
		flex: 1;
		min-width: 160px;
	}

	.dsr-inline-error {
		color: var(--lq-error);
		font-size: 13px;
		margin: 0 0 var(--lq-space-2);
	}

	.dsr-empty-line {
		color: var(--lq-text-tertiary);
		font-size: 0.9rem;
		margin: 0 0 var(--lq-space-2);
	}

	.dsr-timeline {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-3);
	}

	.dsr-timeline__item {
		border-left: 2px solid var(--lq-border);
		padding-left: var(--lq-space-3);
	}

	.dsr-timeline__meta {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		margin-bottom: var(--lq-space-1);
	}

	.dsr-timeline__date {
		font-size: 0.8rem;
		color: var(--lq-text-tertiary);
	}

	.dsr-channel-badge {
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.03em;
		text-transform: uppercase;
		background: var(--lq-tier-soft);
		color: var(--lq-tier);
		border: 1px solid var(--lq-tier-border);
		border-radius: var(--lq-radius-pill);
		padding: 0.1rem 0.5rem;
		white-space: nowrap;
	}

	.dsr-rows {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
	}

	.dsr-row {
		display: flex;
		align-items: center;
		gap: var(--lq-space-3);
		padding: var(--lq-space-2) var(--lq-space-3);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		background: var(--lq-inset);
	}

	.dsr-row--closed {
		opacity: 0.6;
	}

	.dsr-row__desc {
		flex: 1;
		font-size: 0.9rem;
		color: var(--lq-text-primary);
	}

	.dsr-row__due {
		font-size: 0.8rem;
		color: var(--lq-text-tertiary);
		white-space: nowrap;
	}

	.dsr-row__due--overdue {
		color: var(--lq-error);
		font-weight: 600;
	}

	.dsr-topic {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		background: var(--lq-inset);
		padding: var(--lq-space-2) var(--lq-space-3);
	}

	.dsr-topic__head {
		display: flex;
		align-items: center;
		gap: var(--lq-space-2);
		flex-wrap: wrap;
	}

	.dsr-topic__name {
		font-weight: 600;
		font-size: 0.9rem;
		color: var(--lq-text-primary);
		flex: 1;
	}

	.dsr-topic__asof {
		font-size: 0.8rem;
		color: var(--lq-text-tertiary);
		white-space: nowrap;
	}

	.dsr-topic__note {
		margin-top: var(--lq-space-1);
	}

	.dsr-topic__history {
		list-style: none;
		margin: var(--lq-space-2) 0 0;
		padding: var(--lq-space-2) 0 0;
		border-top: 1px dashed var(--lq-border);
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-1);
	}

	.dsr-topic__history-row {
		display: flex;
		align-items: baseline;
		gap: var(--lq-space-2);
		font-size: 0.85rem;
	}

	.dsr-topic__history-note {
		color: var(--lq-text-secondary);
	}

	.dsr-stance {
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border-radius: var(--lq-radius-pill);
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
		border: 1px solid transparent;
	}

	.dsr-stance--good {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-color: var(--lq-accent-border);
	}

	.dsr-stance--warn {
		background: var(--lq-warn-soft);
		color: var(--lq-warn);
		border-color: var(--lq-warn-border);
	}

	.dsr-stance--error {
		background: var(--lq-error-soft);
		color: var(--lq-error);
		border-color: var(--lq-error-border);
	}

	.dsr-stance--muted,
	.dsr-stance--info {
		background: var(--lq-inset);
		color: var(--lq-text-tertiary);
		border-color: var(--lq-border);
	}

	.dsr-btn-brief {
		align-self: flex-start;
		background: transparent;
		color: var(--lq-accent);
		border: 1px solid var(--lq-accent-border, var(--lq-accent));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-4);
		font-weight: 500;
		font-size: 14px;
		cursor: pointer;
	}

	.dsr-btn-brief:hover:not(:disabled) {
		background: var(--lq-accent-soft);
	}

	.dsr-btn-brief:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.dsr-btn-brief:disabled {
		opacity: 0.65;
		cursor: not-allowed;
	}

	.dsr-brief-head-actions {
		display: flex;
		gap: var(--lq-space-2);
	}

	.dsr-pastbriefs {
		margin-bottom: var(--lq-space-4);
	}

	.dsr-pastbriefs-list {
		list-style: none;
		margin: var(--lq-space-2) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
	}

	.dsr-pastbrief {
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius);
		background: var(--lq-inset);
	}

	.dsr-pastbrief__row {
		display: block;
		width: 100%;
		text-align: left;
		background: none;
		border: 0;
		padding: var(--lq-space-2) var(--lq-space-3);
		font-size: 0.85rem;
		color: var(--lq-text-primary);
		cursor: pointer;
	}

	.dsr-pastbrief__row:hover {
		color: var(--lq-accent);
	}

	.dsr-pastbrief__row:focus-visible {
		outline: 2px solid var(--lq-accent);
		outline-offset: 2px;
	}

	.dsr-brief-md {
		padding: 0 var(--lq-space-3) var(--lq-space-3);
		color: var(--lq-text-primary);
		font-size: 0.9rem;
		line-height: 1.65;
	}

	.dsr-brief-md :global(h1),
	.dsr-brief-md :global(h2),
	.dsr-brief-md :global(h3) {
		font-size: 0.95rem;
		font-weight: 600;
		color: var(--lq-text-primary);
		margin: var(--lq-space-3) 0 var(--lq-space-2);
	}

	.dsr-brief-md :global(p),
	.dsr-brief-md :global(ul),
	.dsr-brief-md :global(ol) {
		margin: 0 0 var(--lq-space-2);
	}

	.dsr-brief-md :global(ul),
	.dsr-brief-md :global(ol) {
		padding-left: 1.25rem;
	}

	.dsr-brief-md :global(.mgmt-src-marker) {
		font-family: var(--lq-font-mono, ui-monospace, monospace);
		font-size: 0.72em;
		color: var(--lq-text-tertiary);
		background: var(--lq-canvas);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-pill);
		padding: 0.05em 0.45em;
		white-space: nowrap;
	}

	.dsr-roadmap-card {
		border: 1px dashed var(--lq-border);
		border-radius: var(--lq-radius-lg);
		padding: var(--lq-space-4);
		color: var(--lq-text-tertiary);
		font-size: 0.85rem;
		text-align: center;
	}
</style>
