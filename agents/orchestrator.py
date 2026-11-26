"""Master Orchestrator, task queue, progress, cancellation and retry engine."""

from __future__ import annotations

import json
from contextlib import closing
import queue
import threading
import traceback
from typing import Any, Callable, Optional

import engine
from agents.base import (
    AgentCancelledError,
    AgentConfigurationError,
    AgentContext,
    AgentExecutionError,
    AgentMetadata,
    AgentResult,
    BaseAgent,
)
from agents.ollama_client import OllamaClient, OllamaConfig, OllamaError
from agents.registry import build_default_agents
from agents.storage import ACTIVE_STATUSES, AgentStorage

DEFAULT_WORKFLOW_STEPS = (
    'catalog_agent',
    'catalogue_auditor',
    'reel_copy_strategist',
    'performance_analyst',
    'marketing_agent',
    'video_agent',
    'qa_agent',
    'social_agent',
)


class MasterOrchestratorAgent(BaseAgent):
    """Top-level coordinator agent that sequences enabled specialist agents."""

    metadata = AgentMetadata(
        agent_id='master_orchestrator',
        name='Master Orchestrator',
        role='orchestrator',
        description='Coordinates enabled specialist agents in a queued workflow to audit the catalogue, draft Telugu Reel hooks, and summarize campaign performance.',
        capabilities=('task_orchestration', 'workflow_planning', 'ollama_synthesis'),
        default_enabled=True,
        requires_ollama=True,
        can_publish=False,
        can_spend=False,
    )

    def __init__(self, orchestrator: 'MasterOrchestrator'):
        self._orchestrator = orchestrator

    def execute(self, ctx: AgentContext) -> AgentResult:
        if ctx.payload.get('product_promotion'):
            from agents.promotion_workflow import prepare_product_promotion
            return prepare_product_promotion(ctx, self._orchestrator.storage)
        requested = ctx.payload.get('steps')
        if requested is not None:
            if not isinstance(requested, list) or not all(isinstance(s, str) for s in requested):
                raise AgentConfigurationError('Workflow steps must be a list of agent IDs.')
            step_ids = list(dict.fromkeys(requested))
        else:
            step_ids = [aid for aid in DEFAULT_WORKFLOW_STEPS if aid in self._orchestrator.agents]

        configs = self._orchestrator.storage.get_agent_configs()
        enabled_steps = []
        skipped = []
        for aid in step_ids:
            if aid == self.metadata.agent_id:
                continue
            if aid not in self._orchestrator.agents:
                raise AgentConfigurationError(f'Unknown specialist agent "{aid}".')
            if not configs.get(aid, {}).get('enabled', True):
                skipped.append(aid)
                ctx.log('INFO', f'Skipping disabled agent: {aid}')
            else:
                enabled_steps.append(aid)

        if not enabled_steps:
            raise AgentConfigurationError('Enable at least one specialist agent before running the Master Orchestrator.')

        step_outputs: dict[str, Any] = {}
        total = len(enabled_steps)
        for idx, aid in enumerate(enabled_steps):
            ctx.check_cancelled()
            base_pct = 10 + int((idx / total) * 70)
            ctx.progress(base_pct, f'Running {aid} ({idx + 1}/{total})...')
            step_payload = dict(ctx.payload)
            if aid == 'video_agent' and 'marketing_agent' in step_outputs:
                step_payload['video_handoff'] = step_outputs['marketing_agent']['data'].get('video_handoff')
            if aid == 'qa_agent' and 'video_agent' in step_outputs:
                step_payload['qa_handoff'] = step_outputs['video_agent']['data'].get('qa_handoff')
            if aid == 'social_agent' and 'video_agent' in step_outputs:
                step_payload['production_id'] = (step_outputs['video_agent']['data'].get('qa_handoff') or {}).get('production_id', '')
            child_run = ctx.execute_child(aid, step_payload)
            if child_run['status'] == 'cancelled':
                raise AgentCancelledError(f'Cancelled while executing {aid}.')
            if child_run['status'] != 'succeeded':
                err = child_run.get('error_message') or f'Specialist agent {aid} failed.'
                raise AgentExecutionError(f'{aid} failed: {err}', retryable=False)
            step_outputs[aid] = {
                'run_id': child_run['id'],
                'summary': (child_run.get('output') or {}).get('summary', ''),
                'data': (child_run.get('output') or {}).get('data', {}),
            }

        ctx.check_cancelled()
        ctx.progress(85, f'Synthesizing unified plan with {ctx.model}...')
        prompt = (
            'You are the ProdXMart Master Orchestrator. Combine ONLY the real specialist outputs below into a short, '
            'actionable weekly promotion plan. Highlight: (1) immediate catalogue fixes, (2) recommended SKUs & Telugu Reel angles, '
            '(3) performance/tracking reminders. Note that publishing and spending are strictly manual and disabled here.\n\n'
            f'Specialist Outputs JSON:\n{json.dumps(step_outputs, ensure_ascii=False, indent=2)}'
        )
        llm = ctx.ollama.generate(
            prompt,
            system='You are a disciplined ecommerce workflow orchestrator. Stay grounded in the specialist outputs.',
            model=ctx.model,
            cancel_event=ctx.cancel_event,
        )
        ctx.progress(98, 'Completing orchestrated plan...')

        return AgentResult(
            summary=f'Orchestrated {len(enabled_steps)} specialist agent(s) for {ctx.mode}.',
            data={
                'executed_agents': enabled_steps,
                'skipped_agents': skipped,
                'steps': step_outputs,
                'executive_plan': llm['response'],
            },
            model_used=llm.get('model', ctx.model),
        )


