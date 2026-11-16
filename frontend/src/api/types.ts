export interface NamedRef { id: number; name: string }
export interface DomainRef { id: number; name: string; url: string }
export interface DictionaryDefaults {
  group_id: number | null
  traffic_source_id: number | null
  domain_id: number | null
}
export interface Dictionaries {
  groups: NamedRef[]
  traffic_sources: NamedRef[]
  domains: DomainRef[]
  defaults: DictionaryDefaults
}
export interface Country { code: string; name: string }

export interface Offer {
  id: number
  name: string
  group_name: string | null
  countries: string[] | null
  payout_value: number | null
  payout_currency: string | null
}

export interface Campaign {
  id: string
  keitaro_id: number
  name: string
  alias: string | null
  geo: string | null
  group_name: string | null
  traffic_source_name: string | null
  domain_name: string | null
  campaign_url: string | null
  created_by_app: boolean
  streams_fetched_at: string | null
  created_at: string
}

export interface KeitaroCampaignRef {
  id: number
  name: string
  group: string | null
  state: string | null
  imported: boolean
}

export interface Stats {
  clicks: number
  unique_clicks: number
  bots: number
  conversions: number
  leads: number
  sales: number
  revenue: number
}

export type OfferStatus = 'synced' | 'added' | 'removed' | 'changed'

export interface StreamOffer {
  offer_id: number
  offer_name: string | null
  share: number
  kt_share: number | null
  pinned: boolean
  removed: boolean
  status: OfferStatus
  position: number
  stats: Stats
}

export interface Stream {
  id: string
  keitaro_id: number
  name: string
  schema_type: string
  action_payload: string | null
  position: number
  state: string
  filters_summary: string
  accepts_offers: boolean
  offers: StreamOffer[]
  stats: Stats
}

export interface CampaignStreams {
  campaign: Campaign
  streams: Stream[]
  has_pending_changes: boolean
  interval: string
  keitaro_url: string
}

export interface PushResult {
  pushed_streams: number
  applied: string[]
  campaign: Campaign
}

export interface ApiErrorBody {
  code?: string
  message?: string
  details?: unknown
  detail?: unknown
}
