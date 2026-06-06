import React, { useState } from 'react'

const MODE_ICONS = { taxi: '🚕', driving: '🚗', walking: '🚶', transit: '🚌' }
const MODE_LABELS = { taxi: '打车', driving: '开车', walking: '步行', transit: '公交' }

// ── Soft time helpers ─────────────────────────────────────────────────

function timePeriod(hhmm) {
  if (!hhmm) return ''
  const h = parseInt(hhmm.split(':')[0], 10)
  if (isNaN(h)) return ''
  if (h < 6)  return '凌晨'
  if (h < 12) return '上午'
  if (h < 13) return '中午'
  if (h < 18) return '下午'
  if (h < 20) return '傍晚'
  return '晚上'
}

function softTime(hhmm) {
  // Round minutes to nearest 5 for a "soft" feel
  if (!hhmm) return ''
  const [hStr, mStr] = hhmm.split(':')
  const h = parseInt(hStr, 10)
  const m = parseInt(mStr, 10)
  if (isNaN(h) || isNaN(m)) return hhmm
  const rm = Math.round(m / 5) * 5
  const rh = rm === 60 ? h + 1 : h
  const fm = rm === 60 ? 0 : rm
  return `${rh}:${fm.toString().padStart(2, '0')}`
}

const CLOSING_RE = /(\d{1,2})[：:点](\d{0,2})\s*[闭关停]馆/

function closingWarning(riskFacts) {
  if (!riskFacts?.length) return null
  for (const fact of riskFacts) {
    if (CLOSING_RE.test(fact)) return fact
  }
  return null
}

