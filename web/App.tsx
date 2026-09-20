import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowUpRight, Check, ChevronDown, CircleHelp, Copy, Images, Layers, LoaderCircle, Maximize2, Plus, Settings2, SlidersHorizontal, Sparkles, Square, Trash2, Upload, X } from 'lucide-react';
import { api, ApiError, Asset, Batch, bootstrap, download, Draft, initialDraft, Settings } from './api';

const ratios = ['Adaptive', '1:1', '16:9', '21:9', '4:3', '3:2', '5:4', '2:1', '3:4', '2:3', '4:5', '9:16'];
const active = (batch: Batch) => batch.tasks.some(task => ['queued', 'running'].includes(task.status));
const labels: Record<string, string> = { queued: '等待中', running: '生成中', success: '已完成', failed: '未完成', interrupted: '已中断', cancelled: '已取消' };

function readDraft(): Draft {
  try { return { ...initialDraft, ...JSON.parse(localStorage.getItem('canvas-draft') || '{}') }; }
  catch { return initialDraft; }
}

export function App() {
  const [settings, setSettings] = useState<Settings>();
  const [draft, setDraft] = useState<Draft>(readDraft);
  const [assets, setAssets] = useState<Record<string, Asset>>({});
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<string[] | null>(null);
  const [lightbox, setLightbox] = useState<Asset | null>(null);
  const [original, setOriginal] = useState(false);
  const [pendingId, setPendingId] = useState(() => localStorage.getItem('canvas-pending-id') || '');
  const fileInput = useRef<HTMLInputElement>(null);
  const commonInput = useRef<HTMLInputElement>(null);
  const promptInput = useRef<HTMLTextAreaElement>(null);
  const current = batches.find(batch => batch.id === selected) ?? batches[0];
  const running = batches.flatMap(batch => batch.tasks).filter(task => ['running', 'queued'].includes(task.status)).length;
  const patch = (values: Partial<Draft>) => setDraft(previous => ({ ...previous, ...values }));
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : '操作未完成');

  async function refresh() {
    const result = await api<{ items: Batch[] }>('/api/batches');
    setBatches(result.items);
    const items = await api<{ items: Asset[] }>('/api/assets');
    setAssets(Object.fromEntries(items.items.map(item => [item.id, item])));
  }

  useEffect(() => { bootstrap().then(async result => { setSettings(result); await refresh(); }).catch(fail); }, []);
  useEffect(() => { localStorage.setItem('canvas-draft', JSON.stringify(draft)); }, [draft]);
  useEffect(() => { if (pendingId) localStorage.setItem('canvas-pending-id', pendingId); else localStorage.removeItem('canvas-pending-id'); }, [pendingId]);
  useEffect(() => {
    if (!settings) return;
    const timer = setInterval(() => { if (document.visibilityState === 'visible') refresh().catch(() => {}); }, 2000);
    return () => clearInterval(timer);
  }, [settings]);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 4500); return () => clearTimeout(timer); }, [toast]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setLightbox(null); setPreview(null); setShowSettings(false); } };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, []);

  async function upload(files: File[], common = false) {
    if (!files.length) return;
    setUploading(true); setError('');
    try {
      const previous = common ? draft.common_references : draft.references;
      if (previous.length + files.length > (common ? 3 : draft.mode === 'per-image' ? 10 : 12)) throw new Error(common ? '通用参考图最多 3 张' : '参考图数量超过当前模式限制');
      if (files.some(file => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) throw new Error('请选择 PNG、JPEG 或 WebP 图片');
      const total = files.reduce((sum, file) => sum + file.size, 0) + previous.reduce((sum, id) => sum + (assets[id]?.bytes || 0), 0);
      if (total > (common ? 6 : 15) * 1024 * 1024) throw new Error(common ? '通用参考图合计最多 6 MB' : '参考图合计最多 15 MB');
      const form = new FormData(); files.forEach(file => form.append('files', file));
      const result = await api<{ items: Asset[] }>('/api/uploads', form);
      setAssets(values => ({ ...values, ...Object.fromEntries(result.items.map(item => [item.id, item])) }));
      patch({ [common ? 'common_references' : 'references']: [...previous, ...result.items.map(item => item.id)] });
    } catch (reason) { fail(reason); }
    finally { setUploading(false); }
  }

  async function submit() {
    if (!settings?.configured) { setShowSettings(true); return; }
    setError(''); setBusy(true);
    const clientId = crypto.randomUUID();
    localStorage.setItem('canvas-pending-id', clientId);
    setPendingId(clientId);
    try {
      const batch = await api<Batch>('/api/batches', { ...draft, client_id: clientId });
      setSelected(batch.id); setPendingId(''); await refresh();
      setToast(`${batch.tasks.length} 个任务已接收，关闭页面不影响后台处理`);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) { setPendingId(''); fail(reason); return; }
      try {
        const batch = await api<Batch>(`/api/batches/${clientId}`);
        setSelected(batch.id); setPendingId(''); await refresh(); setToast('已查到接收回执，没有重复提交');
      } catch { setPendingId(clientId); fail(reason); }
    } finally { setBusy(false); }
  }

  async function inspectPrompt() {
    try { const result = await api<{ tasks: { effective_prompt: string }[] }>('/api/plan', { ...draft, client_id: crypto.randomUUID() }); setPreview(result.tasks.map(task => task.effective_prompt)); }
    catch (reason) { fail(reason); }
  }

  async function resolvePending() {
    try { const batch = await api<Batch>(`/api/batches/${pendingId}`); setSelected(batch.id); setPendingId(''); await refresh(); setToast('已查到任务，请勿重复提交'); }
    catch (reason) { fail(reason); }
  }

  function editImage(image: Asset) {
    setAssets(values => ({ ...values, [image.id]: image }));
    setDraft({ ...initialDraft, references: [image.id], derivative: true, custom_size: current?.request.custom_size ?? null, image_size: current?.request.image_size ?? '2K' });
    setLightbox(null); promptInput.current?.focus(); setToast('来源图已放入编辑区，请填写修改要求');
  }

  function addReference(image: Asset) {
    if (draft.references.includes(image.id)) { setToast('这张图片已在参考图中'); return; }
    if (draft.references.length >= (draft.mode === 'per-image' ? 10 : 12)) { setError('参考图已达当前模式上限，请先移除一张图片'); return; }
    setAssets(values => ({ ...values, [image.id]: image }));
    patch({ references: [...draft.references, image.id] });
    setToast('已加入参考图');
  }

  function referenceArea(common = false) {
    const ids = common ? draft.common_references : draft.references;
    return <div className="reference-area" tabIndex={0} role="group" aria-label={common ? '通用参考图上传区' : '参考图上传区'}
      onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); upload([...event.dataTransfer.files], common); }}
      onPaste={event => { const files = [...event.clipboardData.files]; if (files.length) { event.preventDefault(); upload(files, common); } }}>
      {ids.length ? <div className="reference-list">{ids.map((id, index) => <div className="reference" key={`${id}-${index}`}>
        {assets[id] ? <button className="reference-preview" aria-label={`预览参考图 ${index + 1}`} onClick={() => { setLightbox(assets[id]); setOriginal(false); }}><img src={assets[id].url} alt={assets[id].name}/></button> : <span>图片失效</span>}
        <button className="remove-reference" aria-label={`移除${common ? '通用' : ''}参考图 ${index + 1}`} onClick={() => patch({ [common ? 'common_references' : 'references']: ids.filter((_, position) => position !== index) })}><X size={12}/></button>
        <span>{index + 1}</span>
      </div>)}<button className="add-reference" aria-label={common ? '添加通用参考图' : '添加参考图'} onClick={() => (common ? commonInput : fileInput).current?.click()}><Plus size={20}/></button></div>
        : <button className="drop-button" onClick={() => (common ? commonInput : fileInput).current?.click()}><Upload size={21}/><span>{common ? '上传通用参考图' : '点击上传，或将图片拖到这里'}</span><small>也可以在此处粘贴图片 · PNG / JPG / WebP</small></button>}
      <input hidden ref={common ? commonInput : fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple aria-label={common ? '通用参考图文件' : '参考图文件'} onChange={event => { upload([...(event.target.files || [])], common); event.target.value = ''; }}/>
    </div>;
  }

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><div className="brand-mark"><Layers size={24}/></div><div><strong>画间</strong><span>ChatGPT 图片工作台</span></div></div>
      <div className="header-actions"><span className="local-label"><span/>本地工作台 · 结果保存本机</span><button className="connection-button" onClick={() => setShowSettings(true)}><span className={settings?.configured ? 'dot connected' : 'dot'}/>{settings?.configured ? '已配置连接' : '配置 ChatGPT'}<Settings2 size={16}/></button></div></header>
    {error && <div className="error-banner" role="alert"><span>{error}</span><button aria-label="关闭错误提示" onClick={() => setError('')}><X size={17}/></button></div>}
    <main className="workspace">
      <aside className="composer"><div className="section-heading"><h1>自定义生图</h1><span>把想法变成图片</span></div>
        <div className="mode-tabs" role="group" aria-label="生成模式">{([['count', '自由生图'], ['queue', '提示词队列'], ['per-image', '逐图修改']] as const).map(([mode, label]) => <button key={mode} className={draft.mode === mode ? 'selected' : ''} onClick={() => patch({ mode, derivative: false, common_references: mode === 'per-image' ? draft.common_references : [] })}>{label}</button>)}</div>
        {draft.derivative && <div className="edit-notice">继续修改：保留来源商品外观与原有文案<button onClick={() => patch({ derivative: false })}>取消保真规则</button></div>}
        <div className="field-heading"><label htmlFor="prompt">{draft.derivative ? '修改要求' : '你的提示词'}</label><span>{draft.mode === 'queue' ? '一行一个任务' : '普通生图不添加风格词'}</span></div>
        <textarea id="prompt" ref={promptInput} value={draft.prompt} onChange={event => patch({ prompt: event.target.value })} placeholder={draft.mode === 'queue' ? '一张白色运动鞋的电商主图\n同一双鞋的鞋底纹理特写\n一张展示轻盈质感的场景图' : draft.derivative ? '例如：只把背景换成浅灰色，鞋子和标识保持不变' : '描述你想要的画面、主体、风格与文字……\n\n例如：一只陶瓷马克杯，暖白背景，窗边自然光，保留参考图中的杯身图案。'} maxLength={20000}/>
        <div className="prompt-footer"><button className="text-button" onClick={inspectPrompt}><SlidersHorizontal size={13}/>发送内容预览</button><span>{draft.prompt.length.toLocaleString()} / 20,000</span></div>
        <div className="field-heading"><label>{draft.mode === 'per-image' ? '来源图' : '参考图'} <span>{draft.mode === 'per-image' ? '必选' : '可选'}</span></label><small>{draft.references.length} / {draft.mode === 'per-image' ? 10 : 12}</small></div>
        {referenceArea()}
        {draft.mode === 'per-image' && <><div className="field-heading"><label>通用参考图 <span>每个任务共用</span></label><small>最多 3 张</small></div>{referenceArea(true)}</>}
        <div className="control-row"><label>画布比例<select value={draft.custom_size ? 'custom' : draft.ratio} onChange={event => event.target.value === 'custom' ? patch({ custom_size: { widthCm: '20', heightCm: '30' }, ratio: 'Adaptive' }) : patch({ ratio: event.target.value, custom_size: null })}>{ratios.map(ratio => <option key={ratio} value={ratio}>{ratio === 'Adaptive' ? '自动 · 不附加尺寸' : ratio}</option>)}<option value="custom">自定义厘米画布</option></select></label>
          {draft.mode === 'count' ? <label>生成数量<select value={draft.count} onChange={event => patch({ count: Number(event.target.value) })}>{Array.from({ length: 10 }, (_, index) => <option key={index} value={index + 1}>{index + 1} 张</option>)}</select></label> : <div className="task-counter"><span>本批任务</span><strong>{draft.mode === 'queue' ? draft.prompt.split('\n').filter(line => line.trim()).length : draft.references.length}<small> 张</small></strong></div>}</div>
        {draft.custom_size && <div className="canvas-fields"><label>宽 / 厘米<input value={draft.custom_size.widthCm} onChange={event => patch({ custom_size: { ...draft.custom_size!, widthCm: event.target.value } })}/></label><label>高 / 厘米<input value={draft.custom_size.heightCm} onChange={event => patch({ custom_size: { ...draft.custom_size!, heightCm: event.target.value } })}/></label><label>目标像素<select value={draft.image_size} onChange={event => patch({ image_size: event.target.value as Draft['image_size'] })}><option>1K</option><option>2K</option><option>4K</option></select></label></div>}
        <p className="dimension-note">尺寸仅作为网页提示词要求，不保证精确像素，不拉伸裁切结果。</p>
        <div className="generate-area"><div className="model-line"><span className="model-icon">G</span><span>ChatGPT 网页生图<small>{settings?.display_model || 'gpt-image-2.5'} · 实际版本未校验</small></span><button aria-label="设置生图模型" onClick={() => setShowSettings(true)}><ChevronDown size={16}/></button></div>
          <button className="generate-button" onClick={submit} disabled={busy || uploading || !draft.prompt.trim() || Boolean(pendingId)}>{busy || uploading ? <LoaderCircle className="spin" size={19}/> : <Sparkles size={19}/>} {uploading ? '正在上传参考图' : busy ? '正在提交' : '开始生成'}<span>{draft.mode === 'count' ? draft.count : draft.mode === 'per-image' ? draft.references.length : draft.prompt.split('\n').filter(line => line.trim()).length} 张</span></button>
          {pendingId && <div className="pending-notice">上次接收状态未确认。<button onClick={resolvePending}>查询回执</button><button onClick={() => { if (confirm('请确认已经检查过网页及历史记录，确实未接收任务。清除此状态后可重新提交，可能产生重复图片。')) setPendingId(''); }}>已核对，清除提示</button></div>}
          <p className="privacy-note">提示词和参考图发送至 ChatGPT，使用你的账号额度。</p></div>
      </aside>
      <section className="results-panel"><div className="results-heading"><div><div className="workspace-label">我的创作空间</div><h2>{current ? '这一批，正在成形' : '让想法，有自己的画面。'}</h2></div><div className="results-tools">{running > 0 && <span className="queue-badge"><LoaderCircle className="spin" size={14}/>{running} 个任务处理中</span>}{current && <button onClick={() => download(`/api/batches/${current.id}/download`, '画间-图片与任务清单.zip').catch(fail)}><ArrowDownToLine size={16}/>整批下载</button>}</div></div>
        {batches.length > 0 && <div className="history-strip" aria-label="历史批次">{batches.map(batch => <button key={batch.id} className={current?.id === batch.id ? 'active' : ''} onClick={() => setSelected(batch.id)}><span>{new Date(batch.created * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span><strong>{batch.request.prompt.slice(0, 14) || '图片任务'}</strong><small>{batch.tasks.filter(task => task.status === 'success').length}/{batch.tasks.length} 张 {active(batch) && '· 处理中'}</small></button>)}</div>}
        {current ? <><div className="batch-toolbar"><span>{current.tasks.length} 个独立任务 · 结果实时保存</span><div><button onClick={() => { setDraft({ ...initialDraft, ...current.request }); setToast('已恢复本批提示词、参考图及画布设置'); }}>恢复设置</button><button onClick={() => setPreview(current.tasks.map(task => task.effective_prompt))}>查看提示词</button>{active(current) ? <button onClick={() => api(`/api/batches/${current.id}/cancel`, {}).then(refresh).catch(fail)}><Square size={12}/>取消未开始</button> : <button aria-label="删除此批历史" onClick={() => { if (confirm('只删除此批历史记录，图片文件仍保留在本地。确定删除？')) api(`/api/batches/${current.id}`, undefined, 'DELETE').then(refresh).catch(fail); }}><Trash2 size={14}/></button>}</div></div>
          <div className="result-grid">{current.tasks.map(task => <article className={`result-card ${task.status}`} key={task.id}><div className="card-meta"><span>作品 {String(task.index + 1).padStart(2, '0')}</span><span className={`status ${task.status}`}>{labels[task.status]}</span></div>
            {task.results.length ? task.results.map(image => <div key={image.id}><button className="image-button" onClick={() => { setLightbox(image); setOriginal(false); }}><img src={image.url} alt={task.prompt}/><span><Maximize2 size={17}/> 查看大图</span></button><div className="image-meta"><span>{image.width} × {image.height}</span><span>PNG · {(image.bytes / 1024 / 1024).toFixed(1)} MB</span></div><div className="card-actions"><button onClick={() => editImage(image)}><Sparkles size={14}/>继续修改</button><button onClick={() => addReference(image)}><Plus size={14}/>作参考</button><button aria-label={`下载作品 ${task.index + 1}`} onClick={() => download(image.url, `画间-${task.index + 1}.png`).catch(fail)}><ArrowDownToLine size={16}/></button></div></div>) : <div className="task-placeholder">{['queued', 'running'].includes(task.status) ? <><LoaderCircle className={task.status === 'running' ? 'spin' : ''} size={28}/><strong>{task.stage}</strong><span>无需保持页面打开</span></> : <><CircleHelp size={28}/><strong>{task.stage}</strong><p>{task.error || '此任务未生成图片'}</p><button onClick={() => { setDraft({ ...initialDraft, ...current.request, prompt: task.prompt, mode: 'count', count: 1 }); setToast('已载入要求。请核对网页后再点击生成，不会自动重试。'); }}>载入要求，手动重试</button></>}</div>}
            <p className="card-prompt" title={task.prompt}>{task.prompt}</p></article>)}</div></>
          : <div className="empty-workspace"><div className="empty-art"><div/><div/><div><Images size={46} strokeWidth={1}/></div></div><h3>第一张作品，从一句话开始</h3><p>输入提示词，也可以带上你的商品参考图。<br/>生成的图片、修改版本和任务记录，都保存在这里。</p><div className="empty-features"><span><Check size={14}/>自由提示词</span><span><Check size={14}/>多图参考</span><span><Check size={14}/>本地历史</span></div></div>}
        <footer className="workspace-footer"><span>本地单账号队列 · 不自动重试未知结果</span><span>网页接口可能变化，账号额度与平台规则仍然适用</span></footer>
      </section>
    </main>
    {toast && <div className="toast" role="status"><Check size={17}/>{toast}</div>}
    {showSettings && settings && <SettingsDialog settings={settings} close={() => setShowSettings(false)} saved={value => { setSettings(value); setToast('连接设置已保存到本机'); }}/ >}
    {preview && <div className="overlay" onClick={() => setPreview(null)}><section className="dialog preview-dialog" role="dialog" aria-modal="true" aria-label="发送内容预览" onClick={event => event.stopPropagation()}><div className="dialog-heading"><h2>实际发送的提示词</h2><button aria-label="关闭预览" onClick={() => setPreview(null)}><X/></button></div><p>普通生成只发送你填写的内容；画布与继续修改规则会在这里完整展示。</p>{preview.map((text, index) => <div className="prompt-preview" key={index}><strong>任务 {index + 1}</strong><pre>{text}</pre></div>)}</section></div>}
    {lightbox && <div className="lightbox" role="dialog" aria-modal="true" aria-label="图片大图"><div className="lightbox-toolbar"><span>{lightbox.width} × {lightbox.height} · {original ? '原尺寸' : '适应窗口'}</span><div><button onClick={() => setOriginal(!original)}>{original ? '适应窗口' : '原尺寸查看'}</button><button onClick={() => download(lightbox.url, '画间-原图.png').catch(fail)}><ArrowDownToLine size={17}/>下载原图</button><button aria-label="关闭大图" onClick={() => setLightbox(null)}><X/></button></div></div><div className={`lightbox-canvas ${original ? 'original' : ''}`}><img src={lightbox.url} alt={lightbox.name} width={original ? lightbox.width : undefined} height={original ? lightbox.height : undefined}/></div></div>}
  </div>;
}

function SettingsDialog({ settings, close, saved }: { settings: Settings; close: () => void; saved: (settings: Settings) => void }) {
  const [token, setToken] = useState('');
  const [proxy, setProxy] = useState('');
  const [proxyChanged, setProxyChanged] = useState(false);
  const [upstream, setUpstream] = useState(settings.upstream_model);
  const [display, setDisplay] = useState(settings.display_model);
  const [message, setMessage] = useState('');
  const [working, setWorking] = useState(false);
  async function save(clear = false) {
    setWorking(true); setMessage('');
    try { const result = await api<Settings>('/api/settings', { access_token: token || null, proxy: proxyChanged ? proxy : null, upstream_model: upstream, display_model: display, clear_token: clear }); saved(result); setToken(''); setProxy(''); setProxyChanged(false); setMessage(clear ? '网页登录凭证已清除' : '已保存。可以检查登录状态，或返回工作台生成。'); }
    catch (error) { setMessage((error as Error).message); }
    finally { setWorking(false); }
  }
  async function check() {
    setWorking(true); setMessage('');
    try { const result = await api<{ message: string }>('/api/connection/check', {}); setMessage(result.message); }
    catch (error) { setMessage((error as Error).message); }
    finally { setWorking(false); }
  }
  return <div className="overlay"><section className="dialog settings-dialog" role="dialog" aria-modal="true" aria-label="连接设置"><div className="dialog-heading"><div><h2>连接你的 ChatGPT</h2><p>只连接自己的账号，凭证不进入浏览器历史或代码仓库。</p></div><button aria-label="关闭连接设置" onClick={close}><X/></button></div>
    <div className="connection-state"><span className={settings.configured ? 'dot connected' : 'dot'}/>{settings.configured ? '已保存网页登录凭证' : '尚未连接'}<small>{settings.credential_storage}</small></div>
    <label className="settings-label">网页登录凭证<input type="password" autoComplete="new-password" value={token} onChange={event => setToken(event.target.value)} onPaste={event => { event.preventDefault(); setToken(event.clipboardData.getData('text/plain')); }} placeholder={settings.configured ? '可粘贴整个会话 JSON 更新；留空不修改' : '直接粘贴整个会话 JSON，自动提取凭证'}/></label>
    <details className="help"><summary>全选复制会话 JSON，无需查找字段</summary><p>在你自己的浏览器登录 ChatGPT，再打开下方会话信息页，全选复制页面中的整个 JSON，粘贴到上方输入框并保存。程序自动提取登录凭证，无需自己查找 <code>accessToken</code>；也兼容单独粘贴凭证。</p><a href="https://chatgpt.com/api/auth/session" target="_blank" rel="noreferrer">打开 ChatGPT 会话信息页 <ArrowUpRight size={14}/></a><p>只加密保存所需凭证，不保存 JSON 中的个人资料。整个 JSON 也包含敏感登录信息，请勿发给别人。凭证有有效期；若遇到验证或无权访问，请先在网页完成登录，本程序不自动注册或绕过权限。</p></details>
    <label className="settings-label">出站代理 <span>可选</span><input type="password" autoComplete="off" value={proxy} onChange={event => { setProxy(event.target.value); setProxyChanged(true); }} placeholder={settings.proxy_configured ? '已有代理；留空不修改。填写后清空可删除。' : 'http://127.0.0.1:端口 或 socks5://…'}/></label>
    <details className="advanced"><summary>模型映射与本地 API</summary><label className="settings-label">对外模型名称<input value={display} onChange={event => setDisplay(event.target.value)}/></label><label className="settings-label">网页请求 model 参数<input value={upstream} onChange={event => setUpstream(event.target.value)}/></label><p>沿用源项目的网页参数作为初始值。gpt-image-2.5 是对外别名，不代表已经验证底层版本；修改名称不等于升级模型。</p><p>API 地址：<code>{location.origin}/v1</code><br/>支持 images/generations、images/edits 与 models。</p><button className="secondary" onClick={async () => { try { const result = await api<{ api_key: string }>('/api/local-api-key', {}); await navigator.clipboard.writeText(result.api_key); setMessage('本地 API 密钥已复制，请勿分享或提交到 Git'); } catch { setMessage('无法复制密钥，请检查浏览器剪贴板权限'); } }}><Copy size={14}/>复制本地 API 密钥</button></details>
    {message && <div className="settings-message" role="status">{message}</div>}
    <div className="settings-actions"><button className="text-button danger" disabled={working || !settings.configured} onClick={() => { if (confirm('清除本地网页登录凭证？历史图片不会删除。')) save(true); }}>清除凭证</button><div><button className="secondary" disabled={working || !settings.configured || Boolean(token)} onClick={check}>检查已保存连接</button><button className="primary" disabled={working} onClick={() => save()}>{working ? <LoaderCircle size={16} className="spin"/> : '保存设置'}</button></div></div>
  </section></div>;
}
