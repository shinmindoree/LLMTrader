import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center">
      <div className="max-w-md">
        <h1 className="text-6xl font-bold text-[#2962ff] mb-4">404</h1>
        <h2 className="text-xl font-semibold text-[#d1d4dc] mb-3">
          Page Not Found
        </h2>
        <p className="text-sm text-[#868993] mb-8">
          The page you are looking for does not exist or has been moved.
        </p>
        <Link
          href="/dashboard"
          className="inline-block rounded bg-[#2962ff] px-6 py-2.5 text-sm font-medium text-white hover:bg-[#1e53e5] transition-colors"
        >
          Go to Dashboard
        </Link>
      </div>
    </div>
  );
}
