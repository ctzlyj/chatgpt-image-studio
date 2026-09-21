import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, Check, LoaderCircle, Plus, RefreshCw, Search, Settings2, Upload, Users, X } from 'lucide-react';
import { api, Settings } from './api';
import './accounts.css';

type Account = { id: string; name: string; email: string; plan: string; enabled: boolean; status: string; quota: number | null; restore_at: string | number | null; success: number; fail: number; inflight: number; last_refresh: number | null; error: string; metadata_source?: string };
type Options = { concurrency: number; per_account: number; refresh_minutes: number };
type Pool = { items: Account[]; options: Options; settings: Settings; added?: number; duplicates?: number; refreshed?: number; errors?: { message: string }[] };
type Source = { id: string; kind: 'cpa' | 'sub2api'; name: string; base_url: string; group: string; credential_configured: boolean };
type RemoteItem = { id: string; name: string; email: string };
type Job = { id: string; status: string; total: number; done: number; added: number; duplicates: number; failed: number; errors: { index: number; message: string }[] };
type Sources = { items: Source[]; jobs: Job[] };
const statuses: Record<string, string> = { unverified: '待检查', ready: '可用', limited: '限流 / 额度不足', invalid: '凭证失效', verification: '需网页验证' };
const date = (value: number | null) => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未刷新';
function saveJSON(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Accounts({ close, saved }: { close: () => void; saved: (settings: Settings) => void }) {
  const dialog = useRef<HTMLElement>(null);
  const [pool, setPool] = useState<Pool>();
  const [tab, setTab] = useState<'accounts' | 'sources'>('accounts');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [credential, setCredential] = useState('');
  const [name, setName] = useState('');
  const [editing, setEditing] = useState<Account | null>(null);
  const [options, setOptions] = useState<Options>({ concurrency: 2, per_account: 1, refresh_minutes: 60 });
  const [backup, setBackup] = useState(false);
  const [password, setPassword] = useState('');
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  function accept(result: Pool) { setPool(result); saved(result.settings); }
  async function load() { const result = await api<Pool>('/api/accounts'); accept(result); return result; }
  async function run(operation: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await operation(); } catch (reason) { setError(reason instanceof Error ? reason.message : '操作未完成'); }
    finally { setBusy(false); }
  }
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement as HTMLElement | null;
    document.body.style.overflow = 'hidden';
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
      if (event.key === 'Tab') {
        const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],summary') ?? []).filter(element => element.getClientRects().length > 0);
        const target = event.shiftKey ? controls[controls.length - 1] : controls[0];
        if (document.activeElement === (event.shiftKey ? controls[0] : controls[controls.length - 1])) { event.preventDefault(); target?.focus(); }
      }
    };
    window.addEventListener('keydown', keyboard);
    return () => { document.body.style.overflow = previousOverflow; window.removeEventListener('keydown', keyboard); previousFocus?.focus(); };
  }, []);
  useEffect(() => {
    let mounted = true;
    api<Pool>('/api/accounts').then(result => { if (mounted) { accept(result); setOptions(result.options); } }).catch(reason => { if (mounted) setError(reason.message); });
    const timer = setInterval(() => { api<Pool>('/api/accounts').then(result => { if (mounted) accept(result); }).catch(() => {}); }, 4000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);
  const visible = (pool?.items ?? []).filter(account => `${account.name} ${account.email} ${account.plan}`.toLowerCase().includes(query.toLowerCase()) && (filter === 'all' || (filter === 'disabled' ? !account.enabled : account.enabled && account.status === filter)));
  const selectedExisting = selected.filter(id => pool?.items.some(account => account.id === id));
  const ready = pool?.items.filter(account => account.enabled && ['ready', 'unverified'].includes(account.status) && account.quota !== 0).length ?? 0;
  const toggle = (id: string) => setSelected(previous => previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id]);
  async function action(actionName: string, ids = selectedExisting) {
    if (actionName === 'delete' && !confirm(`删除选中的 ${ids.length} 个账号？历史图片和任务不会删除。`)) return;
    await run(async () => { accept(await api<Pool>('/api/accounts/action', { ids, action: actionName })); setSelected([]); setMessage('账号已更新，历史任务保留'); });
  }
  async function importContent(content: string) {
    const result = await api<Pool>('/api/accounts/import', { content, name });
    accept(result); setCredential(''); setShowImport(false); setName(''); setMessage(`已新增 ${result.added} 个账号，跳过重复 ${result.duplicates} 个`);
  }
  return <div className="account-overlay"><section ref={dialog} className="account-workspace" role="dialog" aria-modal="true" aria-label="号池管理">
    <header className="account-heading"><div><span className="account-eyebrow">画间 / 连接管理</span><h2><Users size={26}/>号池管理</h2><p>管理自己的授权账号。自动分配新任务，不重复提交结果不明的请求。</p></div><button aria-label="关闭号池管理" onClick={close}><X/></button></header>
    <nav className="account-tabs"><button className={tab === 'accounts' ? 'selected' : ''} onClick={() => setTab('accounts')}>账号与调度 <span>{pool?.items.length ?? 0}</span></button><button className={tab === 'sources' ? 'selected' : ''} onClick={() => setTab('sources')}>CPA / sub2api 导入</button></nav>
    {error && <div className="account-error" role="alert">{error}</div>}{message && <div className="account-notice" role="status"><Check size={16}/>{message}</div>}
    {tab === 'sources' ? <SourceManager changed={load}/> : <>
      <div className="account-summary"><div><span>账号总数</span><strong>{pool?.items.length ?? 0}</strong></div><div><span>可调度 / 待检查</span><strong>{ready}</strong></div><div><span>正在执行</span><strong>{pool?.items.reduce((total, account) => total + account.inflight, 0) ?? 0}</strong></div><p>额度未知不等于无限额度。账号限流后暂停分配，刷新确认恢复后再使用。</p></div>
      <div className="account-toolbar"><label className="account-search"><Search size={16}/><input aria-label="搜索账号" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索名称、邮箱或套餐"/></label><select aria-label="筛选账号状态" value={filter} onChange={event => { setFilter(event.target.value); setSelected([]); }}><option value="all">全部状态</option>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value="disabled">已停用</option></select><button className="primary" onClick={() => { setShowImport(!showImport); setEditing(null); }}><Plus size={16}/>导入账号</button></div>
      {showImport && <div className="account-form"><h3>直接粘贴完整 JSON</h3><p>支持会话 JSON、CPA JSON、账号数组，或每行一个凭证。只提取登录凭证，不保存会话中的个人资料。</p><label>账号备注（可选）<input aria-label="导入账号备注" value={name} onChange={event => setName(event.target.value)} maxLength={80}/></label><label>账号 JSON 或凭证<input type="password" autoComplete="new-password" aria-label="号池导入内容" value={credential} onChange={event => setCredential(event.target.value)} onPaste={event => { event.preventDefault(); setCredential(event.clipboardData.getData('text/plain')); }} placeholder="全选复制整个 JSON 粘贴到这里"/></label><div className="account-buttons"><button className="primary" disabled={busy || !credential.trim()} onClick={() => run(() => importContent(credential))}>导入粘贴内容</button><label className="account-file"><Upload size={16}/>选择 JSON 文件（可多选）<input aria-label="导入账号文件" type="file" accept=".json,application/json" multiple disabled={busy} onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ''; run(async () => { if (!files.length) return; if (files.reduce((total, file) => total + file.size, 0) > 4 * 1024 * 1024) throw new Error('文件合计不能超过 4 MB'); const values = await Promise.all(files.map(async file => JSON.parse(await file.text()))); const accounts = values.flatMap(value => Array.isArray(value) ? value : Array.isArray(value.accounts) ? value.accounts : [value]); await importContent(JSON.stringify(accounts)); }); }}/></label><a href="https://chatgpt.com/api/auth/session" target="_blank" rel="noreferrer">打开会话信息页</a></div></div>}
      <div className="account-bulk"><span>已选 {selectedExisting.length} 个</span><button disabled={busy || !selectedExisting.length} onClick={() => run(async () => { const result = await api<Pool>('/api/accounts/refresh', { ids: selectedExisting }); accept(result); setMessage(`已刷新 ${result.refreshed} 个，失败 ${result.errors?.length ?? 0} 个`); })}><RefreshCw size={14}/>刷新选中</button><button disabled={busy || !selectedExisting.length} onClick={() => action('enable')}>启用</button><button disabled={busy || !selectedExisting.length} onClick={() => action('disable')}>停用</button><button disabled={busy || !selectedExisting.length} onClick={() => action('delete')}>删除选中</button><button disabled={busy || !selectedExisting.length} onClick={() => { setBackup(true); setRestoreFile(null); }}><ArrowDownToLine size={14}/>加密导出</button><button onClick={() => { setBackup(true); setRestoreFile(null); }}>恢复备份</button><button disabled={busy || !pool?.items.some(account => account.status === 'invalid' && !account.inflight)} onClick={() => action('delete', pool!.items.filter(account => account.status === 'invalid' && !account.inflight).map(account => account.id))}>清理失效账号</button>{busy && <LoaderCircle className="spin" size={16}/>}</div>
      <div className="account-table-wrap"><table className="account-table"><thead><tr><th><input type="checkbox" aria-label="全选当前筛选账号" checked={visible.length > 0 && visible.every(account => selected.includes(account.id))} onChange={event => setSelected(event.target.checked ? visible.map(account => account.id) : [])}/></th><th>账号</th><th>状态 / 套餐</th><th>图片额度</th><th>成功 / 失败</th><th>最近刷新</th><th>操作</th></tr></thead><tbody>{visible.map(account => <tr key={account.id}><td><input type="checkbox" aria-label={`选择账号 ${account.name}`} checked={selected.includes(account.id)} onChange={() => toggle(account.id)}/></td><td><strong>{account.name}</strong><small>{account.email || '未获取邮箱'}</small><small>编号 {account.id.slice(0, 8)}</small></td><td><span className={`account-status ${account.enabled ? account.status : 'disabled'}`}>{account.enabled ? statuses[account.status] ?? account.status : '已停用'}</span><small>{account.plan || '套餐待检查'}{account.inflight ? ` · 执行中 ${account.inflight}` : ''}</small></td><td>{account.quota === null ? '未知' : account.quota}<small>{account.restore_at ? `上游恢复提示：${account.restore_at}` : '恢复时间未提供'}</small></td><td>{account.success} / {account.fail}</td><td><small>{date(account.last_refresh)}</small>{account.error && <small className="account-warning">{account.error}</small>}</td><td><button disabled={busy || account.inflight > 0} onClick={() => { setEditing(account); setName(account.name); setCredential(''); setShowImport(false); }}>编辑</button></td></tr>)}</tbody></table>{!visible.length && <div className="account-empty"><Users size={32}/><h3>{pool?.items.length ? '没有匹配的账号' : '还没有账号'}</h3><p>{pool?.items.length ? '调整搜索或筛选条件。' : '粘贴整个会话 JSON，或从 CPA / sub2api 导入。'}</p></div>}</div>
      {editing && <div className="account-form"><h3>编辑账号</h3><label>账号名称<input aria-label="编辑账号名称" maxLength={80} value={name} onChange={event => setName(event.target.value)}/></label><label>更新凭证（留空不修改）<input aria-label="更新账号凭证" type="password" autoComplete="new-password" value={credential} onChange={event => setCredential(event.target.value)} onPaste={event => { event.preventDefault(); setCredential(event.clipboardData.getData('text/plain')); }}/></label><details><summary>手动修正套餐与额度</summary><p>仅修正本地记录，不改变上游权限或额度，也不会解除失效、限流或验证状态；下次刷新以上游为准。</p><label>套餐<input aria-label="账号套餐" value={editing.plan} onChange={event => setEditing({ ...editing, plan: event.target.value, metadata_source: 'manual' })}/></label><label>剩余额度（留空表示未知）<input aria-label="账号剩余额度" type="number" min={0} max={1000000} value={editing.quota ?? ''} onChange={event => setEditing({ ...editing, quota: event.target.value === '' ? null : Number(event.target.value), metadata_source: 'manual' })}/></label></details><div className="account-buttons"><button className="primary" disabled={busy} onClick={() => run(async () => { accept(await api<Pool>(`/api/accounts/${editing.id}`, { name, access_token: credential || null, ...(editing.metadata_source === 'manual' ? { plan: editing.plan, quota: editing.quota } : {}) })); setEditing(null); setCredential(''); setMessage('账号已保存'); })}>保存账号</button><button onClick={() => { setEditing(null); setCredential(''); }}>取消编辑</button></div></div>}
      {backup && <div className="account-form"><h3>加密备份 / 恢复</h3><p>导出仅包含加密凭证，不生成明文 Token 文件。请妥善保存备份口令。</p><label>备份口令（至少 12 个字符）<input aria-label="备份口令" type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)}/></label><label>选择已有加密备份<input aria-label="恢复备份文件" type="file" accept=".json" onChange={event => setRestoreFile(event.target.files?.[0] ?? null)}/></label><div className="account-buttons"><button disabled={busy || password.length < 12 || !selectedExisting.length} onClick={() => run(async () => { const result = await api('/api/accounts/backup', { ids: selectedExisting, password }); saveJSON(result, '画间-账号加密备份.json'); setPassword(''); setMessage('加密备份已下载'); })}>导出选中账号</button><button className="primary" disabled={busy || !restoreFile || password.length < 12} onClick={() => run(async () => { if (restoreFile!.size > 8 * 1024 * 1024) throw new Error('备份文件超过 8 MB'); const result = await api<Pool>('/api/accounts/restore', { backup: JSON.parse(await restoreFile!.text()), password }); accept(result); setPassword(''); setRestoreFile(null); setBackup(false); setMessage(`已恢复 ${result.added} 个账号，跳过重复 ${result.duplicates} 个`); })}>恢复到号池</button><button onClick={() => { setBackup(false); setPassword(''); setRestoreFile(null); }}>关闭备份</button></div></div>}
      <details className="account-options"><summary><Settings2 size={16}/>调度与定时刷新</summary><div><label>总并发任务<input type="number" min={1} max={8} value={options.concurrency} onChange={event => setOptions({ ...options, concurrency: Number(event.target.value) })}/></label><label>单账号并发<input type="number" min={1} max={3} value={options.per_account} onChange={event => setOptions({ ...options, per_account: Number(event.target.value) })}/></label><label>刷新间隔（分钟，0 关闭）<input type="number" min={0} max={1440} value={options.refresh_minutes} onChange={event => setOptions({ ...options, refresh_minutes: Number(event.target.value) })}/></label><button className="secondary" disabled={busy} onClick={() => run(async () => { accept(await api<Pool>('/api/accounts/options', options)); setMessage('调度设置已保存'); })}>保存调度设置</button></div><p>只为新任务轮询可用账号；失效账号自动停止分配，限流账号定时刷新。单账号并发不能突破上游额度。关闭程序后定时刷新停止。</p></details>
    </>}
  </section></div>;
}

