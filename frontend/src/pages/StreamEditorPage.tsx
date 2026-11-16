import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { CampaignStreams, Offer, Stream, StreamOffer } from '../api/types'
import { Banner } from '../components/Banner'
import { OfferPicker } from '../components/OfferPicker'
import { errorMessage, formatDate } from '../lib/format'

// Values must match Keitaro's report/build interval names exactly.
const INTERVALS = [
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'this_week', label: 'This week' },
  { value: '7_days_ago', label: 'Last 7 days' },
  { value: '30_days_ago', label: 'Last 30 days' },
  { value: 'this_month', label: 'This month' },
  { value: 'last_month', label: 'Last month' },
  { value: 'all_time', label: 'All time' },
]

export function StreamEditorPage() {
  const { campaignId = '' } = useParams()
  const [data, setData] = useState<CampaignStreams | null>(null)
  const [statsInterval, setStatsInterval] = useState('today')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  /** Run one call that returns the whole campaign view, with a shared in-flight flag. */
  const run = useCallback(
    async (action: () => Promise<CampaignStreams>, successMessage?: string) => {
      setBusy(true)
      setError(null)
      setNotice(null)
      try {
        setData(await action())
        if (successMessage) setNotice(successMessage)
      } catch (caught) {
        setError(errorMessage(caught))
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  useEffect(() => {
    void run(() => api.streams(campaignId, statsInterval))
  }, [campaignId, statsInterval, run])

  async function push() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await api.push(campaignId)
      setData(await api.streams(campaignId, statsInterval))
      setNotice(
        result.pushed_streams === 0
          ? 'Nothing to push — Keitaro is already up to date.'
          : `Pushed ${result.pushed_streams} stream(s) to Keitaro: ${result.applied.join('; ')}`,
      )
    } catch (caught) {
      setError(errorMessage(caught))
      // A refused push leaves the staged changes intact; re-read so the table matches.
      try {
        setData(await api.streams(campaignId, statsInterval))
      } catch {
        /* keep the push error, which is the useful one */
      }
    } finally {
      setBusy(false)
    }
  }

  if (!data) {
    return (
      <div className="page">
        {error ? <Banner kind="error">{error}</Banner> : <div className="empty-state">Loading…</div>}
      </div>
    )
  }

  const { campaign, streams, has_pending_changes: pending } = data

  return (
    <div className="page">
      <div className="breadcrumbs">
        <Link to="/">Campaigns</Link> / <b>{campaign.name}</b> / Keitaro streams
      </div>

      <section className="card">
        <div className="toolbar">
          <button
            data-testid="fetch-from-kt"
            disabled={busy}
            onClick={() => {
              if (
                pending &&
                !window.confirm('Fetching from Keitaro discards your pending changes. Continue?')
              ) {
                return
              }
              void run(
                () => api.fetchStreams(campaignId, statsInterval),
                'Reloaded from Keitaro.',
              )
            }}
          >
            ⟳ Fetch streams from KT
          </button>
          <a href={data.keitaro_url} target="_blank" rel="noreferrer">
            <button>↗ View in KT</button>
          </a>
          <div className="spacer" />
          <label htmlFor="interval" style={{ margin: 0 }}>
            Stats
          </label>
          <select
            id="interval"
            data-testid="interval"
            style={{ width: 150 }}
            value={statsInterval}
            disabled={busy}
            onChange={(event) => setStatsInterval(event.target.value)}
          >
            {INTERVALS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {busy && <span className="spinner" data-testid="busy" />}
        </div>

        <div className="kv">
          <span>
            Keitaro&nbsp;<b>#{campaign.keitaro_id}</b>
          </span>
          <span>
            Geo&nbsp;<b>{campaign.geo ?? '—'}</b>
          </span>
          <span>
            Group&nbsp;<b>{campaign.group_name ?? '—'}</b>
          </span>
          <span>
            Source&nbsp;<b>{campaign.traffic_source_name ?? '—'}</b>
          </span>
          <span>
            Domain&nbsp;<b>{campaign.domain_name ?? '—'}</b>
          </span>
          <span>
            Fetched&nbsp;<b>{formatDate(campaign.streams_fetched_at)}</b>
          </span>
        </div>
      </section>

      {error && <Banner kind="error">{error}</Banner>}
      {notice && !error && <Banner kind="ok">{notice}</Banner>}

      {pending && (
        <div className="pending-bar" data-testid="pending-bar">
          <span className="text">You have unsaved changes.</span>
          <span className="muted">Nothing is sent to Keitaro until you push.</span>
          <div className="spacer" />
          <button className="primary" data-testid="push-to-kt" disabled={busy} onClick={push}>
            ⬆ Push to KT
          </button>
          <button
            data-testid="cancel-changes"
            disabled={busy}
            onClick={() =>
              void run(() => api.reset(campaignId, statsInterval), 'Pending changes discarded.')
            }
          >
            ✕ Cancel
          </button>
        </div>
      )}

      <section className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table data-testid="streams-table">
          <thead>
            <tr>
              <th style={{ width: '38%' }}>Offer</th>
              <th className="num" style={{ width: 130 }}>
                Share
              </th>
              <th className="num">Clicks</th>
              <th className="num">Unique</th>
              <th className="num">Bots</th>
              <th className="num">Conv.</th>
              <th className="num">Revenue</th>
              <th style={{ width: 130 }} />
            </tr>
          </thead>
          <tbody>
            {streams.map((stream) => (
              <StreamRows
                key={stream.id}
                stream={stream}
                busy={busy}
                campaignId={campaignId}
                statsInterval={statsInterval}
                onRun={run}
              />
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}

type RunFn = (action: () => Promise<CampaignStreams>, successMessage?: string) => Promise<void>

interface StreamRowsProps {
  stream: Stream
  busy: boolean
  campaignId: string
  statsInterval: string
  onRun: RunFn
}

function StreamRows({ stream, busy, campaignId, statsInterval, onRun }: StreamRowsProps) {
  const [offerToAdd, setOfferToAdd] = useState<Offer | null>(null)

  async function add() {
    if (!offerToAdd) return
    await onRun(() => api.addOffer(campaignId, stream.id, offerToAdd.id, statsInterval))
    setOfferToAdd(null)
  }

  return (
    <>
      <tr className="stream-head" data-testid={`stream-${stream.keitaro_id}`}>
        <td colSpan={8}>
          {stream.name}
          <span className="meta">
            #{stream.keitaro_id} · {stream.schema_type} · {stream.filters_summary} ·{' '}
            {stream.stats.clicks} click(s)
            {stream.action_payload ? ` · → ${stream.action_payload}` : ''}
          </span>
          {stream.accepts_offers && stream.share_total !== 100 && stream.offers.length > 0 && (
            <span className="badge removed" data-testid={`share-total-${stream.keitaro_id}`}>
              shares total {stream.share_total}% — must be 100% to push
            </span>
          )}
        </td>
      </tr>

      {stream.offers.map((offer) => (
        <OfferRow
          key={offer.offer_id}
          offer={offer}
          streamId={stream.id}
          campaignId={campaignId}
          statsInterval={statsInterval}
          busy={busy}
          onRun={onRun}
        />
      ))}

      {stream.accepts_offers ? (
        <tr>
          <td colSpan={7}>
            <OfferPicker
              value={offerToAdd}
              onChange={setOfferToAdd}
              disabled={busy}
              testId={`add-offer-${stream.keitaro_id}`}
              placeholder="Add an offer to this stream…"
            />
          </td>
          <td className="num">
            <button
              className="primary sm"
              data-testid={`add-btn-${stream.keitaro_id}`}
              disabled={!offerToAdd || busy}
              onClick={add}
            >
              + Add
            </button>
          </td>
        </tr>
      ) : (
        stream.offers.length === 0 && (
          <tr>
            <td colSpan={8} className="muted" style={{ fontStyle: 'italic' }}>
              This is a “{stream.schema_type}” stream — it redirects instead of holding offers.
            </td>
          </tr>
        )
      )}
    </>
  )
}

interface OfferRowProps {
  offer: StreamOffer
  streamId: string
  campaignId: string
  statsInterval: string
  busy: boolean
  onRun: RunFn
}

function OfferRow({ offer, streamId, campaignId, statsInterval, busy, onRun }: OfferRowProps) {
  const [draftShare, setDraftShare] = useState(String(offer.share))

  useEffect(() => {
    setDraftShare(String(offer.share))
  }, [offer.share])

  function commitShare() {
    const parsed = Number(draftShare)
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100 || parsed === offer.share) {
      setDraftShare(String(offer.share))
      return
    }
    void onRun(() =>
      api.patchOffer(campaignId, streamId, offer.offer_id, { share: parsed }, statsInterval),
    )
  }

  const rowClass = [offer.status !== 'synced' ? 'dirty' : '', offer.removed ? 'removed' : '']
    .filter(Boolean)
    .join(' ')

  return (
    <tr className={rowClass} data-testid={`offer-row-${offer.offer_id}`}>
      <td>
        <span className="offer-name">{offer.offer_name ?? `Offer ${offer.offer_id}`}</span>
        {offer.status !== 'synced' && (
          <span className={`badge ${offer.status}`} data-testid={`badge-${offer.offer_id}`}>
            {offer.status}
          </span>
        )}
        <div className="offer-sub">
          #{offer.offer_id}
          {offer.kt_share !== null && offer.kt_share !== offer.share
            ? ` · in Keitaro: ${offer.kt_share}%`
            : ''}
        </div>
      </td>
      <td className="num">
        <div className="share-cell">
          <input
            className="share-input"
            data-testid={`share-${offer.offer_id}`}
            value={draftShare}
            disabled={busy || offer.removed}
            onChange={(event) => setDraftShare(event.target.value)}
            onBlur={commitShare}
            onKeyDown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
          />
          <span className="muted">%</span>
          <button
            className={`pin${offer.pinned ? ' on' : ''}`}
            data-testid={`pin-${offer.offer_id}`}
            title={offer.pinned ? 'Unpin: let this share rebalance' : 'Pin: hold this share'}
            disabled={busy || offer.removed}
            onClick={() =>
              void onRun(() =>
                api.patchOffer(
                  campaignId,
                  streamId,
                  offer.offer_id,
                  { pinned: !offer.pinned },
                  statsInterval,
                ),
              )
            }
          >
            📌
          </button>
        </div>
      </td>
      <td className="num">{offer.stats.clicks}</td>
      <td className="num">{offer.stats.unique_clicks}</td>
      <td className="num">{offer.stats.bots}</td>
      <td className="num">{offer.stats.conversions}</td>
      <td className="num">{offer.stats.revenue.toFixed(2)}</td>
      <td className="num">
        {offer.removed ? (
          <button
            className="sm"
            data-testid={`restore-${offer.offer_id}`}
            disabled={busy}
            onClick={() =>
              void onRun(() =>
                api.restoreOffer(campaignId, streamId, offer.offer_id, statsInterval),
              )
            }
          >
            ↺ Bring back
          </button>
        ) : (
          <button
            className="sm danger"
            data-testid={`remove-${offer.offer_id}`}
            disabled={busy}
            onClick={() =>
              void onRun(() =>
                api.removeOffer(campaignId, streamId, offer.offer_id, statsInterval),
              )
            }
          >
            🗑 Remove
          </button>
        )}
      </td>
    </tr>
  )
}
