import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Offer } from '../api/types'
import { errorMessage } from '../lib/format'

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
  const [error, setError] = useState<string | null>(null)
  const [highlight, setHighlight] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    const timer = window.setTimeout(() => {
      api
        .searchOffers(query)
        .then((found) => {
          if (cancelled) return
          setOptions(found)
          setError(null)
          setHighlight(0)
        })
        // The search hits Keitaro through the cache, so a bad key or a flaky tracker
        // lands here. Saying so beats an indistinguishable "No offers found".
        .catch((caught: unknown) => {
          if (cancelled) return
          setOptions([])
          setError(errorMessage(caught))
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
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
      <div className="picker-input">
        <input
          data-testid={testId}
          value={display}
          placeholder={placeholder ?? 'Search offers by name or id…'}
          disabled={disabled}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          onChange={(event) => {
            // While an offer is selected the input shows "[id] name". Typing must start a
            // fresh search from the new character, not append to that display string.
            if (value) {
              onChange(null)
              setQuery(event.nativeEvent instanceof InputEvent ? event.nativeEvent.data ?? '' : '')
            } else {
              setQuery(event.target.value)
            }
            setOpen(true)
          }}
        />
        {value && (
          <button
            type="button"
            className="picker-clear"
            aria-label="Clear the selected offer"
            data-testid={testId ? `${testId}-clear` : undefined}
            disabled={disabled}
            onClick={() => {
              onChange(null)
              setQuery('')
              setOpen(true)
            }}
          >
            ×
          </button>
        )}
      </div>
      {open && (
        <div className="options" role="listbox">
          {loading && (
            <div className="empty">
              <span className="spinner" /> Searching…
            </div>
          )}
          {!loading && error && <div className="empty picker-error">{error}</div>}
          {!loading && !error && options.length === 0 && <div className="empty">No offers found</div>}
          {!loading &&
            !error &&
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
                <div className="title">
                  [{offer.id}] {offer.name}
                </div>
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
