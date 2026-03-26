"use client";

import { useEffect, useRef, useCallback } from "react";

export function FormModal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const stableOnClose = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) {
      el.showModal();
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const handler = () => stableOnClose();
    el.addEventListener("close", handler);
    return () => el.removeEventListener("close", handler);
  }, [stableOnClose]);

  const onBackdropClick = (e: React.MouseEvent<HTMLDialogElement>) => {
    if (e.target === dialogRef.current) {
      onClose();
    }
  };

  return (
    <dialog
      ref={dialogRef}
      onClick={onBackdropClick}
      className="backdrop:bg-black/60 backdrop:backdrop-blur-sm bg-transparent p-0 max-w-none w-full open:flex items-start justify-center"
    >
      <div className="w-full max-w-[560px] mx-auto my-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] shadow-2xl animate-[modal-in_150ms_ease-out]">
        <div className="flex items-center justify-between border-b border-[#2a2e39] px-5 py-3.5">
          <h2 className="text-sm font-medium text-[#d1d4dc]">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-[#868993] hover:bg-[#2a2e39] hover:text-[#d1d4dc] transition-colors"
            aria-label="Close"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
        <div className="px-5 py-4 max-h-[calc(100vh-8rem)] overflow-y-auto">
          {children}
        </div>
      </div>
    </dialog>
  );
}
