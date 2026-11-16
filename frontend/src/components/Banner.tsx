interface Props {
  kind: 'error' | 'ok' | 'info'
  children: React.ReactNode
}

export function Banner({ kind, children }: Props) {
  return (
    <div className={`banner ${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  )
}
