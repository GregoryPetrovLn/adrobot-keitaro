import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Offer } from '../api/types'

interface Props {
  value: Offer | null
  onChange: (offer: Offer | null) => void
  placeholder?: string
  disabled?: boolean
  testId?: string
}

/** Debounced autocomplete over the locally cached Keitaro offer catalogue. */
export function OfferPicker({ value, onChange, placeholder, disabled, testId }: Props) {
  const [query, setQuery] = useState('')
  const [options, setOptions] = useState<Offer[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    const timer = window.setTimeout(async () => {
      try {
        const found = await api.searchOffers(query)
        if (!cancelled) {
          setOptions(found)
          setHighlight(0)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query, open])

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  function choose(offer: Offer) {
    onChange(offer)
    setQuery('')
    setOpen(false)
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlight((h) => Math.min(h + 1, options.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (event.key === 'Enter' && options[highlight]) {
      event.preventDefault()
      choose(options[highlight])
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  const display = value ? `[${value.id}] ${value.name}` : query

  return (
    <div className="autocomplete" ref={boxRef}>
      <input
        data-testid={testId}
        value={display}
        placeholder={placeholder ?? 'Search offers by name or id…'}
        disabled={disabled}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        onChange={(event) => {
          if (value) onChange(null)
          setQuery(event.target.value)
          setOpen(true)
        }}
      />
      {open && (
        <div className="options" role="listbox">
          {loading && <div className="empty"><span className="spinner" /> Searching…</div>}
          {!loading && options.length === 0 && <div className="empty">No offers found</div>}
          {!loading &&
            options.map((offer, index) => (
              <div
                key={offer.id}
                role="option"
                aria-selected={index === highlight}
                className={`option${index === highlight ? ' active' : ''}`}
                onMouseEnter={() => setHighlight(index)}
                onMouseDown={(event) => {
                  event.preventDefault()
                  choose(offer)
                }}
              >
                <div className="title">[{offer.id}] {offer.name}</div>
                <div className="sub">
                  {offer.group_name ?? 'no group'}
                  {offer.countries?.length ? ` · ${offer.countries.join(', ')}` : ''}
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}
