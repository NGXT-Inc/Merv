import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import {
  ADAPTERS,
  DEFAULT_WORKSPACE,
  capabilitiesFor,
  configFromDraft,
  configSignature,
  defaultPlatforms,
  draftFromSettings,
  nextCustomId,
  normalizeLocalPlatforms,
  validateDraft,
} from './agentPlatformConfig';

const DRAFT_KEY = 'rsui:agentPlatforms';
const WORKSPACE_KEY = 'rsui:agentWorkspace';
const LOCAL_RUNNER_URL = 'http://127.0.0.1:8791';

function readDraft() {
  if (typeof localStorage === 'undefined') return defaultPlatforms();
  try {
    const saved = JSON.parse(localStorage.getItem(DRAFT_KEY));
    return normalizeLocalPlatforms(saved);
  } catch {
    return defaultPlatforms();
  }
}

function readWorkspace() {
  if (typeof localStorage === 'undefined') return DEFAULT_WORKSPACE;
  try {
    const saved = JSON.parse(localStorage.getItem(WORKSPACE_KEY));
    return saved && typeof saved === 'object'
      ? {
        ...DEFAULT_WORKSPACE,
        repository: typeof saved.repository === 'string' ? saved.repository : '',
        root: saved.strategy === 'existing'
          ? ''
          : (typeof saved.root === 'string' ? saved.root : ''),
        base_ref: typeof saved.base_ref === 'string' ? saved.base_ref : 'HEAD',
        strategy: 'git_worktree',
      }
      : { ...DEFAULT_WORKSPACE };
  } catch {
    return { ...DEFAULT_WORKSPACE };
  }
}

