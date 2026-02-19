export function PageLoadingSpinner() {
  return (
    <div className="flex min-h-[40vh] w-full items-center justify-center px-6 py-10">
      <div className="flex flex-col items-center gap-5">
        <div className="page-load-spinner" aria-hidden />
        <div className="flex gap-1.5">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="page-load-dot"
              style={{ animationDelay: `${i * 0.15}s` }}
              aria-hidden
            />
          ))}
        </div>
      </div>
    </div>
  );
}
