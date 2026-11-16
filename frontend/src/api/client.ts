import type {
  Campaign,
  CampaignStreams,
  Country,
  Dictionaries,
  KeitaroCampaignRef,
  NamedRef,
  Offer,
  PushResult,
  ApiErrorBody,
} from './types'

const BASE = '/api'

export class ApiError extends Error {
  readonly status: number
  readonly details: unknown

  constructor(message: string, status: number, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

/** Turn FastAPI's two error shapes (ours and pydantic's) into one readable message. */
function describe(body: ApiErrorBody | null, status: number): string {
  if (!body) return `Request failed with HTTP ${status}`
  if (typeof body.message === 'string') return body.message
  if (Array.isArray(body.detail)) {
    const parts = body.detail
      .map((item: unknown) => {
        if (item && typeof item === 'object' && 'msg' in item) {
          const entry = item as { msg: string; loc?: unknown[] }
          const field = Array.isArray(entry.loc) ? entry.loc.slice(1).join('.') : ''
          return field ? `${field}: ${entry.msg}` : entry.msg
        }
        return String(item)
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  if (typeof body.detail === 'string') return body.detail
  return `Request failed with HTTP ${status}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }

  if (!response.ok) {
    const parsed = (body ?? null) as ApiErrorBody | null
    throw new ApiError(describe(parsed, response.status), response.status, parsed?.details)
  }
  return body as T
}

export const api = {
  dictionaries: () => request<Dictionaries>('/dictionaries'),
  createGroup: (name: string) =>
    request<NamedRef>('/dictionaries/groups', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  countries: () => request<Country[]>('/countries'),

  searchOffers: (q: string, limit = 25) =>
    request<Offer[]>(`/offers?q=${encodeURIComponent(q)}&limit=${limit}`),
  syncOffers: () => request<{ synced: number; synced_at: string }>('/offers/sync', { method: 'POST' }),

  listCampaigns: () => request<Campaign[]>('/campaigns'),
  createCampaign: (payload: {
    name: string
    geo: string
    offer_id: number
    group_id?: number | null
    traffic_source_id?: number | null
    domain_id?: number | null
  }) => request<Campaign>('/campaigns', { method: 'POST', body: JSON.stringify(payload) }),
  listKeitaroCampaigns: (q: string) =>
    request<KeitaroCampaignRef[]>(`/campaigns/keitaro?q=${encodeURIComponent(q)}`),
  importCampaign: (keitaroId: number) =>
    request<Campaign>('/campaigns/import', {
      method: 'POST',
      body: JSON.stringify({ keitaro_id: keitaroId }),
    }),
  forgetCampaign: (id: string) => request<void>(`/campaigns/${id}`, { method: 'DELETE' }),

  streams: (id: string, interval: string) =>
    request<CampaignStreams>(`/campaigns/${id}/streams?interval=${interval}`),
  fetchStreams: (id: string, interval: string) =>
    request<CampaignStreams>(`/campaigns/${id}/fetch?interval=${interval}`, { method: 'POST' }),
  // Every editing call carries the stats period, otherwise the response would come back
  // with today's numbers while the picker still says "Last 30 days".
  addOffer: (id: string, streamId: string, offerId: number, interval: string) =>
    request<CampaignStreams>(
      `/campaigns/${id}/streams/${streamId}/offers?interval=${interval}`,
      { method: 'POST', body: JSON.stringify({ offer_id: offerId }) },
    ),
  removeOffer: (id: string, streamId: string, offerId: number, interval: string) =>
    request<CampaignStreams>(
      `/campaigns/${id}/streams/${streamId}/offers/${offerId}?interval=${interval}`,
      { method: 'DELETE' },
    ),
  restoreOffer: (id: string, streamId: string, offerId: number, interval: string) =>
    request<CampaignStreams>(
      `/campaigns/${id}/streams/${streamId}/offers/${offerId}/restore?interval=${interval}`,
      { method: 'POST' },
    ),
  patchOffer: (
    id: string,
    streamId: string,
    offerId: number,
    patch: { share?: number; pinned?: boolean },
    interval: string,
  ) =>
    request<CampaignStreams>(
      `/campaigns/${id}/streams/${streamId}/offers/${offerId}?interval=${interval}`,
      { method: 'PATCH', body: JSON.stringify(patch) },
    ),
  push: (id: string) => request<PushResult>(`/campaigns/${id}/push`, { method: 'POST' }),
  reset: (id: string, interval: string) =>
    request<CampaignStreams>(`/campaigns/${id}/reset?interval=${interval}`, { method: 'POST' }),
}