function SourceManager({ changed }: { changed: () => Promise<unknown> }) {
  const [data, setData] = useState<Sources>({ items: [], jobs: [] });
  const [editingId, setEditingId] = useState('');
  const [form, setForm] = useState({ kind: 'cpa', name: '', base_url: '', secret: '', email: '', password: '', group: '' });
  const [sourceId, setSourceId] = useState('');
  const [remote, setRemote] = useState<RemoteItem[]>([]);
  const [groups, setGroups] = useState<RemoteItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function load() { setData(await api<Sources>('/api/account-sources')); }
  useEffect(() => { load().catch(reason => setError(reason.message)); const timer = setInterval(() => load().catch(() => {}), 3000); return () => clearInterval(timer); }, []);
  async function run(operation: () => Promise<void>) { setBusy(true); setError(''); setMessage(''); try { await operation(); } catch (reason) { setError(reason instanceof Error ? reason.message : '远程请求失败'); } finally { setBusy(false); } }
  const visible = remote.filter(item => `${item.name} ${item.email}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="account-sources">{error && <div role="alert" className="account-error">{error}</div>}{message && <div role="status" className="account-notice">{message}</div>}
    <div className="account-form"><h3>{editingId ? '编辑导入服务器' : '添加导入服务器'}</h3><p>管理凭证仅加密保存在本机，不显示在列表中。只读取你选中的 OpenAI 账号，不修改远程服务器。</p><div className="source-fields"><label>服务类型<select aria-label="导入服务类型" value={form.kind} onChange={event => setForm({ ...form, kind: event.target.value, secret: '', password: '' })}><option value="cpa">CPA</option><option value="sub2api">sub2api</option></select></label><label>服务器名称<input aria-label="服务器名称" maxLength={80} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })}/></label><label>服务器地址<input aria-label="服务器地址" value={form.base_url} onChange={event => setForm({ ...form, base_url: event.target.value })} placeholder="https://你的服务器"/></label><label>{form.kind === 'cpa' ? 'CPA 管理密钥' : 'sub2api 管理 API Key'}<input aria-label="服务器管理密钥" type="password" autoComplete="new-password" value={form.secret} onChange={event => setForm({ ...form, secret: event.target.value })} placeholder={editingId ? '留空保留；修改地址后需重新填写' : ''}/></label>{form.kind === 'sub2api' && <><label>管理员邮箱（未填 Key 时使用）<input type="password" autoComplete="off" value={form.email} onChange={event => setForm({ ...form, email: event.target.value })}/></label><label>管理员密码<input type="password" autoComplete="new-password" value={form.password} onChange={event => setForm({ ...form, password: event.target.value })}/></label><label>分组 ID（可选）<input aria-label="远程分组 ID" value={form.group} onChange={event => setForm({ ...form, group: event.target.value })}/></label></>}</div><div className="account-buttons"><button className="primary" disabled={busy || !form.name || !form.base_url} onClick={() => run(async () => { setData(await api<Sources>('/api/account-sources' + (editingId ? `/${editingId}` : ''), { ...form, secret: form.secret || null, email: form.email || null, password: form.password || null })); setForm({ ...form, secret: '', email: '', password: '', name: '', base_url: '', group: '' }); setEditingId(''); setRemote([]); setSelected([]); setMessage('服务器配置已加密保存'); })}>保存服务器</button>{editingId && <button onClick={() => { setEditingId(''); setForm({ kind: 'cpa', name: '', base_url: '', secret: '', email: '', password: '', group: '' }); }}>取消编辑服务器</button>}</div></div>
    <div className="source-list">{data.items.map(source => <div key={source.id}><div><strong>{source.name}</strong><small>{source.kind} · {source.base_url}</small></div><div className="account-buttons"><button disabled={busy} onClick={() => run(async () => { setSourceId(source.id); setRemote([]); setSelected([]); setGroups([]); setRemote((await api<{ items: RemoteItem[] }>(`/api/account-sources/${source.id}/items`)).items); })}>读取账号列表</button>{source.kind === 'sub2api' && <button disabled={busy} onClick={() => run(async () => { setGroups((await api<{ items: RemoteItem[] }>(`/api/account-sources/${source.id}/groups`)).items); })}>查看分组</button>}<button onClick={() => { setEditingId(source.id); setForm({ ...source, secret: '', email: '', password: '' }); }}>编辑服务器</button><button disabled={busy} onClick={() => { if (confirm('删除此服务器配置？已导入账号不受影响。')) run(async () => { setData(await api<Sources>(`/api/account-sources/${source.id}`, undefined, 'DELETE')); if (sourceId === source.id) { setRemote([]); setSourceId(''); setSelected([]); } }); }}>删除服务器</button></div></div>)}</div>
    {groups.length > 0 && <div className="account-notice">分组：{groups.map(group => `${group.name}（${group.id}）`).join('、')}。在编辑服务器中填写分组 ID 后重新读取。</div>}
    {sourceId && <div className="remote-accounts"><div className="account-toolbar"><h3>远程账号 · {remote.length}</h3><input aria-label="搜索远程账号" placeholder="搜索名称或邮箱" value={query} onChange={event => setQuery(event.target.value)}/><button className="primary" disabled={busy || !selected.length} onClick={() => run(async () => { await api(`/api/account-sources/${sourceId}/import`, { ids: selected }); setSelected([]); await load(); setMessage('导入已在后台开始，可查看下方进度'); })}>导入选中（{selected.length}）</button></div><label><input type="checkbox" aria-label="全选远程筛选结果" checked={visible.length > 0 && visible.every(item => selected.includes(item.id))} onChange={event => setSelected(event.target.checked ? visible.slice(0, 500).map(item => item.id) : [])}/>全选筛选结果（每批最多 500）</label><div className="remote-items">{visible.map(item => <label key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={() => setSelected(previous => previous.includes(item.id) ? previous.filter(id => id !== item.id) : previous.length < 500 ? [...previous, item.id] : previous)}/><span>{item.name}<small>{item.email}</small></span></label>)}</div></div>}
    {data.jobs.length > 0 && <div className="account-form"><h3>导入进度</h3>{data.jobs.map(job => <div className="import-job" key={job.id}><strong>{{ queued: '等待中', running: '导入中', completed: '已完成', failed: '失败', interrupted: '已中断' }[job.status] ?? job.status} · {job.done}/{job.total}</strong><p>新增 {job.added} · 重复 {job.duplicates} · 失败 {job.failed}</p>{job.errors.length > 0 && <details><summary>查看失败清单</summary>{job.errors.map((item, index) => <p key={index}>第 {item.index || '—'} 条：{item.message}</p>)}</details>}</div>)}<button onClick={() => run(async () => { await changed(); setMessage('本地号池已更新，可切回账号与调度查看'); })}>刷新本地号池</button><p>重复导入会自动去重。关闭服务会中断尚未完成的导入；已成功导入的账号会保留。</p></div>}{busy && <p role="status"><LoaderCircle size={16} className="spin"/> 正在请求远程服务器…</p>}
  </div>;
}
