import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { cancelTask, deleteTask, exportUrl, getResult, getTask } from './api'
import type { Result, Task } from './types'

const terminal = new Set(['succeeded', 'failed', 'cancelled', 'expired'])
const colors = ['#58a6ff', '#d2a8ff', '#3fb950', '#f0883e', '#ff7b72', '#a5d6ff']

export default function TaskPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [task, setTask] = useState<Task>()
  const [result, setResult] = useState<Result>()
  const [current, setCurrent] = useState(-1)
  const audio = useRef<HTMLAudioElement>(null)
  const speakers = useMemo(() => new Map<string, string>(), [])

  useEffect(() => {
    let stopped = false
    const poll = async () => {
      try {
        const next = await getTask(id)
        if (stopped) return
        setTask(next)
        if (next.status === 'succeeded') setResult(await getResult(id))
        if (!terminal.has(next.status)) setTimeout(poll, 900)
      } catch { /* retain the last trustworthy state */ }
    }
    void poll()
    return () => { stopped = true }
  }, [id])

  const color = (speaker: string) => {
    if (!speakers.has(speaker)) speakers.set(speaker, colors[speakers.size % colors.length])
    return speakers.get(speaker)
  }
  const sync = () => {
    const ms = (audio.current?.currentTime || 0) * 1000
    setCurrent(result?.segments.findIndex(segment => segment.start_ms <= ms && ms < segment.end_ms) ?? -1)
  }

  return <main>
    <nav><Link to="/">← 新任务</Link><span className="mono">{id}</span></nav>
    <section className="status"><span className={`dot ${task?.status}`} /><div>
      <small>{task?.input_mode === 'cloud_asr' ? `${task.asr_provider} 云转写` : '已有转写'}</small>
      <h2>{task?.status || '正在读取'} · {task?.stage || '等待阶段'}</h2>
      {task?.error && <p className="error">{task.error.code}：{task.error.message}</p>}
    </div></section>
    {result && <>
      <section className="card">
        <audio ref={audio} controls src={`/v1/tasks/${id}/audio`} onTimeUpdate={sync} />
        <div className="actions">{['json', 'txt', 'srt', 'vtt'].map(format => <a key={format} href={exportUrl(id, format)}>下载 {format.toUpperCase()}</a>)}</div>
        {result.status === 'partial' && <p className="warning">结果包含局部失败，请结合警告复核。</p>}
      </section>
      <section className="transcript">{result.segments.map((segment, index) => {
        const label = segment.assignment?.label || 'unknown'
        return <article key={segment.id} className={index === current ? 'current' : ''} onClick={() => {
          if (audio.current) { audio.current.currentTime = segment.start_ms / 1000; void audio.current.play() }
        }}><i style={{ background: label === 'unknown' ? '#667085' : color(label) }} /><div>
          <b>{label}</b><time>{(segment.start_ms / 1000).toFixed(1)}–{(segment.end_ms / 1000).toFixed(1)}s</time>
          <p>{segment.text}</p><small>{label === 'unknown' ? '未可靠归属' : `置信 ${segment.assignment?.confidence?.toFixed(3) ?? '—'} · 风险 ${segment.assignment?.risk?.level || '—'}`}</small>
        </div></article>
      })}</section>
    </>}
    {task && !terminal.has(task.status) && <button className="danger" onClick={async () => setTask(await cancelTask(id))}>取消任务</button>}
    {task && terminal.has(task.status) && <button className="danger" onClick={async () => { await deleteTask(id); navigate('/') }}>删除任务数据</button>}
  </main>
}
