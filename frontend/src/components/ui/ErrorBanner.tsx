export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="bg-red-900/40 border border-red-500/50 text-red-300 rounded-lg p-3 text-sm">
      {message}
    </div>
  )
}