class MasterOrchestrator:
    """Manages agent registration, task queue, worker execution, retries and cancellation."""

    def __init__(
        self,
        *,
        storage: Optional[AgentStorage] = None,
        specialists: Optional[list[BaseAgent]] = None,
        ollama_factory: Optional[Callable[[OllamaConfig], OllamaClient]] = None,
    ):
        self.storage = storage or AgentStorage()
        self.ollama_factory = ollama_factory or OllamaClient
        self._lock = threading.RLock()
        self._promotion_lock = threading.Lock()
        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_children: dict[str, set[str]] = {}
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        self.agents: dict[str, BaseAgent] = {}
        master_agent = MasterOrchestratorAgent(self)
        self.register(master_agent)
        for agent in (specialists if specialists is not None else build_default_agents(storage_provider=lambda: self.storage)):
            if getattr(agent, '_storage_provider', None) is None and hasattr(agent, '_storage_provider'):
                agent._storage_provider = lambda: self.storage
            self.register(agent)

        self.storage.ensure_defaults(list(self.agents.values()))

    def register(self, agent: BaseAgent) -> None:
        aid = agent.metadata.agent_id
        if aid in self.agents:
            raise ValueError(f'Duplicate agent_id: {aid}')
        self.agents[aid] = agent

    def start(self) -> None:
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self.storage.reconcile_interrupted_runs()
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name='ProdXMart-AgentWorker',
                daemon=True,
            )
            self._worker_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stop_event.set()
            for ev in self._cancel_events.values():
                ev.set()
            worker = self._worker_thread
            if worker is not None:
                self._queue.put(None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        with self._lock:
            self._worker_thread = None

    def _make_ollama_client(self, global_cfg: dict[str, Any], model: str) -> OllamaClient:
        return self.ollama_factory(
            OllamaConfig(
                base_url=global_cfg['ollama_base_url'],
                model=model,
                timeout_seconds=float(global_cfg['request_timeout_seconds']),
            )
        )

    def test_ollama_connection(self) -> dict[str, Any]:
        cfg = self.storage.get_global_config()
        client = self._make_ollama_client(cfg, cfg['default_model'])
        return client.test_connection(model=cfg['default_model'])

    def get_config(self) -> dict[str, Any]:
        return self.storage.get_global_config()

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        return self.storage.update_global_config(updates)

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        if agent_id not in self.agents:
            raise ValueError(f'Unknown agent: {agent_id}')
        return self.storage.set_agent_enabled(agent_id, enabled)

    def set_agent_model(self, agent_id: str, model_override: str) -> dict[str, Any]:
        if agent_id not in self.agents:
            raise ValueError(f'Unknown agent: {agent_id}')
        return self.storage.set_agent_model(agent_id, model_override)

    def enqueue_run(
        self,
        agent_id: str,
        *,
        mode: str = 'store',
        payload: Optional[dict[str, Any]] = None,
        trigger: str = 'manual',
        parent_run_id: str = '',
        retry_of_run_id: str = '',
    ) -> dict[str, Any]:
        engine.directory(mode)
        if agent_id not in self.agents:
            raise ValueError(f'Unknown agent: {agent_id}')
        agent = self.agents[agent_id]
        validated_payload = agent.validate_input(payload or {})

        global_cfg = self.storage.get_global_config()
        agent_cfgs = self.storage.get_agent_configs()
        acfg = agent_cfgs.get(agent_id, {'enabled': True, 'model_override': ''})
        if not acfg.get('enabled', True):
            raise AgentConfigurationError(f'{agent.metadata.name} is disabled. Enable it before running.')

        effective_model = (acfg.get('model_override') or '').strip() or global_cfg['default_model']
        run = self.storage.create_run(
            agent_id=agent_id,
            mode=mode,
            trigger=trigger,
            model=effective_model,
            max_retries=int(global_cfg['max_retries']),
            input_payload=validated_payload,
            parent_run_id=parent_run_id,
            retry_of_run_id=retry_of_run_id,
        )
        with self._lock:
            self._cancel_events[run['id']] = threading.Event()
        self.storage.add_log(run['id'], agent_id, 'INFO', f'Task queued ({trigger}) with model {effective_model}.')
        self._queue.put(run['id'])
        return run

    def enqueue_product_promotion(self, sku: str, *, language: str = 'en',
                                  duration_seconds: int = 30, platforms=None) -> dict[str, Any]:
        """Queue once per verified product/content configuration in the real store."""
        from agents.promotion_workflow import promotion_payload
        from agents.social_adapters import SUPPORTED_PLATFORMS
        with self._promotion_lock:
            payload = promotion_payload(sku, language, duration_seconds,
                                        list(SUPPORTED_PLATFORMS) if platforms is None else platforms)
            with engine.LOCK, closing(engine.db()) as con:
                previous = con.execute(
                    "SELECT id FROM agent_runs WHERE mode='store' AND agent_id='master_orchestrator' "
                    "AND json_extract(input_json, '$.promotion_key')=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (payload['promotion_key'],),
                ).fetchone()
            if previous:
                return {'run': self.storage.get_run(previous['id']), 'already_exists': True}
            run = self.enqueue_run('master_orchestrator', mode='store', payload=payload,
                                   trigger='product_saved')
            return {'run': run, 'already_exists': False}

    def stop_run(self, run_id: str) -> dict[str, Any]:
        run = self.storage.get_run(run_id)
        if not run:
            raise ValueError('Run not found.')
        if run['status'] not in ACTIVE_STATUSES:
            return run

        with self._lock:
            ev = self._cancel_events.get(run_id)
            if ev is not None:
                ev.set()
            child_ids = list(self._active_children.get(run_id, ()))

        updated = self.storage.mark_cancel_requested(run_id) or run
        self.storage.add_log(run_id, run['agent_id'], 'WARNING', 'Stop requested by user.')

        for cid in child_ids:
            try:
                self.stop_run(cid)
            except ValueError:
                pass
        return updated

    def retry_run(self, run_id: str) -> dict[str, Any]:
        prev = self.storage.get_run(run_id)
        if not prev:
            raise ValueError('Run not found.')
        if prev['status'] not in ('failed', 'cancelled'):
            raise ValueError('Only failed or cancelled runs can be retried.')
        return self.enqueue_run(
            prev['agent_id'],
            mode=prev['mode'],
            payload=prev.get('input') or {},
            trigger='retry',
            retry_of_run_id=prev['id'],
        )

    def get_state(self, mode: str = 'store', limit: int = 30) -> dict[str, Any]:
        global_cfg = self.storage.get_global_config()
        agent_cfgs = self.storage.get_agent_configs()
        latest_by_agent = self.storage.get_latest_runs_by_agent(mode=mode)
        runs = self.storage.list_runs(mode=mode, limit=limit)
        logs = self.storage.list_logs(limit=60)
        catalog_freshness = self.storage.get_catalog_freshness_summary(
            mode=mode,
            max_age_hours=engine.config()['maximum_catalogue_age_hours'],
        )
        staged = self.storage.get_staged_catalog_batch(mode)
        staged_summary = (
            {
                'source': staged['source'],
                'item_count': staged['item_count'],
                'pages_count': staged['pages_count'],
                'staged_at': staged['staged_at'],
            }
            if staged
            else None
        )
        marketing_campaigns = self.storage.list_marketing_campaigns(mode=mode, limit=10)
        video_handoff = self.storage.get_latest_handoff('video_agent', mode=mode)
        video_productions = self.storage.list_video_productions(mode=mode, limit=15)
        qa_reports = self.storage.list_qa_reports(mode=mode, limit=20)
        qa_handoff = self.storage.get_latest_handoff('qa_agent', mode=mode)
        from agents.credentials import SecureCredentialStore
        from agents.social_agent import reconcile_social_approvals
        reconcile_social_approvals(self.storage, mode)
        social_posts = self.storage.list_social_posts(mode=mode, limit=100)
        social_settings = self.storage.get_social_settings()
        social_credentials = SecureCredentialStore().list_status()

        cards = []
        for aid, agent in self.agents.items():
            meta = agent.metadata
            cfg = agent_cfgs.get(aid, {'enabled': True, 'model_override': ''})
            latest = latest_by_agent.get(aid)
            if not cfg.get('enabled', True) and (not latest or latest['status'] not in ACTIVE_STATUSES):
                display_status = 'disabled'
            elif latest:
                display_status = latest['status']
            else:
                display_status = 'idle'
            cards.append({
                'agent_id': meta.agent_id,
                'name': meta.name,
                'role': meta.role,
                'description': meta.description,
                'capabilities': list(meta.capabilities),
                'enabled': bool(cfg.get('enabled', True)),
                'model_override': cfg.get('model_override', ''),
                'effective_model': cfg.get('model_override') or global_cfg['default_model'],
                'requires_ollama': meta.requires_ollama,
                'can_publish': meta.can_publish,
                'can_spend': meta.can_spend,
                'status': display_status,
                'latest_run': latest,
            })

        return {
            'mode': mode,
            'config': global_cfg,
            'agents': cards,
            'runs': runs,
            'logs': logs,
            'active_count': sum(1 for r in runs if r['status'] in ACTIVE_STATUSES),
            'catalog_freshness': catalog_freshness,
            'staged_catalog': staged_summary,
            'marketing_campaigns': marketing_campaigns,
            'video_handoff': video_handoff,
            'video_productions': video_productions,
            'qa_reports': qa_reports,
            'qa_handoff': qa_handoff,
            'social_posts': social_posts,
            'social_settings': social_settings,
            'social_credentials': social_credentials,
        }

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                run_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if run_id is None:
                self._queue.task_done()
                break
            try:
                self._execute_run(run_id)
            except Exception as exc:
                self.storage.add_log(run_id, 'orchestrator', 'ERROR', f'Unexpected worker error: {exc}', traceback.format_exc())
            finally:
                self._queue.task_done()

    def _execute_child_inline(self, parent_run_id: str, parent_cancel: threading.Event, mode: str, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if parent_cancel.is_set():
            raise AgentCancelledError('Parent orchestrator run was stopped.')
        child = self.enqueue_child_sync_record(parent_run_id, mode, agent_id, payload)
        with self._lock:
            self._cancel_events[child['id']] = parent_cancel
            self._active_children.setdefault(parent_run_id, set()).add(child['id'])
        try:
            return self._execute_run(child['id'])
        finally:
            with self._lock:
                children = self._active_children.get(parent_run_id)
                if children is not None:
                    children.discard(child['id'])
                    if not children:
                        self._active_children.pop(parent_run_id, None)
                self._cancel_events.pop(child['id'], None)

    def enqueue_child_sync_record(self, parent_run_id: str, mode: str, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.agents[agent_id]
        validated_payload = agent.validate_input(payload)
        global_cfg = self.storage.get_global_config()
        agent_cfgs = self.storage.get_agent_configs()
        acfg = agent_cfgs.get(agent_id, {'enabled': True, 'model_override': ''})
        effective_model = (acfg.get('model_override') or '').strip() or global_cfg['default_model']
        run = self.storage.create_run(
            agent_id=agent_id,
            mode=mode,
            trigger='orchestrator',
            model=effective_model,
            max_retries=int(global_cfg['max_retries']),
            input_payload=validated_payload,
            parent_run_id=parent_run_id,
        )
        self.storage.add_log(run['id'], agent_id, 'INFO', f'Dispatched by Master Orchestrator ({parent_run_id}).')
        return run

    def _execute_run(self, run_id: str) -> dict[str, Any]:
        run = self.storage.get_run(run_id)
        if not run:
            raise ValueError(f'Missing run {run_id}')

        with self._lock:
            cancel_event = self._cancel_events.setdefault(run_id, threading.Event())

        if run['status'] == 'cancelled' or run['cancel_requested'] or cancel_event.is_set() or self._stop_event.is_set():
            finished = self.storage.update_run(
                run_id,
                status='cancelled',
                progress_message='Cancelled before execution',
                error_message=run.get('error_message') or 'Cancelled before execution started.',
                finished_at=run.get('finished_at') or engine.now().isoformat(),
            )
            self.storage.add_log(run_id, run['agent_id'], 'WARNING', 'Run skipped because cancellation was requested.')
            with self._lock:
                if not run.get('parent_run_id'):
                    self._cancel_events.pop(run_id, None)
            return finished

        agent_id = run['agent_id']
        agent = self.agents[agent_id]
        global_cfg = self.storage.get_global_config()
        max_retries = int(run['max_retries'])
        max_attempts = 1 + max_retries
        retry_delay = float(global_cfg['retry_delay_seconds'])

        self.storage.update_run(
            run_id,
            status='running',
            started_at=engine.now().isoformat(),
            progress=5,
            progress_message='Starting...',
        )
        self.storage.add_log(run_id, agent_id, 'INFO', f'Started execution in {run["mode"]} workspace.')

        try:
            for attempt in range(1, max_attempts + 1):
                if cancel_event.is_set() or self._stop_event.is_set():
                    raise AgentCancelledError('Execution stopped by user.')

                self.storage.update_run(run_id, attempt=attempt)
                effective_model = run['model'] or global_cfg['default_model']
                ollama = self._make_ollama_client(global_cfg, effective_model)

                def report_progress(pct: int, msg: str) -> None:
                    self.storage.update_run(run_id, progress=pct, progress_message=msg)

                def log_fn(level: str, msg: str, details: str = '') -> None:
                    self.storage.add_log(run_id, agent_id, level, msg, details)

                child_runner = (
                    (lambda cid, cpayload: self._execute_child_inline(run_id, cancel_event, run['mode'], cid, cpayload))
                    if agent.metadata.role == 'orchestrator'
                    else None
                )

                ctx = AgentContext(
                    run_id=run_id,
                    mode=run['mode'],
                    model=effective_model,
                    payload=run.get('input') or {},
                    ollama=ollama,
                    cancel_event=cancel_event,
                    report_progress=report_progress,
                    log=log_fn,
                    run_child=child_runner,
                )

                try:
                    result = agent.execute(ctx)
                    output_json = json.dumps(result.to_dict(), ensure_ascii=False)
                    done = self.storage.update_run(
                        run_id,
                        status='succeeded',
                        progress=100,
                        progress_message='Completed',
                        output_json=output_json,
                        model=result.model_used or effective_model,
                        error_message='',
                        error_traceback='',
                        finished_at=engine.now().isoformat(),
                    )
                    self.storage.add_log(run_id, agent_id, 'INFO', result.summary)
                    if (agent_id == 'catalog_agent' and run['mode'] == 'store'
                            and not run.get('parent_run_id')
                            and global_cfg.get('execution_mode') == 'auto_prepare'):
                        for sku in result.data.get('saved_skus', []):
                            try:
                                queued = self.enqueue_product_promotion(sku)
                                self.storage.add_log(run_id, agent_id, 'INFO',
                                    f"Promotion for {sku}: {queued['run']['id']}.")
                            except Exception as exc:
                                self.storage.add_log(run_id, agent_id, 'WARNING',
                                    f'Promotion not queued for {sku}: {exc}')
                    return done
                except AgentCancelledError as exc:
                    cancelled = self.storage.update_run(
                        run_id,
                        status='cancelled',
                        progress_message='Stopped by user',
                        error_message=str(exc),
                        finished_at=engine.now().isoformat(),
                    )
                    self.storage.add_log(run_id, agent_id, 'WARNING', str(exc))
                    return cancelled
                except Exception as exc:
                    tb = traceback.format_exc()
                    retryable = getattr(exc, 'retryable', True)
                    if isinstance(exc, AgentConfigurationError):
                        retryable = False

                    self.storage.add_log(
                        run_id,
                        agent_id,
                        'ERROR',
                        f'Attempt {attempt}/{max_attempts} failed: {exc}',
                        tb,
                    )
                    if retryable and attempt < max_attempts and not cancel_event.is_set() and not self._stop_event.is_set():
                        self.storage.update_run(
                            run_id,
                            progress_message=f'Attempt {attempt} failed; retrying ({attempt + 1}/{max_attempts})...',
                            error_message=str(exc)[:500],
                        )
                        if cancel_event.wait(timeout=retry_delay):
                            cancelled = self.storage.update_run(
                                run_id,
                                status='cancelled',
                                progress_message='Stopped during retry backoff',
                                error_message='Cancelled during retry wait.',
                                finished_at=engine.now().isoformat(),
                            )
                            self.storage.add_log(run_id, agent_id, 'WARNING', 'Cancelled during retry wait.')
                            return cancelled
                        continue

                    failed = self.storage.update_run(
                        run_id,
                        status='failed',
                        progress_message='Failed',
                        error_message=str(exc)[:500],
                        error_traceback=tb[:4000],
                        finished_at=engine.now().isoformat(),
                    )
                    return failed
        finally:
            with self._lock:
                if not run.get('parent_run_id'):
                    self._cancel_events.pop(run_id, None)


def build_default_orchestrator() -> MasterOrchestrator:
    """Create the default MasterOrchestrator instance."""
    return MasterOrchestrator()
