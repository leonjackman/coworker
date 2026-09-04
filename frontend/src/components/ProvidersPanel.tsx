import { AlertTriangle, BrainCircuit, Check, Loader2, Network, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { getProviderTemplateOrder, providerTemplate, ProviderIcon } from '../lib/provider-registry';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { ProviderEntry, ProviderPayload, ProviderTestResult } from '../types';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './ui/dropdown-menu';
import { Switch } from './ui/switch';
import { WorkspacePage } from './ui/workspace-page';
import { usePageNavPublish } from '../nav/PageNav';

type ViewMode = 'list' | 'form';

type FormState = ProviderPayload & {
  id: string | null;
  availableModels: string[];
};

const emptyForm = (): FormState => ({
  id: null,
  name: '',
  provider_type: 'custom',
  base_url: '',
  api_key: '',
  model: '',
  availableModels: [],
});

// Preset max-output options (tokens) for the per-provider cap; a custom value
// falls outside the list.
const MAX_OUTPUT_PRESETS = [4096, 8192, 16384, 32768, 65536, 128000];

interface ProvidersPanelProps {
  onProviderChange: () => void;
}

export function ProvidersPanel({ onProviderChange }: ProvidersPanelProps) {
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [defaultProviderId, setDefaultProviderId] = useState('');
  const [defaultModel, setDefaultModel] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [form, setForm] = useState<FormState>(emptyForm());
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [discoveringCtx, setDiscoveringCtx] = useState(false);
  const [ctxSource, setCtxSource] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [maxOutputMode, setMaxOutputMode] = useState<'preset' | 'custom'>('preset');
  const [templateOrder, setTemplateOrder] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setMessage(null);
    try {
      const response = await chatService.listProviders();
      setProviders(response.providers);
      setDefaultProviderId(response.default_provider_id);
      setDefaultModel(response.default_model);
    } catch (error) {
      setMessage(translateError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void getProviderTemplateOrder().then(order => setTemplateOrder(order));
  }, []);

  const publishNav = usePageNavPublish();
  useEffect(() => {
    publishNav({
      viewLabel: t('providers.title'),
      leafLabel: viewMode === 'form' ? (form.id ? t('providers.edit_title') : t('providers.add_title')) : undefined,
      onBackToRoot: viewMode === 'form' ? cancelForm : undefined,
    });
    return () => publishNav(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publishNav, viewMode, form.id]);

  function selectTemplate(key: string) {
    const template = providerTemplate(key);
    if (!template) return;
    // Preserve the current editing state (id + existing api_key/model) when a
    // template pill is clicked while editing; wiping the whole form would turn
    // an update into a new-provider creation.
    setForm((current) => ({
      ...emptyForm(),
      ...(current?.id && current.id !== '' ? { id: current.id, api_key: current.api_key, model: current.model } : {}),
      provider_type: key,
      name: key === 'custom' ? '' : template.name,
      base_url: template.base_url,
    }));
  }

  const hasValidProviderId = (id: string | null): boolean => {
    return !!id && id !== '';
  };

  function startAdd() {
    setForm(emptyForm());
    setTestResult(null);
    setCtxSource('');
    setViewMode('form');
  }

  function startEdit(provider: ProviderEntry) {
    setForm({
      id: provider.id,
      name: provider.name,
      provider_type: provider.provider_type,
      base_url: provider.base_url,
      api_key: '',
      model: provider.model,
      availableModels: provider.model ? [provider.model] : [],
      ...(provider.context_window ? { context_window: provider.context_window } : {}),
      ...(provider.max_output_tokens !== undefined && provider.max_output_tokens > 0 ? { max_output_tokens: provider.max_output_tokens } : {}),
      vision: Boolean(provider.vision),
    });
    setMaxOutputMode(provider.max_output_tokens !== undefined && MAX_OUTPUT_PRESETS.includes(provider.max_output_tokens) ? 'preset' : 'custom');
    setTestResult(null);
    setCtxSource(provider.context_source ?? '');
    setViewMode('form');
  }

  function cancelForm() {
    setViewMode('list');
    setForm(emptyForm());
    setTestResult(null);
    setCtxSource('');
    setDeleteConfirm(null);
  }

  async function handleFetchModels() {
    if (!form.base_url.trim()) return;
    setFetchingModels(true);
    setMessage(null);
    try {
      const fetchParams: { base_url: string; api_key: string; provider_type: string; provider_id?: string } = {
        base_url: form.base_url,
        api_key: form.api_key,
        provider_type: form.provider_type,
      };
      if (form.id) fetchParams.provider_id = form.id;
      const response = await chatService.fetchProviderModels(fetchParams);
      setForm((current) => ({
        ...current,
        availableModels: response.models,
        model: response.models.length > 0 && !current.model ? response.models[0] ?? '' : current.model,
      }));
      if (response.error) setMessage(response.error);
    } catch (error) {
      setMessage(translateError(error));
    } finally {
      setFetchingModels(false);
    }
  }

  async function handleSave() {
    if (!form.name.trim() || !form.base_url.trim() || !form.model.trim()) return;
    if (form.id === "") return;
    setSaving(true);
    setMessage(null);
    try {
      let newProviderId: string | null = null;
      if (form.id) {
        await chatService.updateProvider(form.id, {
          name: form.name,
          base_url: form.base_url,
          ...(form.api_key ? { api_key: form.api_key } : {}),
          model: form.model,
          ...(form.context_window !== undefined ? { context_window: form.context_window } : {}),
          ...(form.max_output_tokens !== undefined ? { max_output_tokens: form.max_output_tokens } : {}),
          vision: Boolean(form.vision),
        });
        newProviderId = form.id;
      } else {
        const created = await chatService.createProvider({
          name: form.name,
          provider_type: form.provider_type,
          base_url: form.base_url,
          api_key: form.api_key,
          model: form.model,
          ...(form.context_window !== undefined ? { context_window: form.context_window } : {}),
          ...(form.max_output_tokens !== undefined ? { max_output_tokens: form.max_output_tokens } : {}),
          vision: Boolean(form.vision),
        });
        newProviderId = created.provider.id;
      }
      await load();
      onProviderChange();
      setViewMode('list');
      if (newProviderId) {
        setForm((current) => ({ ...current, id: newProviderId }));
      }
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    } finally {
      setSaving(false);
    }
  }

  async function handleDiscoverContext() {
    if (!form.id || form.id === "") return;
    setDiscoveringCtx(true);
    setMessage(null);
    try {
      const response = await chatService.discoverProviderContext(form.id);
      setForm((current) => ({ ...current, ...(response.provider.context_window ? { context_window: response.provider.context_window } : {}) }));
      setCtxSource(response.provider.context_source ?? 'discovered');
    } catch (error) {
      setMessage(translateError(error) || t('providers.context_discover_failed'));
    } finally {
      setDiscoveringCtx(false);
    }
  }

  async function handleDelete(providerId: string) {
    if (!hasValidProviderId(providerId)) return;
    setSaving(true);
    try {
      await chatService.deleteProvider(providerId);
      await load();
      onProviderChange();
      setViewMode('list');
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    } finally {
      setSaving(false);
      setDeleteConfirm(null);
    }
  }

  async function handleToggle(provider: ProviderEntry) {
    if (!hasValidProviderId(provider.id)) return;
    try {
      await chatService.updateProvider(provider.id, { enabled: !provider.enabled });
      await load();
      onProviderChange();
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    }
  }

  async function handleSetDefault(provider: ProviderEntry) {
    if (!provider.model || !hasValidProviderId(provider.id)) return;
    try {
      await chatService.setDefaultProvider(provider.id, provider.model);
      await load();
      onProviderChange();
    } catch (error) {
      setMessage(translateError(error) || t('common.operation_failed'));
    }
  }

  async function handleTestConnection() {
    if (!form.base_url || !form.model) return;
    setTesting(true);
    setTestResult(null);
    try {
      const testParams: { base_url: string; api_key: string; model: string; provider_id?: string } = {
        base_url: form.base_url,
        api_key: form.api_key,
        model: form.model,
      };
      if (form.id) testParams.provider_id = form.id;
      const result = await chatService.testProvider(testParams);
      setTestResult(result);
    } catch (error) {
      setTestResult({ ok: false, latency_ms: null, error: translateError(error) });
    } finally {
      setTesting(false);
    }
  }

  if (viewMode === 'form') {
    return (
      <WorkspacePage
        className="provider-shell--form"
        eyebrow={t('providers.title')}
        title={form.id ? t('providers.edit_title') : t('providers.add_title')}
        action={(
          <Button variant="ghost" onClick={cancelForm}>
            {t('providers.back')}
          </Button>
        )}
      >
        <div className="provider-form-card">
          <BrainCircuit size={18} className="provider-form-card__icon" />
          <div className="provider-form-card__body">
            <h2>{form.id ? t('providers.edit_title') : t('providers.add_title')}</h2>

            <label className="field">
              <span>{t('providers.name')}</span>
              <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder={t('providers.name_placeholder')} disabled={saving} />
            </label>

            <div className="field">
              <span>{t('providers.template')}</span>
              <div className="provider-template-row">
                {templateOrder.map((key) => {
                  const template = providerTemplate(key);
                  if (!template) return null;
                  const active = form.provider_type === key;
                  return (
                    <button className={`provider-template-pill ${active ? 'provider-template-pill--active' : ''}`} key={key} type="button" onClick={() => selectTemplate(key)} disabled={saving}>
                      <ProviderIcon type={key} />
                      {template.name}
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="field">
              <span>{t('providers.base_url')}</span>
              <input value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} placeholder="https://api.openai.com/v1" disabled={saving} />
            </label>

            <label className="field">
              <span>{t('providers.api_key')}</span>
              <input value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder="sk-..." type="password" disabled={saving} />
              {form.id && <small>{t('providers.api_key_saved')}</small>}
            </label>

            <div className="field">
              <span>{t('providers.model')}</span>
              <div className="provider-model-row">
                <input
                  list="provider-model-suggestions"
                  value={form.model}
                  onChange={(event) => setForm({ ...form, model: event.target.value })}
                  placeholder={t('providers.model_select_placeholder')}
                  disabled={saving}
                />
                <datalist id="provider-model-suggestions">
                  {form.availableModels.map((model) => <option key={model} value={model} />)}
                </datalist>
                <Button variant="icon" onClick={handleFetchModels} disabled={fetchingModels || !form.base_url.trim()} title={t('providers.fetch_models')}>
                  <RefreshCw size={14} className={fetchingModels ? 'animate-spin' : ''} />
                </Button>
              </div>
              {form.availableModels.length === 0 && !fetchingModels && form.base_url.trim() && <small>{t('providers.fetch_models_hint')}</small>}
            </div>

            <label className="field">
              <span>{t('providers.context_window')}</span>
              <div className="provider-model-row">
                <input
                  type="number"
                  min={0}
                  step={1000}
                  value={form.context_window ?? ''}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value === '') {
                      const { context_window: _cw, ...rest } = form;
                      setForm(rest);
                    } else {
                      setForm({ ...form, context_window: Number(value) });
                    }
                  }}
                  placeholder={t('providers.context_window_placeholder')}
                  disabled={saving}
                />
                <Button variant="icon" onClick={handleDiscoverContext} disabled={discoveringCtx || !hasValidProviderId(form.id)} title={t('providers.context_discover')}>
                  <RefreshCw size={14} className={discoveringCtx ? 'animate-spin' : ''} />
                </Button>
              </div>
              {ctxSource && ctxSource === 'unreachable' && (
                <small className="provider-ctx-error" role="alert">{t('providers.context_unreachable')}</small>
              )}
              {ctxSource && ctxSource !== 'unreachable' && <small>{t(`providers.context_source_${ctxSource}`)}</small>}
              {form.provider_type === 'ollama' && <small>{t('providers.ollama_ctx_hint')}</small>}
            </label>

            <label className="field">
              <span>{t('providers.max_output_tokens')}</span>
              <div className="provider-model-row">
                <select
                  value={maxOutputMode === 'custom' && form.max_output_tokens !== undefined ? '__custom__' : (form.max_output_tokens ?? '')}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value === '__custom__') {
                      setMaxOutputMode('custom');
                    } else if (value === '') {
                      const { max_output_tokens: _m, ...rest } = form;
                      setForm(rest);
                      setMaxOutputMode('preset');
                    } else {
                      setMaxOutputMode('preset');
                      setForm({ ...form, max_output_tokens: Number(value) });
                    }
                  }}
                  disabled={saving}
                >
                  <option value="">{t('providers.max_output_tokens_default')}</option>
                  {MAX_OUTPUT_PRESETS.map((value) => (
                    <option key={value} value={value}>{value.toLocaleString()}</option>
                  ))}
                  <option value="__custom__">{t('providers.max_output_tokens_custom')}</option>
                </select>
                {maxOutputMode === 'custom' && (
                  <input
                    type="number"
                    min={0}
                    max={1000000}
                    step={1000}
                    value={form.max_output_tokens ?? ''}
                    onChange={(event) => {
                      const value = event.target.value;
                      if (value === '') {
                        const { max_output_tokens: _m, ...rest } = form;
                        setForm(rest);
                      } else {
                        setForm({ ...form, max_output_tokens: Number(value) });
                      }
                    }}
                    placeholder={t('providers.max_output_tokens_placeholder')}
                    disabled={saving}
                  />
                )}
              </div>
              <small>{t('providers.max_output_tokens_hint')}</small>
            </label>

            <label className="field provider-vision-field">
              <div className="provider-vision-row">
                <input
                  type="checkbox"
                  checked={Boolean(form.vision)}
                  onChange={(e) => setForm({ ...form, vision: e.target.checked })}
                  disabled={saving}
                />
                <span>{t('providers.vision')}</span>
              </div>
            </label>

            <div className="provider-test-row">
              <Button variant="secondary" onClick={handleTestConnection} disabled={testing || !form.base_url || !form.model}>
                {testing ? <Loader2 size={14} className="animate-spin" /> : <Network size={14} />}
                {t('providers.test_connection')}
              </Button>
              {testResult && (
                <span className={`provider-test-result ${testResult.ok ? 'provider-test-result--ok' : 'provider-test-result--error'}`}>
                  {testResult.ok ? `${t('providers.test_success')} (${testResult.latency_ms}ms)` : `${t('providers.test_failed')}: ${testResult.error}`}
                </span>
              )}
            </div>

            <div className="provider-form-footer">
              {hasValidProviderId(form.id) && (
                <Button variant="ghost" className="provider-danger-button" onClick={() => setDeleteConfirm(form.id)} disabled={saving}>
                  <Trash2 size={14} />
                  {t('common.delete')}
                </Button>
              )}
              <div className="provider-form-footer__actions">
                <Button variant="secondary" onClick={cancelForm} disabled={saving}>{t('providers.cancel')}</Button>
                <Button variant="primary" onClick={handleSave} disabled={saving || !form.name.trim() || !form.base_url.trim() || !form.model.trim()}>
                  {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                  {t('providers.save')}
                </Button>
              </div>
            </div>
          </div>
        </div>

        {deleteConfirm && (
          <div className="provider-delete-confirm">
            <p>{t('providers.delete_confirm')}</p>
            <div>
              <Button variant="secondary" onClick={() => setDeleteConfirm(null)}>{t('providers.cancel')}</Button>
              <Button variant="primary" className="provider-delete-confirm__button" onClick={() => handleDelete(deleteConfirm)}>
                <Trash2 size={13} />
                {t('common.delete')}
              </Button>
            </div>
          </div>
        )}

        {message && <p className="provider-message">{message}</p>}
      </WorkspacePage>
    );
  }

  const defaultProvider = providers.find((provider) => provider.id === defaultProviderId);

  return (
    <WorkspacePage
      title={t('providers.title')}
      description={t('providers.description')}
    >
      <div className="provider-quick-card">
        <Network size={18} className="provider-form-card__icon" />
        <div>
          <strong>{t('providers.quick_add')}</strong>
          <p>{t('providers.quick_add_desc')}</p>
          <div className="provider-template-row">
            {templateOrder.filter((key) => key !== 'custom').map((key) => {
              const template = providerTemplate(key);
              if (!template) return null;
              return (
                <button key={key} type="button" className="provider-template-pill" onClick={() => { selectTemplate(key); setViewMode('form'); }}>
                  <ProviderIcon type={key} />
                  {template.name}
                </button>
              );
            })}
            <button type="button" className="provider-template-pill provider-template-pill--dashed" onClick={startAdd}>
              <Plus size={12} />
              {t('providers.custom_provider')}
            </button>
          </div>
        </div>
      </div>

      <div className="provider-list-heading">
        <h2>{t('providers.my_providers')}</h2>
        <Button variant="primary" onClick={startAdd}>
          <Plus size={14} />
          {t('providers.add_provider')}
        </Button>
      </div>

      {defaultProvider && defaultModel && (
        <div className="provider-default-card">
          <Check size={14} />
          <span>{t('providers.default_label')}</span>
          <ProviderIcon type={defaultProvider.provider_type} />
          <strong>{defaultProvider.name}</strong>
          <Badge>{defaultModel}</Badge>
        </div>
      )}

      {loading ? (
        <div className="provider-empty">{t('common.loading')}</div>
      ) : providers.length === 0 ? (
        <div className="provider-empty">
          <p>{t('providers.empty')}</p>
          <span>{t('providers.empty_hint')}</span>
        </div>
      ) : (
        <div className="provider-list">
          {providers.map((provider) => {
            const isDefault = provider.id === defaultProviderId;
            return (
              <article
                className={`provider-card provider-card--mir provider-card--mir-hover ${!provider.enabled ? 'provider-card--disabled' : ''} ${isDefault ? 'provider-card--is-default' : ''}`}
                key={provider.id}
                onClick={() => { startEdit(provider); setViewMode('form'); }}
              >
                {/* Row 1: icon + name + badges / switcher */}
                <div className="provider-card__top">
                  <div className="provider-card__identity">
                    <ProviderIcon type={provider.provider_type} size={18} />
                    <strong>{provider.name}</strong>
                    {!provider.enabled && <Badge>{t('providers.disabled')}</Badge>}
                  </div>
                  <div className="provider-card__controls" onClick={(e) => e.stopPropagation()}>
                    <Switch id={`provider-switch-${provider.id}`} checked={provider.enabled} onChange={() => handleToggle(provider)} />
                  </div>
                </div>

                {/* Row 2: model + url · default / error · edit menu */}
                <div className="provider-card__meta">
                  <span className="provider-card__subtitle">
                    {provider.model && <span>{t('providers.model')}: <Badge className="provider-card-model-badge">{provider.model}</Badge></span>}
                    {provider.model && provider.base_url && <span> · </span>}
                    {provider.base_url && <span className="provider-card-url">{provider.base_url}</span>}
                    {isDefault && <span> · {t('providers.default_badge')}</span>}
                    {provider.context_error && <span className="provider-card__warning">{provider.context_error}</span>}
                  </span>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        className="provider-card-edit-btn"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {t('providers.edit')}
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="provider-menu__content">
                      <DropdownMenuItem onSelect={() => startEdit(provider)}>{t('providers.edit')}</DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => handleSetDefault(provider)} disabled={isDefault}>
                        {isDefault && <Check size={12} />}
                        {t('providers.set_default')}
                      </DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => { void handleToggle(provider); }}>
                        {provider.enabled ? t('providers.disable') : t('providers.enable')}
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem variant="destructive" onSelect={() => setDeleteConfirm(provider.id)}>
                        <Trash2 size={12} />
                        {t('common.delete')}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>

                {/* Delete inline confirm */}
                {deleteConfirm === provider.id && (
                  <div className="provider-inline-delete">
                    <p>{t('providers.delete_confirm')}</p>
                    <div>
                      <Button variant="secondary" className="h-7" onClick={() => setDeleteConfirm(null)}>{t('providers.cancel')}</Button>
                      <Button variant="primary" className="h-7 provider-delete-confirm__button" onClick={() => handleDelete(provider.id)}>{t('common.delete')}</Button>
                    </div>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      {message && <p className="provider-message">{message}</p>}
    </WorkspacePage>
  );
}
