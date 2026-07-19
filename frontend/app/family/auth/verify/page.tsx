"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function VerifyContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const hasError = searchParams.get("error") !== null;

  useEffect(() => {
    if (!token || hasError) return;
    // Full browser navigation (not fetch) — the backend's Set-Cookie needs
    // to land on its own origin, which only works for a top-level
    // navigation, not a cross-origin fetch(). See backend/api/routes/auth.py.
    window.location.href = `${API_BASE}/auth/verify?token=${encodeURIComponent(token)}`;
  }, [token, hasError]);

  if (hasError || !token) {
    return (
      <div className="text-center">
        <p className="text-lg text-[#2C2C2C]">
          This link has expired. Request a new one.
        </p>
        <a
          href="/family/login"
          className="mt-4 inline-block text-[#C8956C] underline"
        >
          Back to login
        </a>
      </div>
    );
  }

  return <p className="text-lg text-[#2C2C2C]">Logging you in...</p>;
}

export default function VerifyMagicLinkPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#FDF6EC] p-8">
      <Suspense
        fallback={<p className="text-lg text-[#2C2C2C]">Logging you in...</p>}
      >
        <VerifyContent />
      </Suspense>
    </main>
  );
}
