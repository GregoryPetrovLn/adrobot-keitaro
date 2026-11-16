import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Campaign, Country, Dictionaries, KeitaroCampaignRef, Offer } from '../api/types'
import { Banner } from '../components/Banner'
import { OfferPicker } from '../components/OfferPicker'
import { errorMessage, formatDate } from '../lib/format'

export function CampaignsPage() {
  const [dictionaries, setDictionaries] = useState<Dictionaries | null>(null)
  const [countries, setCountries] = useState<Country[]>([])
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [geo, setGeo] = useState('')
  const [offer, setOffer] = useState<Offer | null>(null)
  const [groupId, setGroupId] = useState<number | ''>('')
  const [sourceId, setSourceId] = useState<number | ''>('')
  const [domainId, setDomainId] = useState<number | ''>('')
  const [newGroupName, setNewGroupName] = useState('')

  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [created, setCreated] = useState<Campaign | null>(null)

  const [importQuery, setImportQuery] = useState('')
  const [importResults, setImportResults] = useState<KeitaroCampaignRef[]>([])
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)

  const [listError, setListError] = useState<string | null>(null)
  const [syncNotice, setSyncNotice] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)

  useEffect(() => {
    void (async () => {
      try {
        const [dict, countryList, existing] = await Promise.all([
          api.dictionaries(),
          api.countries(),
          api.listCampaigns(),
        ])
        setDictionaries(dict)
        setCountries(countryList)
        setCampaigns(existing)
        setGroupId(dict.defaults.group_id ?? dict.groups[0]?.id ?? '')
        setSourceId(dict.defaults.traffic_source_id ?? dict.traffic_sources[0]?.id ?? '')
        setDomainId(dict.defaults.domain_id ?? dict.domains[0]?.id ?? '')
      } catch (error) {
        setLoadError(errorMessage(error))
      }
    })()
  }, [])

  async function createCampaign(event: React.FormEvent) {
    event.preventDefault()
    setCreateError(null)
    setCreated(null)
    if (!offer) {
      setCreateError('Pick an offer for the second flow.')
      return
    }
    setCreating(true)
    try {
      const campaign = await api.createCampaign({
        name,
        geo,
        offer_id: offer.id,
        group_id: groupId === '' ? null : groupId,
        traffic_source_id: sourceId === '' ? null : sourceId,
        domain_id: domainId === '' ? null : domainId,
      })
      setCreated(campaign)
      setCampaigns((current) => [campaign, ...current])
      setName('')
      setGeo('')
      setOffer(null)
    } catch (error) {
      setCreateError(errorMessage(error))
    } finally {
      setCreating(false)
    }
  }

  async function createGroup() {
    if (!newGroupName.trim() || !dictionaries) return
    try {
      const group = await api.createGroup(newGroupName.trim())
      setDictionaries({ ...dictionaries, groups: [...dictionaries.groups, group] })
      setGroupId(group.id)
      setNewGroupName('')
    } catch (error) {
      setCreateError(errorMessage(error))
    }
  }

  async function searchKeitaro() {
    setImportError(null)
    setImportBusy(true)
    try {
      setImportResults(await api.listKeitaroCampaigns(importQuery))
    } catch (error) {
      setImportError(errorMessage(error))
    } finally {
      setImportBusy(false)
    }
  }

  async function importCampaign(keitaroId: number) {
    setImportError(null)
    try {
      const campaign = await api.importCampaign(keitaroId)
      setCampaigns((current) => [campaign, ...current])
      setImportResults((current) =>
        current.map((item) => (item.id === keitaroId ? { ...item, imported: true } : item)),
      )
    } catch (error) {
      setImportError(errorMessage(error))
    }
  }

  async function forget(id: string) {
    setListError(null)
    try {
      await api.forgetCampaign(id)
      setCampaigns((current) => current.filter((campaign) => campaign.id !== id))
    } catch (error) {
      setListError(errorMessage(error))
    }
  }

  async function refreshOfferCatalogue() {
    setListError(null)
    setSyncing(true)
    try {
      const result = await api.syncOffers()
      setSyncNotice(`Offer catalogue refreshed: ${result.synced} offers available.`)
    } catch (error) {
      setListError(errorMessage(error))
    } finally {
      setSyncing(false)
    }
  }

  if (loadError) {
    return (
      <div className="page">
        <Banner kind="error">
          Could not load Keitaro reference data: {loadError}
          <div className="muted" style={{ marginTop: 6, fontWeight: 400 }}>
            Check <code>KEITARO_BASE_URL</code> and <code>KEITARO_API_KEY</code> in your{' '}
            <code>.env</code>.
          </div>
        </Banner>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="grid-2">
        <section className="card">
          <h2>New campaign</h2>
          <p className="hint">
            Creates a Keitaro campaign with two flows: everyone from the chosen country goes to
            Google, everyone else goes to the chosen offer.
          </p>

          {createError && <Banner kind="error">{createError}</Banner>}
          {created && (
            <Banner kind="ok">
              Created <b>{created.name}</b> (Keitaro&nbsp;#{created.keitaro_id}).{' '}
              <Link to={`/campaigns/${created.id}`}>Open the editor →</Link>
            </Banner>
          )}

          <form onSubmit={createCampaign}>
            <div className="field">
              <label htmlFor="campaign-name">Name</label>
              <input
                id="campaign-name"
                data-testid="campaign-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="my campaign"
                required
              />
            </div>

            <div className="field">
              <label htmlFor="campaign-geo">Geo</label>
              <select
                id="campaign-geo"
                data-testid="campaign-geo"
                value={geo}
                onChange={(event) => setGeo(event.target.value)}
                required
              >
                <option value="">Select a country…</option>
                {countries.map((country) => (
                  <option key={country.code} value={country.code}>
                    {country.code} — {country.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label>Offer</label>
              <OfferPicker value={offer} onChange={setOffer} testId="campaign-offer" />
            </div>

            <div className="field">
              <label htmlFor="campaign-group">Group</label>
              <div className="field-row">
                <select
                  id="campaign-group"
                  data-testid="campaign-group"
                  value={groupId}
                  onChange={(event) => setGroupId(Number(event.target.value))}
                >
                  {dictionaries?.groups.map((group) => (
                    <option key={group.id} value={group.id}>
                      {group.name}
                    </option>
                  ))}
                </select>
                <input
                  value={newGroupName}
                  onChange={(event) => setNewGroupName(event.target.value)}
                  placeholder="or create a new group…"
                />
                <button type="button" onClick={createGroup} disabled={!newGroupName.trim()}>
                  Create
                </button>
              </div>
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="campaign-source">Traffic source</label>
                <select
                  id="campaign-source"
                  data-testid="campaign-source"
                  value={sourceId}
                  onChange={(event) => setSourceId(Number(event.target.value))}
                >
                  {dictionaries?.traffic_sources.map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="campaign-domain">Domain</label>
                <select
                  id="campaign-domain"
                  data-testid="campaign-domain"
                  value={domainId}
                  onChange={(event) => setDomainId(Number(event.target.value))}
                >
                  {dictionaries?.domains.map((domain) => (
                    <option key={domain.id} value={domain.id}>
                      {domain.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <button className="primary" type="submit" data-testid="campaign-create" disabled={creating}>
              {creating ? <><span className="spinner" /> Creating…</> : 'Create campaign'}
            </button>
          </form>
        </section>

        <section className="card">
          <h2>Import an existing campaign</h2>
          <p className="hint">
            Already have a campaign in Keitaro? Pull it in to edit its offers here.
          </p>
          {importError && <Banner kind="error">{importError}</Banner>}
          <div className="field-row" style={{ marginBottom: 14 }}>
            <input
              data-testid="import-search"
              value={importQuery}
              onChange={(event) => setImportQuery(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && searchKeitaro()}
              placeholder="Search Keitaro campaigns by name or id…"
            />
            <button onClick={searchKeitaro} disabled={importBusy} data-testid="import-search-btn">
              {importBusy ? <span className="spinner" /> : 'Search'}
            </button>
          </div>
          {importResults.length > 0 && (
            <table>
              <tbody>
                {importResults.slice(0, 15).map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="offer-name">{item.name}</div>
                      <div className="offer-sub">
                        #{item.id}
                        {item.group ? ` · ${item.group}` : ''}
                      </div>
                    </td>
                    <td className="num" style={{ width: 110 }}>
                      {item.imported ? (
                        <span className="muted">imported</span>
                      ) : (
                        <button className="sm" onClick={() => importCampaign(item.id)}>
                          Import
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <section className="card">
        <div className="toolbar">
          <h2 style={{ margin: 0 }}>Managed campaigns</h2>
          <div className="spacer" />
          <button
            className="sm"
            data-testid="sync-offers"
            disabled={syncing}
            title="Re-read the offer catalogue from Keitaro so new offers appear in the pickers"
            onClick={refreshOfferCatalogue}
          >
            {syncing ? <span className="spinner" /> : '⟳ Refresh offer catalogue'}
          </button>
        </div>
        {listError && <Banner kind="error">{listError}</Banner>}
        {syncNotice && !listError && <Banner kind="ok">{syncNotice}</Banner>}
        {campaigns.length === 0 ? (
          <div className="empty-state">
            No campaigns yet. Create one above, or import an existing one from Keitaro.
          </div>
        ) : (
          <table data-testid="campaign-table">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Geo</th>
                <th>Group</th>
                <th>Source</th>
                <th>Domain</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {campaigns.map((campaign) => (
                <tr key={campaign.id}>
                  <td>
                    <Link to={`/campaigns/${campaign.id}`} className="offer-name">
                      {campaign.name}
                    </Link>
                    <div className="offer-sub">
                      Keitaro #{campaign.keitaro_id}
                      {campaign.created_by_app ? ' · created here' : ' · imported'}
                    </div>
                  </td>
                  <td>{campaign.geo ?? '—'}</td>
                  <td>{campaign.group_name ?? '—'}</td>
                  <td>{campaign.traffic_source_name ?? '—'}</td>
                  <td>{campaign.domain_name ?? '—'}</td>
                  <td className="muted">{formatDate(campaign.created_at)}</td>
                  <td className="num">
                    <div className="row-gap">
                      <Link to={`/campaigns/${campaign.id}`}>
                        <button className="sm">Edit offers</button>
                      </Link>
                      <button className="sm ghost" title="Stop managing locally; Keitaro is untouched" onClick={() => forget(campaign.id)}>
                        Forget
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
