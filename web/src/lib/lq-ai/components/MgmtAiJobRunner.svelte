<!--
  Shared AI-job UX for the Management tab (wizard drafts, pre-meeting
  briefs, review preps).

  Given a created job id it polls /management/ai-jobs/{id} every ~2s,
  showing an unobtrusive progress shimmer while pending/running, then the
  result (default: result_md rendered as brief markdown with source-marker
  chips; or the default slot with `let:job` for custom rendering) or the
  user-presentable error with a retry button. Retry is delegated upstream
  via `onRetry` — a failed job is terminal, so retrying means creating a
  new job and handing this component the new id.
-->
<script lang="ts">
	import { onDestroy } from 'svelte';
	import { managementAiApi } from '$lib/lq-ai/api';
	import type { MgmtAiJob } from '$lib/lq-ai/types';
	import { renderBriefMarkdown } from '$lib/lq-ai/management/markdown';

	/** The created job's id; changing it restarts polling. */
	export let jobId: string;
	/** Copy shown beside the shimmer while the model works. */
	export let runningCopy = 'Drafting…';
	/** Fires once when the job reaches status 'done'. */
	export let onDone: (job: MgmtAiJob) => void = () => {};
	/** Parent-supplied retry (creates a new job). Button hidden when absent. */
	export let onRetry: (() => void) | null = null;

	type Phase = 'polling' | 'done' | 'error';
	let phase: Phase = 'polling';
	let job: MgmtAiJob | null = null;
	let errorMessage: string | null = null;

	let generation = 0;

	async function run(id: string) {
		const gen = ++generation;
		phase = 'polling';
		job = null;
		errorMessage = null;
		try {
			const finished = await managementAiApi.pollAiJob(id, {
				onUpdate: (j) => {
					if (gen === generation) job = j;
				}
			});
			if (gen !== generation) return;
			job = finished;
			if (finished.status === 'done') {
				phase = 'done';
				onDone(finished);
			} else {
				phase = 'error';
				errorMessage = finished.error ?? 'The AI job failed.';
			}
		} catch (e) {
			if (gen !== generation) return;
			phase = 'error';
			errorMessage = e instanceof Error ? e.message : 'The AI job could not be polled.';
		}
	}

	$: if (jobId) void run(jobId);

	onDestroy(() => {
		generation += 1; // drop any in-flight updates
	});
</script>

<div class="ajr" data-testid="lq-ai-mgmt-ai-job-runner">
	{#if phase === 'polling'}
		<div
			class="ajr-progress"
			role="status"
			aria-live="polite"
			aria-busy="true"
			data-testid="lq-ai-mgmt-ai-job-progress"
		>
			<span class="ajr-progress__copy">
				{job?.status === 'running' ? runningCopy : `Queued — ${runningCopy.toLowerCase()}`}
			</span>
			<span class="ajr-shimmer" aria-hidden="true"></span>
		</div>
	{:else if phase === 'error'}
		<div class="ajr-error" role="alert" data-testid="lq-ai-mgmt-ai-job-error">
			<p class="ajr-error__msg">{errorMessage}</p>
			{#if onRetry}
				<button
					type="button"
					class="ajr-btn-retry"
					data-testid="lq-ai-mgmt-ai-job-retry"
					on:click={onRetry}
				>
					Retry
				</button>
			{/if}
		</div>
	{:else if phase === 'done' && job}
		<div class="ajr-result" data-testid="lq-ai-mgmt-ai-job-result">
			{#if $$slots.default}
				<slot {job} />
			{:else}
				<!-- eslint-disable-next-line svelte/no-at-html-tags — sanitised via DOMPurify in renderBriefMarkdown -->
				<div class="ajr-md">{@html renderBriefMarkdown(job.result_md)}</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.ajr-progress {
		display: flex;
		flex-direction: column;
		gap: var(--lq-space-2);
		padding: var(--lq-space-3) 0;
	}

	.ajr-progress__copy {
		font-size: 0.85rem;
		color: var(--lq-text-secondary);
	}

	.ajr-shimmer {
		display: block;
		height: 6px;
		border-radius: var(--lq-radius-pill);
		background: linear-gradient(
			90deg,
			var(--lq-inset) 25%,
			var(--lq-accent-soft) 50%,
			var(--lq-inset) 75%
		);
		background-size: 200% 100%;
		animation: ajr-shimmer 1.4s ease-in-out infinite;
	}

	@keyframes ajr-shimmer {
		0% {
			background-position: 200% 0;
		}
		100% {
			background-position: -200% 0;
		}
	}

	.ajr-error {
		display: flex;
		align-items: center;
		gap: var(--lq-space-3);
		flex-wrap: wrap;
		background: var(--lq-error-soft);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-2) var(--lq-space-3);
	}

	.ajr-error__msg {
		flex: 1;
		min-width: 200px;
		color: var(--lq-error);
		font-size: 0.85rem;
		margin: 0;
	}

	.ajr-btn-retry {
		background: transparent;
		color: var(--lq-error);
		border: 1px solid var(--lq-error-border, var(--lq-error));
		border-radius: var(--lq-radius);
		padding: var(--lq-space-1) var(--lq-space-3);
		font-weight: 500;
		font-size: 13px;
		cursor: pointer;
	}

	.ajr-btn-retry:hover {
		background: var(--lq-error);
		color: white;
	}

	.ajr-btn-retry:focus-visible {
		outline: 2px solid var(--lq-error);
		outline-offset: 2px;
	}

	/* ── Rendered markdown (also used by slot-less consumers) ── */

	.ajr-md {
		color: var(--lq-text-primary);
		font-size: 0.9rem;
		line-height: 1.65;
	}

	.ajr-md :global(h1),
	.ajr-md :global(h2),
	.ajr-md :global(h3),
	.ajr-md :global(h4) {
		color: var(--lq-text-primary);
		font-weight: 600;
		margin: var(--lq-space-3) 0 var(--lq-space-2);
	}

	.ajr-md :global(h1) {
		font-size: 1.1rem;
	}

	.ajr-md :global(h2) {
		font-size: 1rem;
	}

	.ajr-md :global(h3),
	.ajr-md :global(h4) {
		font-size: 0.9rem;
	}

	.ajr-md :global(p),
	.ajr-md :global(ul),
	.ajr-md :global(ol) {
		margin: 0 0 var(--lq-space-2);
	}

	.ajr-md :global(ul),
	.ajr-md :global(ol) {
		padding-left: 1.25rem;
	}

	.ajr-md :global(li) {
		margin-bottom: var(--lq-space-1);
	}

	.ajr-md :global(blockquote) {
		border-left: 3px solid var(--lq-border);
		margin: 0 0 var(--lq-space-2);
		padding-left: var(--lq-space-3);
		color: var(--lq-text-secondary);
	}

	.ajr-md :global(code) {
		font-family: var(--lq-font-mono, ui-monospace, monospace);
		font-size: 0.82em;
		background: var(--lq-inset);
		border-radius: var(--lq-radius-sm);
		padding: 0.05em 0.35em;
	}

	.ajr-md :global(.mgmt-src-marker) {
		font-family: var(--lq-font-mono, ui-monospace, monospace);
		font-size: 0.72em;
		color: var(--lq-text-tertiary);
		background: var(--lq-inset);
		border: 1px solid var(--lq-border);
		border-radius: var(--lq-radius-pill);
		padding: 0.05em 0.45em;
		white-space: nowrap;
		vertical-align: baseline;
	}
</style>
