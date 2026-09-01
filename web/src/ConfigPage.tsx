import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { validateAsr } from './api'
import { useAppState } from './state'
import type { AsrConfig, Provider } from './types'

export default function ConfigPage() {
  const { setAsr } = useAppState()
  const navigate = useNavigate()
  const [provider, setProvider] = useState<Provider>('tencent')
  const [values, setValues] = useState<Record<string, string>>({})
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const fields = provider === 'tencent'
    ? [['secret_id', 'SecretId'], ['secret_key', 'SecretKey'], ['app_id', 'AppId'], ['region', '地域'], ['engine_model', '引擎模型']]
    : [['access_key_id', 'AccessKey ID'], ['access_key_secret', 'AccessKey Secret'], ['app_key', 'AppKey'], ['region', '地域'], ['model', '模型']]

  const config = (): AsrConfig | undefined => {
    const required = fields.slice(0, 3)
    if (required.some(([key]) => !values[key]?.trim())) return undefined
    return {
      provider,
      credentials: Object.fromEntries(required.map(([key]) => [key, values[key]])),
      options: Object.fromEntries(fields.slice(3).filter(([key]) => values[key]).map(([key]) => [key, values[key]])),
    }
  }
  const testConnection = async () => {
    const next = config()
    if (!next) return setStatus('请填写完整凭据')
    setBusy(true)
    try { await validateAsr(next); setStatus('供应商连接和凭据验证通过') }
    catch (error) { setStatus((error as Error).message) }
    finally { setBusy(false) }
  }
  const useForTask = () => {
    const next = config()
    if (!next) return setStatus('请填写完整凭据')
    setAsr(next)
    navigate('/')
  }

  return <main>
    <header><span className="eyebrow">临时云 ASR 配置</span><h1>让凭据只停留在这次会话</h1>
      <p>刷新页面即丢失。服务端只在活动任务内存中使用，不写入任务目录或日志。</p></header>
    <section className="card">
      <label>供应商<select value={provider} onChange={event => { setProvider(event.target.value as Provider); setValues({}); setStatus('') }}>
        <option value="tencent">腾讯云</option><option value="aliyun">阿里云</option></select></label>
      <div className="grid">{fields.map(([key, label], index) => <label key={key}>{label}<input type={index === 1 ? 'password' : 'text'} value={values[key] || ''} onChange={event => setValues({ ...values, [key]: event.target.value })} autoComplete="off" /></label>)}</div>
      <p className="hint">{status}</p><div className="actions">
        <button disabled={busy} onClick={testConnection}>{busy ? '正在连接…' : '测试供应商连接'}</button>
        <button disabled={busy} onClick={useForTask}>用于本次任务</button>
      </div>
    </section>
  </main>
}