export default function AgentPlatforms({ projectId }) {
  const [platforms, setPlatforms] = useState(readDraft);
  const [workspace, setWorkspace] = useState(readWorkspace);
  const [copied, setCopied] = useState('');
  const [sessions, setSessions] = useState(null);
  const [sessionError, setSessionError] = useState('');
  const [runnerUrl, setRunnerUrl] = useState(LOCAL_RUNNER_URL);
  const [pairingToken, setPairingToken] = useState('');
  const [runnerConnection, setRunnerConnection] = useState('idle');
  const [runnerMessage, setRunnerMessage] = useState('');
  const [runnerStatus, setRunnerStatus] = useState(null);
  const [machineBaseline, setMachineBaseline] = useState(null);
  const [dispatch, setDispatch] = useState(null);
  const [dispatchBusy, setDispatchBusy] = useState(false);
  const [dispatchError, setDispatchError] = useState('');
  const [halting, setHalting] = useState(false);
  const [showHaltPrompt, setShowHaltPrompt] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(platforms));
    } catch {
      // A private browser may disable storage; the in-memory draft still works.
    }
  }, [platforms]);

  useEffect(() => {
    try {
      localStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspace));
    } catch {
      // The visible draft remains usable when browser storage is disabled.
    }
  }, [workspace]);

  useEffect(() => {
    if (!projectId) return undefined;
    let disposed = false;
    async function load() {
      try {
        const response = await api.listAgentSessions(projectId);
        if (!disposed) {
          setSessions(response?.sessions || []);
          setSessionError('');
        }
      } catch {
        if (!disposed) setSessionError('Session status is unavailable.');
      }
    }
    load();
    const timer = setInterval(load, 15_000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return undefined;
    let disposed = false;
    api.getProject(projectId)
      .then((project) => {
        if (!disposed) setDispatch(Boolean(project?.settings?.agent_dispatch));
      })
      .catch(() => {
        if (!disposed) setDispatchError('Dispatch setting is unavailable.');
      });
    return () => { disposed = true; };
  }, [projectId]);

  const draftConfig = useMemo(
    () => configFromDraft(platforms, workspace),
    [platforms, workspace],
  );
  const config = useMemo(() => JSON.stringify(draftConfig, null, 2), [draftConfig]);
  const signature = useMemo(() => configSignature(draftConfig), [draftConfig]);
  const validation = useMemo(
    () => validateDraft(platforms, workspace),
    [platforms, workspace],
  );
  const dirty = machineBaseline !== null && signature !== machineBaseline;
  const runCommand = `merv-agent-runner --project ${projectId || 'PROJECT_ID'}`;
  const liveSessions = useMemo(
    () => (sessions || []).filter(
      (session) => session.status === 'offered' || session.status === 'active',
    ),
    [sessions],
  );

  function update(id, patch) {
    setPlatforms((current) => current.map((platform) => (
      platform.id === id ? { ...platform, ...patch, present: true } : platform
    )));
  }

  function addCommandAgent() {
    const id = nextCustomId(platforms);
    setPlatforms((current) => [...current, {
      id,
      name: id,
      adapter: 'command',
      command: [],
      model: '',
      effort: '',
      parallelism: 1,
      enabled: false,
      present: true,
      custom: true,
      commandWasString: false,
    }]);
  }

  async function toggleDispatch(next) {
    setDispatchBusy(true);
    setDispatchError('');
    try {
      const project = await api.patchProject(projectId, { agent_dispatch: next });
      setDispatch(Boolean(project?.settings?.agent_dispatch ?? next));
      // Turning dispatch off only stops new claims, so offer the separate stop
      // for whatever is already running.
      setShowHaltPrompt(!next);
    } catch (err) {
      setDispatchError(err?.message || 'Could not change the dispatch setting.');
    } finally {
      setDispatchBusy(false);
    }
  }

  async function haltSessions() {
    setHalting(true);
    setDispatchError('');
    try {
      const response = await api.haltAgentSessions(projectId);
      setSessions(response?.sessions || []);
      setShowHaltPrompt(false);
    } catch (err) {
      setDispatchError(err?.message || 'Could not stop the running sessions.');
    } finally {
      setHalting(false);
    }
  }

  function invalidateConnection() {
    setRunnerConnection('idle');
    setRunnerStatus(null);
    setMachineBaseline(null);
    setRunnerMessage('');
  }

  async function copy(label, value) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      setTimeout(() => setCopied(''), 1800);
    } catch {
      setCopied('');
    }
  }

  async function localRunnerRequest(method, path = '/settings') {
    const base = runnerUrl.trim().replace(/\/+$/, '');
    const target = new URL(base);
    if (
      !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname)
      || !['http:', 'https:'].includes(target.protocol)
      || target.username
      || target.password
    ) {
      throw new Error('Runner URL must be an explicit loopback HTTP address.');
    }
    const response = await fetch(`${base}${path}`, {
      method,
      credentials: 'omit',
      headers: {
        Authorization: `Bearer ${pairingToken.trim()}`,
        ...(method === 'PUT' ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(method === 'PUT' ? { body: JSON.stringify(draftConfig) } : {}),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(body?.error || body?.detail || body?.message || `Runner returned HTTP ${response.status}`);
    }
    return body;
  }

  async function connectRunner() {
    if (!pairingToken.trim()) {
      setRunnerMessage('Enter the pairing token printed by the local runner.');
      return;
    }
    setRunnerConnection('connecting');
    setRunnerMessage('');
    try {
      const status = await localRunnerRequest('GET', '/status');
      const settings = await localRunnerRequest('GET', '/settings');
      const hydrated = draftFromSettings(settings);
      const hydratedConfig = configFromDraft(hydrated.platforms, hydrated.workspace);
      setPlatforms(hydrated.platforms);
      setWorkspace(hydrated.workspace);
      setMachineBaseline(configSignature(hydratedConfig));
      setRunnerStatus(status);
      setRunnerConnection('connected');
      setRunnerMessage('Connected and loaded this machine’s settings.');
    } catch (error) {
      setRunnerConnection('idle');
      setRunnerStatus(null);
      setMachineBaseline(null);
      setRunnerMessage(error?.message || 'Could not connect to the local runner.');
    }
  }

  async function applyRunnerSettings() {
    if (runnerConnection !== 'connected' || machineBaseline === null || !dirty || !validation.valid) {
      return;
    }
    setRunnerConnection('applying');
    setRunnerMessage('');
    try {
      const response = await localRunnerRequest('PUT');
      const hydrated = draftFromSettings(response);
      const hydratedConfig = configFromDraft(hydrated.platforms, hydrated.workspace);
      setPlatforms(hydrated.platforms);
      setWorkspace(hydrated.workspace);
      setMachineBaseline(configSignature(hydratedConfig));
      setRunnerConnection('connected');
      setRunnerMessage(response?.restart_required
        ? 'Saved to the runner machine. Restart the runner to use these settings.'
        : 'Saved to the runner machine.');
    } catch (error) {
      setRunnerConnection('connected');
      setRunnerMessage(error?.message || 'Could not save runner settings.');
    }
  }

  return (
    <>
      <div className="settings-panel-head">
        <p className="settings-summary">
          Merv can run this project’s experiments, reviews, and consolidations
          in local coding-agent sessions. Enabled agents run unattended with
          your machine account’s filesystem and network permissions; worktrees
          isolate Git changes, not operating-system access.
        </p>
        <button type="button" className="btn btn--ghost" onClick={addCommandAgent}>
          Add command agent
        </button>
      </div>

      <div className="agent-dispatch">
        <div>
          <strong>Automatic dispatch</strong>
          <p>
            While this is on, any runner started for this project claims its
            experiments, reviews, and consolidations as soon as they are
            available. While it is off, nothing is dispatched and you drive
            agents yourself.
          </p>
        </div>
        <div className="agent-dispatch-control">
          <label className="agent-dispatch-toggle">
            <input
              type="checkbox"
              checked={dispatch === true}
              disabled={dispatch === null || dispatchBusy || !projectId}
              onChange={(event) => toggleDispatch(event.target.checked)}
            />
            <span>
              {dispatch === null ? 'Loading…' : dispatch ? 'On' : 'Off'}
            </span>
          </label>
        </div>
      </div>

      {showHaltPrompt && liveSessions.length > 0 && (
        <div className="agent-dispatch-halt" role="status">
          <p>
            {liveSessions.length === 1
              ? '1 session is still running.'
              : `${liveSessions.length} sessions are still running.`}
            {' '}
            Turning dispatch off stops new work only. Stop these now to end
            their agent processes; their committed work is kept.
          </p>
          <div className="page-actions">
            <button
              type="button"
              className="btn btn--primary"
              onClick={haltSessions}
              disabled={halting}
            >
              {halting ? 'Stopping…' : 'Stop them now'}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => setShowHaltPrompt(false)}
              disabled={halting}
            >
              Let them finish
            </button>
          </div>
        </div>
      )}

      {dispatchError && (
        <p className="agent-session-note" role="alert">{dispatchError}</p>
      )}

      <div className="agent-workspace">
        <div>
          <strong>Workspace isolation</strong>
          <p>
            Every experiment receives its own persistent Git worktree so
            parallel agents never edit the same checkout.
          </p>
        </div>
        <div className="agent-workspace-fields">
          <label>
            <span>Repository</span>
            <input
              className="auth-input mono"
              value={workspace.repository}
              placeholder="/absolute/path/to/repository"
              onChange={(event) => setWorkspace((current) => ({
                ...current,
                repository: event.target.value,
              }))}
            />
            {validation.workspace.repository && (
              <small className="field-error">{validation.workspace.repository}</small>
            )}
          </label>
          <label>
            <span>Worktree root</span>
            <input
              className="auth-input mono"
              value={workspace.root}
              placeholder="/absolute/path/to/worktrees"
              onChange={(event) => setWorkspace((current) => ({
                ...current,
                root: event.target.value,
              }))}
            />
            {validation.workspace.root && (
              <small className="field-error">{validation.workspace.root}</small>
            )}
          </label>
          <label>
            <span>Base ref</span>
            <input
              className="auth-input mono"
              value={workspace.base_ref}
              onChange={(event) => setWorkspace((current) => ({
                ...current,
                base_ref: event.target.value,
              }))}
            />
            {validation.workspace.base_ref && (
              <small className="field-error">{validation.workspace.base_ref}</small>
            )}
          </label>
        </div>
      </div>

      <div className="agent-platform-list">
        {platforms.map((platform) => {
          const capabilities = capabilitiesFor(platform.adapter);
          const errors = validation.platforms[platform.id] || {};
          return (
            <article className="agent-platform" key={platform.id}>
            <div className="agent-platform-head">
              <label className="agent-platform-switch">
                <input
                  type="checkbox"
                  checked={platform.enabled}
                  onChange={(event) => update(platform.id, { enabled: event.target.checked })}
                />
                <span>
                  <strong>{platform.name}</strong>
                  <small>
                    {platform.custom
                      ? `${platform.adapter} adapter · ${platform.id}`
                      : 'Native adapter'}
                  </small>
                </span>
              </label>
              {platform.custom && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setPlatforms((current) => current.filter((item) => item.id !== platform.id))}
                >
                  Remove
                </button>
              )}
            </div>

            <div className="agent-platform-fields">
              {(platform.custom || errors.adapter) && (
                <label>
                  <span>Adapter</span>
                  <select
                    className="auth-input"
                    value={platform.adapter}
                    onChange={(event) => update(platform.id, { adapter: event.target.value })}
                  >
                    {ADAPTERS.map((adapter) => (
                      <option key={adapter} value={adapter}>{adapter}</option>
                    ))}
                  </select>
                  {errors.adapter && <small className="field-error">{errors.adapter}</small>}
                </label>
              )}
              <label className="agent-command-field">
                <span>Command arguments · one per line</span>
                <textarea
                  className="auth-input mono"
                  rows="2"
                  value={platform.command.join('\n')}
                  placeholder={'agent-executable\n--optional-flag'}
                  onChange={(event) => update(platform.id, {
                    command: event.target.value ? event.target.value.split('\n') : [],
                    commandWasString: false,
                  })}
                />
                {errors.command && <small className="field-error">{errors.command}</small>}
              </label>
              {capabilities.model && (
                <label>
                  <span>Model</span>
                  <input
                    className="auth-input"
                    value={platform.model}
                    placeholder="Platform default"
                    onChange={(event) => update(platform.id, { model: event.target.value })}
                  />
                </label>
              )}
              {capabilities.effort && (
                <label>
                  <span>Effort</span>
                  <input
                    className="auth-input"
                    value={platform.effort}
                    placeholder="Platform default"
                    onChange={(event) => update(platform.id, { effort: event.target.value })}
                  />
                </label>
              )}
              <label>
                <span>Parallel experiments</span>
                <input
                  className="auth-input"
                  type="number"
                  min="1"
                  max="32"
                  value={platform.parallelism}
                  onChange={(event) => update(platform.id, { parallelism: event.target.value })}
                />
                {errors.parallelism && (
                  <small className="field-error">{errors.parallelism}</small>
                )}
              </label>
            </div>
          </article>
          );
        })}
      </div>

      <div className="runner-setup">
        <div className="runner-setup-head">
          <div>
            <strong>Apply on the runner machine</strong>
            <p>
              Start <code>merv-agent-runner --settings-only</code>, then enter
              the pairing token it prints. Use
              <code> --show-pairing-token</code> to retrieve it later. The
              token stays in this tab.
            </p>
          </div>
        </div>
        <div className="runner-pairing">
          <label>
            <span>Local runner URL</span>
            <input
              className="auth-input mono"
              value={runnerUrl}
              onChange={(event) => {
                setRunnerUrl(event.target.value);
                invalidateConnection();
              }}
            />
          </label>
          <label>
            <span>Pairing token</span>
            <input
              className="auth-input mono"
              type="password"
              autoComplete="off"
              value={pairingToken}
              placeholder="Paste from the runner terminal"
              onChange={(event) => {
                setPairingToken(event.target.value);
                invalidateConnection();
              }}
            />
          </label>
          <button
            type="button"
            className="btn btn--ghost"
            disabled={runnerConnection === 'connecting' || runnerConnection === 'applying'}
            onClick={connectRunner}
          >
            {runnerConnection === 'connecting' ? 'Connecting…' : 'Connect'}
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={
              runnerConnection !== 'connected'
              || machineBaseline === null
              || !dirty
              || !validation.valid
            }
            onClick={applyRunnerSettings}
          >
            {runnerConnection === 'applying' ? 'Applying…' : 'Apply settings'}
          </button>
        </div>
        {runnerMessage && (
          <p className="runner-pairing-status" role="status">{runnerMessage}</p>
        )}
        {!validation.valid && (
          <div className="runner-validation" role="alert">
            <strong>Fix the draft before applying or copying it.</strong>
            <ul>
              {[...new Set(validation.messages)].map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="runner-machine-status">
          <div>
            <strong>This machine</strong>
            <p>The loopback service reports only the runner on this computer.</p>
          </div>
          <span className="agent-session-note">
            {runnerConnection === 'idle' && 'Not connected'}
            {runnerConnection === 'connecting' && 'Connecting…'}
            {runnerConnection === 'applying' && 'Saving settings…'}
            {runnerConnection === 'connected' && (
              runnerStatus?.runner_active
                ? `Runner active · ${runnerStatus.project_id || 'project unknown'}`
                : 'Settings service active · runner stopped'
            )}
            {runnerConnection === 'connected' && machineBaseline !== null && (
              dirty ? ' · unsaved changes' : ' · settings current'
            )}
          </span>
        </div>

        <details className="runner-manual">
          <summary>Advanced · manual configuration</summary>
          <div className="runner-manual-head">
            <div>
              <strong>Manual fallback</strong>
              <p>
                If the loopback service is unavailable, merge this draft into
                <code> ~/.merv/client.json</code> on the runner machine.
              </p>
            </div>
            <button
              type="button"
              className="btn btn--sm"
              disabled={!validation.valid}
              onClick={() => copy('config', config)}
            >
              {copied === 'config' ? 'Copied' : 'Copy config'}
            </button>
          </div>
          <pre className="runner-config mono"><code>{config}</code></pre>
          <p className="runner-instruction">
            The settings endpoint and manual copy update only
            <code> agent_workspace</code> and <code>agent_platforms</code>;
            existing server configuration is preserved. Each command line is
            one exact argument; shell expansion is never used.
          </p>
        </details>
        <div className="runner-command">
          <code className="mono">{runCommand}</code>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => copy('run', runCommand)}>
            {copied === 'run' ? 'Copied' : 'Copy'}
          </button>
        </div>
        <p className="runner-instruction">
          Run this command on that machine to begin claiming this project’s
          dispatchable experiments. Keep the process running.
        </p>
      </div>

      <div className="agent-session-status">
        <div>
          <strong>Project workers</strong>
          <p>
            These sessions come from Merv. Keep one runner machine per project
            so every experiment branch shares the same central repository.
          </p>
          {liveSessions.length > 0 && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={haltSessions}
              disabled={halting}
            >
              {halting ? 'Stopping…' : `Stop ${liveSessions.length} running`}
            </button>
          )}
        </div>
        {sessionError ? (
          <span className="agent-session-note">{sessionError}</span>
        ) : sessions === null ? (
          <span className="agent-session-note">Loading…</span>
        ) : sessions.length === 0 ? (
          <span className="agent-session-note">No sessions yet.</span>
        ) : (
          <div className="agent-session-list">
            {sessions.slice(0, 8).map((session) => (
              <div className="agent-session-row" key={session.id}>
                <span>
                  <strong>{session.platform}</strong>
                  <small className="mono">{session.experiment_id}</small>
                  {session.workspace_ref && (
                    <small className="mono">{session.workspace_ref}</small>
                  )}
                </span>
                <span className={`mcpk-state mcpk-state--${session.status}`}>
                  {session.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
