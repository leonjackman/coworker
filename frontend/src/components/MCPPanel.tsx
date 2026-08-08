import { ChevronDown, Loader2, Network, Plus, Save, Search, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { McpServerEntry, McpTemplateEntry } from '../types';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './ui/dropdown-menu';
import { Switch } from './ui/switch';
import { WorkspacePage } from './ui/workspace-page';

// Matches backend mcp.py SECRET_PLACEHOLDER. Carried back on save so stored
// secrets survive an edit without the UI ever seeing the raw value.
const SECRET_PLACEHOLDER = '__CW_SECRET_KEPT__';

type ViewMode = 'list' | 'form' | 'catalog';

type FormState = {
  id: string | null;
  name: string;
  transport: string;
  command: string;
  args: string;
  url: string;
  envPairs: { key: string; value: string }[];
};

const emptyForm = (): FormState => ({
  id: null,
  name: '',
  transport: 'stdio',
  command: '',
  args: '',
  url: '',
  envPairs: [],
});

interface MCPPanelProps {
  onMcpChange?: () => void;
}

export function MCPPanel({ onMcpChange }: MCPPanelProps) {
  const [servers, setServers] = useState<McpServerEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; latency_ms: number | null; error?: string; tool_count?: number } | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [form, setForm] = useState<FormState>(emptyForm());
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [templates, setTemplates] = useState<McpTemplateEntry[]>([]);
  const [search, setSearch] = useState('');
  const [catalogSearch, setCatalogSearch] = useState('');
  const [catalogCategory, setCatalogCategory] = useState('all');

  const load = useCallback(async () => {
    setLoading(true);
    setMessage(null);
    try {
      const response = await chatService.listMcps();
      setServers(response.servers);
    } catch (error) {
      setMessage(translateError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    chatService.discoverMcps().then((res) => setTemplates(res.servers ?? [])).catch(() => {});
  }, [load]);

  const filteredServers = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return servers;
    return servers.filter((s) => s.name.toLowerCase().includes(q));
  }, [servers, search]);

  const catalogFiltered = useMemo(() => {
    const q = catalogSearch.trim().toLowerCase();
    let list = templates;
    if (catalogCategory !== 'all') {
      list = list.filter((t) => (t.category || '').toLowerCase() === catalogCategory);
    }
    if (q) {
      list = list.filter((t) => t.name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q));
    }
    return list;
  }, [templates, catalogSearch, catalogCategory]);

  function selectTemplate(template: McpTemplateEntry) {
    setForm({
      id: null,
      name: template.name,
      transport: template.transport,
      command: template.command || '',
      args: template.args || '',
      url: template.url || '',
      envPairs: Object.entries({ ...(template.env || {}) }).map(([k, v]) => ({ key: k, value: v })),
    });
    setViewMode('form');
  }

  function startAdd() {
    setForm(emptyForm());
    setTestResult(null);
    setViewMode('form');
  }

  function startEdit(server: McpServerEntry) {
    // Carry the masked env values as-is: secrets arrive as SECRET_PLACEHOLDER
    // and round-trip unchanged, so saving never wipes an existing API key.
    const envPairs = Object.entries(server.env || {}).map(([k, v]) => ({ key: k, value: v }));
    setForm({
      id: server.id,
      name: server.name,
      transport: server.transport,
      command: server.command || '',
      args: server.args || '',
      url: server.url || '',
      envPairs,
    });
    setTestResult(null);
    setViewMode('form');
  }

  function cancelForm() {
    setViewMode('list');
    setForm(emptyForm());
    setTestResult(null);
    setDeleteConfirm(null);
  }

  async function handleTest() {
    const env = form.envPairs.filter((p) => p.key.trim()).reduce<Record<string, string>>((acc, p) => {
      acc[p.key.trim()] = p.value;
      return acc;
    }, {});
    setTesting(true);
    setTestResult(null);
    setMessage(null);
    try {
      const result = await chatService.testMcp({
        transport: form.transport,
        command: form.transport === 'stdio' ? form.command : undefined,
        args: form.transport === 'stdio' ? form.args : undefined,
        url: ['http', 'sse'].includes(form.transport) ? form.url : undefined,
        env,
        // On edit, let the backend resolve masked secrets to the real values.
        server_id: form.id ?? undefined,
      });
      setTestResult(result);
    } catch (error) {
      setTestResult({ ok: false, latency_ms: null, error: translateError(error) });
    } finally {
      setTesting(false);
    }
  }

  function addEnvPair() {
    setForm((prev) => ({ ...prev, envPairs: [...prev.envPairs, { key: '', value: '' }] }));
  }

  function removeEnvPair(index: number) {
    setForm((prev) => ({ ...prev, envPairs: prev.envPairs.filter((_, i) => i !== index) }));
  }

  function updateEnvPair(index: number, field: 'key' | 'value', value: string) {
    setForm((prev) => {
      const next = [...prev.envPairs];
      next[index] = { ...next[index], [field]: value };
      return { ...prev, envPairs: next };
    });
  }

  async function refreshOne(id: string) {
    try {
      const updated = await chatService.checkMcp(id);
      setServers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch {
      // Best-effort: a failed re-check must not break the save flow.
    }
  }

  async function handleSave() {
    const canSave =
      !!form.name.trim() &&
      (form.transport === 'stdio' ? !!form.command.trim() : !!form.url.trim());
    if (!canSave) return;
    setSaving(true);
    setMessage(null);

    const env = form.envPairs.filter((p) => p.key.trim()).reduce<Record<string, string>>((acc, p) => {
      acc[p.key.trim()] = p.value;
      return acc;
    }, {});

    const payload: Record<string, unknown> = {
      name: form.name.trim(),
      transport: form.transport,
    };
    if (form.transport === 'stdio') {
      payload.command = form.command;
      payload.args = form.args;
    } else {
      payload.url = form.url;
    }
    if (Object.keys(env).length) payload.env = env;

    try {
      const saved = form.id
        ? await chatService.updateMcp(form.id, payload as never)
        : await chatService.createMcp(payload as never);
      await load();
      onMcpChange?.();
      setViewMode('list');
      // Fire a real connection check in the background for accurate status.
      void refreshOne(saved.id);
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(serverId: string) {
    setSaving(true);
    try {
      await chatService.deleteMcp(serverId);
      await load();
      onMcpChange?.();
      setDeleteConfirm(null);
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(server: McpServerEntry) {
    const next = !server.enabled;
    // Optimistic update so the switch never flickers. Revert on failure.
    const prevStatus = server.status;
    setServers((prev) =>
      prev.map((s) =>
        s.id === server.id
          ? { ...s, enabled: next, status: next ? (s.status === 'disabled' ? 'unknown' : s.status) : 'disabled' }
          : s,
      ),
    );
    try {
      await chatService.updateMcp(server.id, { enabled: next });
      onMcpChange?.();
      // After enabling, re-check the connection so the status reflects reality
      // instead of lingering on a neutral "unknown" until the next manual check.
      if (next) void refreshOne(server.id);
    } catch (error) {
      setServers((prev) =>
        prev.map((s) => (s.id === server.id ? { ...s, enabled: server.enabled, status: prevStatus } : s)),
      );
      setMessage(translateError(error) || t('common.operation_failed'));
    }
  }

  async function handleCheckAll() {
    setChecking(true);
    setMessage(null);
    try {
      const res = await chatService.checkAllMcps();
      setServers(res.servers);
      onMcpChange?.();
    } catch (error) {
      setMessage(translateError(error));
    } finally {
      setChecking(false);
    }
  }

  function statusDot(status: string): string {
    switch (status) {
      case 'connected': return 'mcp-status-connected';
      case 'error_connecting': return 'mcp-status-error';
      case 'needs_auth': return 'mcp-status-auth';
      case 'disabled': return 'mcp-status-disabled';
      case 'unknown': return 'mcp-status-unknown';
      default: return 'mcp-status-connected';
    }
  }

  function formatStatus(status: string): string {
    const labels: Record<string, string> = {
      connected: t('mcp.connected'),
      error_connecting: t('mcp.error'),
      needs_auth: t('mcp.needs_auth'),
      disabled: t('mcp.disabled'),
      connecting: t('mcp.connecting'),
      unknown: t('mcp.unknown'),
    };
    return labels[status] || status;
  }

  const hasTransportFields = form.transport === 'stdio';
  const isRemoteTransport = ['http', 'sse'].includes(form.transport);
  const canSave =
    !!form.name.trim() && (form.transport === 'stdio' ? !!form.command.trim() : !!form.url.trim());

  // ========== LIST VIEW (aligned with provider list structure) ==========
  if (viewMode === 'list') {
    return (
      <WorkspacePage
        eyebrow={t('mcp.title')}
        title="模型上下文协议"
        description={t('mcp.description')}
        action={
          <Button variant="secondary" onClick={() => setViewMode('catalog')}>
            更多 ▾
          </Button>
        }
      >
        <div className="workspace-page__content">
          {/* Quick-add card (aligned with provider-quick-card) */}
          {templates.length > 0 && (
            <div className="mcp-quick-card">
              <Network size={18} className="mcp-form-card__icon" />
              <div>
                <strong>{t('mcp.quick_add')}</strong>
                <p>{t('mcp.quick_add_desc')}</p>
                <div className="mcp-template-row">
                  {templates.map((template) => (
                    <button key={template.id} type="button" className="mcp-template-pill" onClick={() => selectTemplate(template)}>
                      {template.icon || <Network size={12} />}
                      {template.name}
                    </button>
                  ))}
                  <button type="button" className="mcp-template-pill mcp-template-pill--dashed" onClick={startAdd}>
                    <Plus size={12} />
                    {t('mcp.custom_extension')}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* List heading (aligned with provider-list-heading) */}
          <div className="mcp-list-heading">
            <h2>{t('mcp.my_extensions')} ({servers.length})</h2>
            <Button variant="primary" onClick={startAdd}>
              <Plus size={14} />
              {t('mcp.add_server')}
            </Button>
          </div>

          {/* Server list (aligned with provider-list / provider-card--mir) */}
          {loading ? (
            <div className="mcp-empty">{t('common.loading')}</div>
          ) : servers.length === 0 ? (
            <div className="mcp-empty">
              <p>{t('mcp.empty')}</p>
              <span>{t('mcp.empty_hint')}</span>
            </div>
          ) : filteredServers.length === 0 ? (
            <div className="mcp-empty">
              <p>{t('mcp.no_match')}</p>
            </div>
          ) : (
            <div className="mcp-list">
              {filteredServers.map((server) => (
                <article
                  className={`mcp-card ${!server.enabled || server.status === 'disabled' ? 'mcp-card--disabled' : ''}`}
                  key={server.id}
                  onClick={() => {
                    startEdit(server);
                    setViewMode('form');
                  }}
                >
                  <div className="mcp-card__top">
                    <div className="mcp-card__identity">
                      <span className={`mcp-status-dot ${statusDot(server.status)}`} />
                      <strong>{server.name}</strong>
                      <Badge className="mcp-transport-badge">{server.transport}</Badge>
                      {!server.enabled || server.status === 'disabled' ? <Badge>{t('mcp.disabled')}</Badge> : null}
                    </div>
                    <div className="mcp-card__controls" onClick={(e) => e.stopPropagation()}>
                      <Switch
                        id={`mcp-switch-${server.id}`}
                        checked={server.enabled}
                        onChange={() => handleToggle(server)}
                      />
                    </div>
                  </div>

                  <div className="mcp-card__meta">
                    <span className="mcp-card__subtitle">
                      {t('mcp.tool_count').replace('{count}', String(server.tool_count || 0))}
                      {server.error_message && <span className="mcp-error-msg">{server.error_message}</span>}
                    </span>
                  </div>
                </article>
              ))}
            </div>
          )}

          {message && <p className="mcp-message">{message}</p>}
        </div>
      </WorkspacePage>
    );
  }

  // ========== CATALOG VIEW (service directory, "更多" entry) ==========
  if (viewMode === 'catalog') {
    return (
      <WorkspacePage
        eyebrow={t('mcp.title')}
        title="全部支持的 MCP 服务"
        description="浏览所有可添加的 MCP 服务，选择一个服务即可快速配置。"
        action={
          <Button variant="ghost" onClick={() => setViewMode('list')}>
            {t('mcp.back')}
          </Button>
        }
      >
        <div className="workspace-page__content">
          {/* Search + categories */}
          <div style={{ marginBottom: 16 }}>
            <input
              type="text"
              className="mcp-catalog-search"
              value={catalogSearch}
              onChange={(event) => setCatalogSearch(event.target.value)}
              placeholder="搜索服务..."
            />
          </div>

          {/* Category tabs (placeholder - templates don't have categories yet) */}
          <div className="mcp-catalog-categories">
            <button
              className={`mcp-catalog-tab ${catalogCategory === 'all' ? 'mcp-catalog-tab--active' : ''}`}
              onClick={() => setCatalogCategory('all')}
            >
              全部
            </button>
            {/* TODO: render actual categories from template metadata when available */}
          </div>

          {/* Service card grid */}
          <div className="mcp-catalog-grid">
            {catalogFiltered.map((template) => (
              <div
                className="mcp-catalog-card"
                key={template.id}
                onClick={() => selectTemplate(template)}
              >
                <div className="mcp-catalog-card__icon">{template.icon || '📦'}</div>
                <div className="mcp-catalog-card__name">{template.name}</div>
                <div className="mcp-catalog-card__desc">{template.description || ''}</div>
                <div className="mcp-catalog-card__tags">
                  <Badge className="mcp-transport-badge">{template.transport}</Badge>
                </div>
              </div>
            ))}
          </div>
        </div>
      </WorkspacePage>
    );
  }

  // ========== FORM VIEW (server detail/edit form, aligned with provider form) ==========
  return (
    <WorkspacePage
      className="mcp-shell--form"
      eyebrow={t('mcp.title')}
      title={form.id ? t('mcp.edit_server') : t('mcp.add_title')}
      action={
        <Button variant="ghost" onClick={cancelForm}>
          {t('mcp.back')}
        </Button>
      }
    >
      <div className="mcp-form-card">
        <Network size={18} className="mcp-form-card__icon" />
        <div className="mcp-form-card__body">
          {/* Connection status row (only on edit) */}
          {form.id && (
            <div className="mcp-status-row">
              <span className={`mcp-status-dot ${statusDot(servers.find((s) => s.id === form.id)?.status || 'unknown')}`} style={{ width: 12, height: 12 }} />
              <Badge className={servers.find((s) => s.id === form.id)?.status === 'connected' ? 'mcp-status-badge--ok' : ''}>
                {formatStatus(servers.find((s) => s.id === form.id)?.status || 'unknown')}
              </Badge>
              <Button
                variant="secondary"
                onClick={handleTest}
                disabled={testing || (hasTransportFields && !form.command.trim()) || (isRemoteTransport && !form.url.trim())}
                className="mcp-test-btn"
              >
                {testing ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Network size={14} />
                )}
                {t('mcp.test_connection')}
              </Button>
              {testResult && (
                <span className={`mcp-test-result ${testResult.ok ? 'mcp-test-result--ok' : 'mcp-test-result--error'}`}>
                  {testResult.ok
                    ? t('mcp.test_success').replace('{latency_ms}', String(testResult.latency_ms)).replace('{tool_count}', String(testResult.tool_count || 0))
                    : `${t('mcp.test_failed')}: ${testResult.error || ''}`}
                </span>
              )}
            </div>
          )}

          <h2>{form.id ? t('mcp.edit_server') : t('mcp.add_title')}</h2>

          <label className="field">
            <span>{t('mcp.name')}</span>
            <input
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder={t('mcp.name_placeholder')}
              disabled={saving}
            />
          </label>

          <div className="field">
            <span>{t('mcp.transport')}</span>
            <div className="mcp-transport-row" role="group" aria-label={t('mcp.transport')}>
              <button
                type="button"
                className={`mcp-transport-pill ${form.transport === 'stdio' ? 'mcp-transport-pill--active' : ''}`}
                aria-pressed={form.transport === 'stdio'}
                onClick={() => setForm({ ...form, transport: 'stdio', command: '', args: '' })}
                disabled={saving}
              >
                {t('mcp.transport_stdio')}
              </button>
              <button
                type="button"
                className={`mcp-transport-pill ${form.transport === 'http' ? 'mcp-transport-pill--active' : ''}`}
                aria-pressed={form.transport === 'http'}
                onClick={() => setForm({ ...form, transport: 'http', url: '' })}
                disabled={saving}
              >
                {t('mcp.transport_http')}
              </button>
              <button
                type="button"
                className={`mcp-transport-pill ${form.transport === 'sse' ? 'mcp-transport-pill--active' : ''}`}
                aria-pressed={form.transport === 'sse'}
                onClick={() => setForm({ ...form, transport: 'sse', url: '' })}
                disabled={saving}
              >
                {t('mcp.transport_sse')}
              </button>
            </div>
          </div>

          {hasTransportFields && (
            <>
              <div className="mcp-cmd-row">
                <label className="field">
                  <span>{t('mcp.command')}</span>
                  <input
                    value={form.command}
                    onChange={(event) => setForm({ ...form, command: event.target.value })}
                    placeholder={t('mcp.command_placeholder')}
                    disabled={saving}
                  />
                </label>
                <label className="field mcp-args-field">
                  <span className="mcp-args-label">{t('mcp.args')}</span>
                  <input
                    value={form.args}
                    onChange={(event) => setForm({ ...form, args: event.target.value })}
                    placeholder={t('mcp.args_placeholder')}
                    disabled={saving}
                  />
                </label>
              </div>
            </>
          )}

          {isRemoteTransport && (
            <label className="field">
              <span>{t('mcp.url')}</span>
              <input
                value={form.url}
                onChange={(event) => setForm({ ...form, url: event.target.value })}
                placeholder={t('mcp.url_placeholder')}
                disabled={saving}
              />
            </label>
          )}

          <div className="field">
            <span>{t('mcp.env_desc')}</span>
            {form.envPairs.map((pair, i) => {
              const isSecret = pair.value === SECRET_PLACEHOLDER;
              return (
                <div className="mcp-env-row" key={i}>
                  <input
                    value={pair.key}
                    onChange={(event) => updateEnvPair(i, 'key', event.target.value)}
                    placeholder={t('mcp.env_key')}
                    className="mcp-env-input"
                    disabled={saving}
                  />
                  <input
                    value={pair.value}
                    onChange={(event) => updateEnvPair(i, 'value', event.target.value)}
                    placeholder={t('mcp.env_value')}
                    className="mcp-env-input"
                    type={isSecret ? 'password' : 'text'}
                    disabled={saving}
                  />
                  {isSecret && <small className="mcp-secret-hint">{t('mcp.secret_kept')}</small>}
                  <Button variant="icon" className="mcp-env-remove" onClick={() => removeEnvPair(i)} disabled={saving} title={t('common.delete')}>
                    <Trash2 size={12} />
                  </Button>
                </div>
              );
            })}
            <Button variant="ghost" onClick={addEnvPair} disabled={saving} className="mcp-env-add-btn">
              <Plus size={12} />
              {t('mcp.env_add')}
            </Button>
          </div>

          <div className="mcp-form-footer">
            {form.id && (
              <Button variant="ghost" className="mcp-danger-button" onClick={() => setDeleteConfirm(form.id)} disabled={saving}>
                <Trash2 size={14} />
                {t('common.delete')}
              </Button>
            )}
            <div className="mcp-form-footer__actions">
              <Button variant="secondary" onClick={cancelForm} disabled={saving}>{t('mcp.cancel')}</Button>
              <Button
                variant="primary"
                onClick={handleSave}
                disabled={saving || !canSave}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                {t('mcp.save')}
              </Button>
            </div>
          </div>
        </div>
      </div>

      {deleteConfirm && (
        <div className="mcp-delete-confirm">
          <p>{t('mcp.delete_confirm')}</p>
          <div>
            <Button variant="secondary" onClick={() => setDeleteConfirm(null)}>{t('mcp.cancel')}</Button>
            <Button variant="primary" className="mcp-delete-confirm__button" onClick={() => handleDelete(deleteConfirm)}>
              <Trash2 size={13} />
              {t('common.delete')}
            </Button>
          </div>
        </div>
      )}

      {message && <p className="mcp-message">{message}</p>}
    </WorkspacePage>
  );
}
