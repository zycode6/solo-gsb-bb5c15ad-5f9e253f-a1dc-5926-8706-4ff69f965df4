import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { fmtTime } from '../utils.js'

/** Split the textarea content into one sensitive word per non-empty line. */
export function parseWords(text) {
  return text.split('\n').map((w) => w.trim()).filter(Boolean)
}

/** Unique speakers of the SAVED segments, in first-appearance order. */
function savedSpeakers(segments) {
  const names = []
  const seen = new Set()
  for (const seg of segments || []) {
    const name = (seg.speaker || '').trim()
    if (name && !seen.has(name)) {
      seen.add(name)
      names.push(name)
    }
  }
  return names
}

/** Validate inputs; '' means valid. */
export function validateInputs(words, selected) {
  if (words.length === 0 && selected.length === 0) {
    return '请至少输入一个敏感词，或勾选一位需匿名的说话人'
  }
  return ''
}

export default function PublicTranscriptPage() {
  const { id: projectId } = useParams()
  const [projectName, setProjectName] = useState('')
  const [speakerList, setSpeakerList] = useState([])
  const [wordsText, setWordsText] = useState('')
  const [selected, setSelected] = useState([])
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [preview, setPreview] = useState(null)
  const [stamp, setStamp] = useState('') // input signature the preview is based on
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    api.getProject(projectId)
      .then((p) => {
        setProjectName(p.name)
        setSpeakerList(savedSpeakers(p.document?.segments))
      })
      .catch(() => setNotFound(true))
  }, [projectId])

  const words = useMemo(() => parseWords(wordsText), [wordsText])
  const validationError = useMemo(
    () => validateInputs(words, selected), [words, selected])
  const currentStamp = useMemo(
    () => JSON.stringify({
      words: [...new Set(words.map((w) => w.replace(/\s+/g, '')))]
        .filter(Boolean),
      speakers: [...selected].sort(),
      caseSensitive,
    }),
    [words, selected, caseSensitive])
  const stale = !preview || stamp !== currentStamp

  if (notFound) {
    return (
      <div className="page">
        <div className="error">项目不存在。</div>
        <Link className="btn" to="/">← 返回项目列表</Link>
      </div>
    )
  }

  const toggleSpeaker = (name) => {
    setSelected((list) => (list.includes(name)
      ? list.filter((n) => n !== name)
      : [...list, name]))
  }

  const buildBody = () => ({
    sensitiveWords: words,
    speakers: selected,
    caseSensitive,
  })

  const runPreview = async () => {
    if (validationError) { setError(validationError); return }
    setError(''); setLoading(true)
    try {
      const data = await api.previewPublicTranscript(projectId, buildBody())
      setPreview(data)
      setStamp(currentStamp)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const download = async () => {
    if (validationError) { setError(validationError); return }
    setError(''); setDownloading(true)
    try {
      const res = await fetch(api.publicTranscriptDownloadUrl(projectId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildBody()),
      })
      if (!res.ok) {
        let detail = `下载失败 (${res.status})`
        try { detail = (await res.json()).detail || detail } catch { /* */ }
        throw new Error(detail)
      }
      const blob = await res.blob()
      const filename = filenameFromDisposition(res.headers.get('content-disposition'))
        || `${preview?.projectName || projectId}-公开版转写.json`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    } finally {
      setDownloading(false)
    }
  }

  const s = preview?.summary

  return (
    <div className="page pub-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>公开版转写{projectName ? ` · ${projectName}` : ''}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="warn-box">
        公开版转写为<strong>只读导出</strong>，基于项目<strong>已保存</strong>的转写
        生成（编辑页有未保存修改时请先保存）：命中的敏感词逐字替换为方块
        <code>█</code>（原文空白保留），勾选的说话人统一改为匿名编号；下载的 JSON
        <strong>只含转写片段</strong>（保留片段 ID、起止时间与分段，不含校注），
        原项目不会被修改。
      </div>

      <div className="panel pub-inputs">
        <h3>
          敏感词
          <span className="muted small sp-panel-hint">
            每行一个；匹配忽略空白，可跨越时间相接且说话人相同的片段，
            遇到空隙、重叠或换人即断开。
          </span>
        </h3>
        <textarea
          className="pub-words"
          rows={5}
          placeholder={'每行输入一个敏感词，例如：\n李明\n身份证号'}
          value={wordsText}
          onChange={(e) => setWordsText(e.target.value)}
        />
        <label className="quote-case" title="区分大小写">
          <input
            type="checkbox"
            checked={caseSensitive}
            onChange={(e) => setCaseSensitive(e.target.checked)}
          />
          区分大小写
        </label>

        <h3 className="pub-speakers-head">
          需匿名的说话人
          <span className="muted small sp-panel-hint">
            勾选后在预览与下载中统一替换为「说话人1 / 说话人2 …」，编号按说话人
            在时间顺序上的首次出现确定；不勾选的说话人名称原样保留。
          </span>
        </h3>
        {speakerList.length === 0 ? (
          <div className="muted small">已保存的转写片段中还没有说话人。</div>
        ) : (
          <div className="pub-speaker-picker">
            {speakerList.map((name) => {
              const active = selected.includes(name)
              return (
                <button
                  key={name}
                  type="button"
                  className={`pub-speaker-chip${active ? ' active' : ''}`}
                  onClick={() => toggleSpeaker(name)}
                >
                  {active ? '☑' : '☐'} {name}
                </button>
              )
            })}
          </div>
        )}

        <div className="sp-map-actions" style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn primary"
            disabled={!!validationError || loading}
            onClick={runPreview}
          >
            {loading ? '预览中…' : '🔍 生成预览'}
          </button>
        </div>
        {validationError && <div className="error">{validationError}</div>}
      </div>

      {preview && (
        <>
          <div className="panel pub-summary">
            <div className="preview-head">
              <h3 style={{ margin: 0 }}>预览结果</h3>
              {stale && (
                <span className="badge warn">输入已修改，请重新生成预览</span>
              )}
            </div>
            <div className="stats-grid">
              <div className="stat-box">
                <strong>转写片段</strong><span>{s.segmentCount}</span>
              </div>
              <div className="stat-box">
                <strong>敏感词</strong><span>{s.sensitiveWordCount}</span>
              </div>
              <div className={`stat-box ${s.sensitiveWordHits ? 'has-conflict' : ''}`}>
                <strong>敏感词命中</strong><span>{s.sensitiveWordHits}</span>
              </div>
              <div className={`stat-box ${s.maskedSegments ? 'has-conflict' : ''}`}>
                <strong>遮盖片段</strong><span>{s.maskedSegments}</span>
              </div>
              <div className={`stat-box ${s.maskedCharacters ? 'has-conflict' : ''}`}>
                <strong>遮盖字符</strong><span>{s.maskedCharacters}</span>
              </div>
              <div className={`stat-box ${s.anonymousSpeakerCount ? 'has-conflict' : ''}`}>
                <strong>匿名说话人</strong><span>{s.anonymousSpeakerCount}</span>
              </div>
            </div>

            {preview.anonymousSpeakers.length > 0 && (
              <div className="pub-anon-map">
                <h4>说话人匿名编号（预览与下载一致）</h4>
                <div className="pub-anon-list">
                  {preview.anonymousSpeakers.map((sp) => (
                    <span key={sp.name} className="tag pub-anon-tag">
                      {sp.name} → {sp.label}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="sp-confirm-row">
              <button
                className="btn primary big"
                disabled={stale || downloading}
                onClick={download}
              >
                {downloading ? '下载中…' : '⬇️ 确认并下载公开版转写 JSON'}
              </button>
              <span className="muted small">
                {stale
                  ? '输入有改动，请先重新生成预览。'
                  : 'JSON 只含片段（id / start / end / speaker / text）与匿名映射；'
                    + '原项目不会被修改。'}
              </span>
            </div>
          </div>

          <div className="panel pub-preview">
            <h3>
              公开版片段（{preview.segments.length}）
              <span className="muted small sp-panel-hint">
                方块 <code>█</code> 为被遮盖的命中字符，空白与分段原样保留。
              </span>
            </h3>
            {preview.segments.length === 0 ? (
              <div className="empty-hint">已保存的转写中没有可用片段。</div>
            ) : (
              <div className="quote-hit-list pub-seg-list">
                {preview.segments.map((seg, i) => (
                  <div key={`${seg.id || 'seg'}-${i}`} className="hit-item">
                    <div className="hit-range">
                      <span className="exc-time">
                        {fmtTime(seg.start)} – {fmtTime(seg.end)}
                      </span>
                      {seg.speaker ? ` · ${seg.speaker}` : ''}
                      {seg.id ? <span className="muted small"> · {seg.id}</span> : null}
                    </div>
                    <div className="hit-text pub-seg-text">
                      <MaskedText text={seg.text} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/** Render public-segment text with the block glyph █ highlighted. */
function MaskedText({ text }) {
  const parts = text.split(/(█+)/).filter((p) => p.length > 0)
  return (
    <span>
      {parts.map((p, i) => (p.startsWith('█')
        ? <mark key={i} className="pub-mask">{p}</mark>
        : <span key={i}>{p}</span>))}
    </span>
  )
}

function filenameFromDisposition(disposition) {
  if (!disposition) return ''
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8) {
    try { return decodeURIComponent(utf8[1].trim()) } catch { /* fall through */ }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i)
  return plain ? plain[1] : ''
}