function InlineTransitBar({ transit, isFirst, onModeChange }) {
  const [mode, setMode] = useState(transit.mode || 'taxi')
  const [dur, setDur] = useState(transit.duration_min || 12)

  const handleChange = (newMode) => {
    setMode(newMode)
    const base = transit.duration_min || 12
    const dist = transit.distance_km || 2.5
    const durMap = {
      taxi:    base,
      driving: Math.max(5, Math.round(base * 0.75)),
      transit: Math.max(8, Math.round(base * 1.3)),
      walking: Math.max(10, Math.round(dist * 12)),
    }
    setDur(durMap[newMode])
    if (onModeChange) onModeChange({ ...transit, mode: newMode, duration_min: durMap[newMode] })
  }

  return (
    <div className="nc-transit-bar">
      <div className="nc-transit-line" />
      <div className="nc-transit-content">
        <span className="nc-transit-icon">{MODE_ICONS[mode]}</span>
        <span className="nc-transit-info">
          {isFirst
            ? `从出发地 · ${MODE_LABELS[mode]} · 约${dur}分钟`
            : `${MODE_LABELS[mode]} · 约${dur}分钟 · ${(transit.distance_km || 2.5).toFixed(1)}km`}
        </span>
        <div className="nc-transit-modes">
          {Object.entries(MODE_ICONS).map(([m, icon]) => (
            <button
              key={m}
              className={`nc-transit-chip ${mode === m ? 'active' : ''}`}
              onClick={() => handleChange(m)}
              title={MODE_LABELS[m]}
            >
              {icon}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function NodeCard({ node, idx, onAction, onSelectAlt, alternatives }) {
  // 使用真实 alternatives，按 category 筛选（restaurant → 只显示餐厅，其他显示活动）
  const altList = (alternatives || []).filter(a =>
    node.type === 'restaurant'
      ? a.category === 'restaurant'
      : a.category !== 'restaurant'
  ).slice(0, 5)

  const isPinned    = node.user_pinned || node.pinned
  const isCompleted = node.completed_lock

  const period  = timePeriod(node.timeStart)
  const tStart  = softTime(node.timeStart)
  const tEnd    = softTime(node.timeEnd)
  const closing = closingWarning(node.risk_facts)

  return (
    <div className={`node-card ${isPinned ? 'pinned' : ''} ${node.locked ? 'locked' : ''} ${isCompleted ? 'completed' : ''}`}>
      {/* Timeline dot */}
      <div className="nc-dot-wrap">
        <div className={`nc-dot ${node.status === 'optional' ? 'opt' : isPinned ? 'pin' : isCompleted ? 'done' : ''}`}>
          {idx + 1}
        </div>
      </div>

      <div className="nc-body">
        {/* Header */}
        <div className="nc-header">
          <span className="nc-icon">{node.icon}</span>
          <div className="nc-title-block">
            <div className="nc-time">
              {period && <span className="nc-period">{period}</span>}
              <span className="nc-time-range">约 {tStart}–{tEnd}</span>
              {node.status === 'optional' && <span className="nc-badge opt">可选</span>}
              {isCompleted && <span className="nc-badge done">✅ 已完成</span>}
              {isPinned && !isCompleted && <span className="nc-badge pin">📌 已锁定</span>}
              {node.locked && !isCompleted && <span className="nc-badge lock">🔒 已预约</span>}
            </div>
            <div className="nc-name">{node.name}</div>
            <div className="nc-sub">{node.sub}</div>
          </div>
        </div>

        {/* Closing time warning */}
        {closing && (
          <div className="nc-closing-warn">
            ⏰ {closing}，请注意时间安排
          </div>
        )}

        {/* Chips */}
        <div className="nc-chips">
          {node.distance && (
            <span className="nc-chip">📍 {node.distance}</span>
          )}
          {node.queueText != null && node.queueText !== '' && (
            <span className={`nc-chip ${(node.queueMin || 0) > 30 ? 'warn' : 'ok'}`}>
              ⏱ {node.queueText}
            </span>
          )}
          {node.price && node.price !== '0' && (
            <span className="nc-chip">💰 {node.price}</span>
          )}
          {!!node.rating && (
            <span className="nc-chip star">⭐ {node.rating}</span>
          )}
        </div>

        {/* Tags */}
        {node.tags?.length > 0 && (
          <div className="nc-tags">
            {node.tags.slice(0, 3).map(t => <span key={t} className="nc-tag">{t}</span>)}
          </div>
        )}

        {/* Reason */}
        {node.reason && <div className="nc-reason">{node.reason}</div>}

        {/* Actions — only replace + pin, no delete */}
        {!isCompleted && (
          <div className="nc-actions">
            <button className="nc-btn replace" onClick={() => onAction(node.id, 'replace')}>
              🔄 换一个
            </button>
            <button className={`nc-btn pin ${isPinned ? 'active' : ''}`}
                    onClick={() => onAction(node.id, 'pin')}>
              📌 {isPinned ? '取消锁定' : '锁定'}
            </button>
          </div>
        )}

        {/* Alternatives panel */}
        {node._showAlts && (
          <div className="alt-panel">
            <div className="alt-panel-title">附近备选方案</div>
            {altList.length === 0 && (
              <div className="alt-item disabled" style={{ opacity: 0.5, cursor: 'default' }}>
                暂无其他备选
              </div>
            )}
            {altList.map((a, i) => (
              <div key={a.poiId || i} className="alt-item"
                   onClick={() => onSelectAlt?.(node.id, a.poiId)}>
                <span className="alt-icon">{a.icon || '📍'}</span>
                <div className="alt-info">
                  <div className="alt-name">{a.name}</div>
                  <div className="alt-meta">{a.sub || `${(a.distanceKm || 0).toFixed(1)}km`} · {a.queueText || '可预约'}</div>
                </div>
                <div className="alt-rating">{a.rating ? `⭐ ${a.rating}` : ''}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function ItineraryCards({ nodes, summary, onNodeAction, onSelectAlt, alternatives, onTransitChange }) {
  if (!nodes?.length) return null

  return (
    <div className="itinerary-wrap">
      {summary && (
        <div className="itin-summary">
          <span className="itin-summary-icon">💡</span>
          <span>{summary}</span>
        </div>
      )}
      <div className="itin-nodes">
        {nodes.map((node, i) => (
          <React.Fragment key={node.id}>
            {node.transit && (
              <InlineTransitBar
                transit={node.transit}
                isFirst={i === 0}
                onModeChange={onTransitChange
                  ? (tData) => onTransitChange(node.id, tData)
                  : undefined}
              />
            )}
            <NodeCard node={node} idx={i} onAction={onNodeAction} onSelectAlt={onSelectAlt} alternatives={alternatives} />
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}
